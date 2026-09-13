// Web Push (PWA/TWA notifications). Registers the push-only service worker and,
// when enabled server-side, subscribes the browser and stores the subscription
// against the device + session so the backend can notify (e.g. gold quote ready).

const DEVICE_ID_STORAGE_KEY = 'pricehunter-device-id';

function getDeviceId() {
  let id = window.localStorage.getItem(DEVICE_ID_STORAGE_KEY);
  if (!id) {
    id =
      window.crypto?.randomUUID?.() ||
      `device-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    window.localStorage.setItem(DEVICE_ID_STORAGE_KEY, id);
  }
  return id;
}

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = window.atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}

const pushSupported = () =>
  typeof window !== 'undefined' &&
  'serviceWorker' in navigator &&
  'PushManager' in window &&
  'Notification' in window;

let swRegistration = null;

export async function registerServiceWorker() {
  if (!('serviceWorker' in navigator)) return null;
  try {
    swRegistration = await navigator.serviceWorker.register('/sw.js');
    return swRegistration;
  } catch (err) {
    console.warn('SW registration failed', err);
    return null;
  }
}

// Best-effort subscribe. No-op if push is disabled server-side, unsupported, or
// the user has already denied permission. `promptIfDefault` gates whether we ask
// for permission (only do so right after a meaningful action, e.g. a request).
export async function ensurePushSubscribed(
  apiBaseUrl,
  sessionId,
  { promptIfDefault = false } = {},
) {
  if (!pushSupported()) return false;
  if (Notification.permission === 'denied') return false;
  if (Notification.permission === 'default' && !promptIfDefault) return false;

  try {
    const cfgRes = await fetch(`${apiBaseUrl}/api/push/config`);
    const cfg = await cfgRes.json().catch(() => ({}));
    if (!cfg.enabled || !cfg.vapid_public_key) return false;

    if (Notification.permission === 'default') {
      const perm = await Notification.requestPermission();
      if (perm !== 'granted') return false;
    }

    const reg = swRegistration || (await navigator.serviceWorker.ready);
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(cfg.vapid_public_key),
      });
    }

    await fetch(`${apiBaseUrl}/api/push/subscribe`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Device-Id': getDeviceId(),
      },
      body: JSON.stringify({
        subscription: sub.toJSON(),
        session_id: sessionId || null,
      }),
    });
    return true;
  } catch (err) {
    console.warn('push subscribe failed', err);
    return false;
  }
}

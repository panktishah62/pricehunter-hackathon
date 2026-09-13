import * as Sentry from '@sentry/react';

const sentryDsn = import.meta.env.VITE_SENTRY_DSN;
const sentryEnvironment =
  import.meta.env.VITE_SENTRY_ENVIRONMENT || import.meta.env.MODE;
const sentryRelease = import.meta.env.VITE_SENTRY_RELEASE || undefined;
const apiBaseUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const SENSITIVE_KEY_PARTS = [
  'authorization',
  'cookie',
  'password',
  'secret',
  'token',
  'api_key',
  'apikey',
  'phone',
  'mobile',
  'wa_id',
  'whatsapp',
  'recording',
  'transcript',
];

function parseSampleRate(value, fallback) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.min(1, Math.max(0, parsed));
}

function redactText(value) {
  return String(value)
    .replace(/(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)/g, '[Filtered]')
    .replace(/[\w.+-]+@[\w-]+\.[\w.-]+/g, '[Filtered]');
}

function isSensitiveKey(key) {
  const normalized = String(key).toLowerCase().replaceAll('-', '_');
  return SENSITIVE_KEY_PARTS.some((part) => normalized.includes(part));
}

function scrub(value) {
  if (Array.isArray(value)) {
    return value.map((item) => scrub(item));
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        isSensitiveKey(key) ? '[Filtered]' : scrub(item),
      ]),
    );
  }
  if (typeof value === 'string') {
    return redactText(value);
  }
  return value;
}

function stripUrlQuery(value) {
  try {
    const url = new URL(value);
    url.search = '';
    return url.toString();
  } catch {
    return redactText(value);
  }
}

function buildTracePropagationTargets() {
  const targets = ['localhost'];
  try {
    targets.push(new URL(apiBaseUrl).origin);
  } catch {
    // Ignore invalid build-time API URLs; Sentry still captures frontend errors.
  }
  return [...new Set(targets)];
}

function beforeSend(event) {
  if (event.request) {
    if (typeof event.request.url === 'string') {
      event.request.url = stripUrlQuery(event.request.url);
    }
    event.request.headers = scrub(event.request.headers || {});
    event.request.cookies = '[Filtered]';
    event.request.query_string = '[Filtered]';
    if (event.request.data) {
      event.request.data = '[Filtered]';
    }
  }
  if (event.user) {
    event.user = scrub(event.user);
  }
  if (event.extra) {
    event.extra = scrub(event.extra);
  }
  if (event.contexts) {
    event.contexts = scrub(event.contexts);
  }
  if (event.breadcrumbs) {
    event.breadcrumbs = scrub(event.breadcrumbs);
  }
  if (event.exception && Array.isArray(event.exception.values)) {
    for (const exc of event.exception.values) {
      if (typeof exc.value === 'string') {
        exc.value = redactText(exc.value);
      }
    }
  }
  return event;
}

if (sentryDsn) {
  Sentry.init({
    dsn: sentryDsn,
    environment: sentryEnvironment,
    release: sentryRelease,
    sendDefaultPii: false,
    integrations: [
      Sentry.browserTracingIntegration(),
      Sentry.replayIntegration({
        maskAllText: true,
        blockAllMedia: true,
      }),
    ],
    tracesSampleRate: parseSampleRate(
      import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE,
      0.1,
    ),
    tracePropagationTargets: buildTracePropagationTargets(),
    replaysSessionSampleRate: parseSampleRate(
      import.meta.env.VITE_SENTRY_REPLAYS_SESSION_SAMPLE_RATE,
      0,
    ),
    replaysOnErrorSampleRate: parseSampleRate(
      import.meta.env.VITE_SENTRY_REPLAYS_ON_ERROR_SAMPLE_RATE,
      1,
    ),
    beforeSend,
    maxBreadcrumbs: 50,
  });
  Sentry.setTag('service', 'pricehunter-web');
}

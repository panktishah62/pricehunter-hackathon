/** Public logo/icon URLs with deploy-scoped cache busting for CDN edges. */
const assetVersion = import.meta.env.VITE_SENTRY_RELEASE || 'dev';

function brandAsset(path) {
  return `${path}?v=${encodeURIComponent(assetVersion)}`;
}

export const BRAND_LOGO_192_URL = brandAsset('/favicon-192.png');
export const BRAND_LOGO_32_URL = brandAsset('/favicon-32.png');
export const BRAND_APPLE_TOUCH_ICON_URL = brandAsset('/apple-touch-icon.png');

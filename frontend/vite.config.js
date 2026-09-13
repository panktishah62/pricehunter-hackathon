import process from 'node:process'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { sentryVitePlugin } from '@sentry/vite-plugin'

const shouldUploadSentrySourceMaps = Boolean(
  process.env.SENTRY_AUTH_TOKEN && process.env.SENTRY_ORG && process.env.SENTRY_PROJECT,
)

// https://vite.dev/config/
export default defineConfig({
  build: {
    sourcemap: shouldUploadSentrySourceMaps ? 'hidden' : false,
  },
  plugins: [
    react(),
    shouldUploadSentrySourceMaps
      ? sentryVitePlugin({
          org: process.env.SENTRY_ORG,
          project: process.env.SENTRY_PROJECT,
          authToken: process.env.SENTRY_AUTH_TOKEN,
          release: process.env.VITE_SENTRY_RELEASE ? { name: process.env.VITE_SENTRY_RELEASE } : undefined,
        })
      : null,
  ].filter(Boolean),
  server: {
    host: '0.0.0.0',
    port: 5173,
  },
})

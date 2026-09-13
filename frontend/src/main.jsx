import './instrument'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import * as amplitude from '@amplitude/unified'
import * as Sentry from '@sentry/react'
import './index.css'
import App from './App.jsx'
import AppErrorFallback from './components/AppErrorFallback.jsx'
import { registerServiceWorker } from './lib/push'

// Register the push-only service worker (PWA/TWA notifications). Safe no-op
// where unsupported; it has no fetch handler so it can't affect page loads.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    registerServiceWorker()
  })
}

const amplitudeApiKey = import.meta.env.VITE_AMPLITUDE_API_KEY
const sessionReplaySampleRate = Number(import.meta.env.VITE_AMPLITUDE_SESSION_REPLAY_SAMPLE_RATE ?? 0)

if (amplitudeApiKey) {
  amplitude.initAll(amplitudeApiKey, {
    analytics: { autocapture: true },
    sessionReplay: { sampleRate: Number.isFinite(sessionReplaySampleRate) ? sessionReplaySampleRate : 0 },
  })
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Sentry.ErrorBoundary fallback={<AppErrorFallback />}>
      <App />
    </Sentry.ErrorBoundary>
  </StrictMode>,
)

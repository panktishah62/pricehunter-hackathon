import * as amplitude from '@amplitude/unified'

export function trackAmplitudeEvent(eventName, eventProperties = {}) {
  if (typeof window === 'undefined') {
    return
  }

  try {
    amplitude.track(eventName, eventProperties)
  } catch (error) {
    console.error('Amplitude event tracking failed', error)
  }
}

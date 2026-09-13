import { useEffect, useState } from 'react'

function LocationPrompt({
  isOpen,
  isResolving,
  error,
  initialValue,
  apiBaseUrl,
  onClose,
  onUseCurrentLocation,
  onConfirmManual,
}) {
  const [manualLocation, setManualLocation] = useState(initialValue)
  const [suggestions, setSuggestions] = useState([])
  const [isLoadingSuggestions, setIsLoadingSuggestions] = useState(false)
  const [showSuggestions, setShowSuggestions] = useState(false)

  useEffect(() => {
    setManualLocation(initialValue)
  }, [initialValue])

  useEffect(() => {
    if (!isOpen) {
      setSuggestions([])
      setIsLoadingSuggestions(false)
      setShowSuggestions(false)
      return undefined
    }

    const query = manualLocation.trim()
    if (query.length < 2) {
      setSuggestions([])
      setIsLoadingSuggestions(false)
      setShowSuggestions(false)
      return undefined
    }

    const controller = new AbortController()
    const timeoutId = window.setTimeout(async () => {
      try {
        setIsLoadingSuggestions(true)
        const response = await fetch(
          `${apiBaseUrl}/api/location/suggest?q=${encodeURIComponent(query)}`,
          { signal: controller.signal },
        )
        if (!response.ok) {
          throw new Error('Suggestion lookup failed')
        }
        const payload = await response.json()
        setSuggestions(payload.suggestions || [])
        setShowSuggestions(true)
      } catch (fetchError) {
        if (fetchError.name !== 'AbortError') {
          setSuggestions([])
          setShowSuggestions(false)
        }
      } finally {
        setIsLoadingSuggestions(false)
      }
    }, 220)

    return () => {
      controller.abort()
      window.clearTimeout(timeoutId)
    }
  }, [apiBaseUrl, isOpen, manualLocation])

  if (!isOpen) {
    return null
  }

  const handleSubmit = (event) => {
    event.preventDefault()
    if (!manualLocation.trim()) {
      return
    }
    onConfirmManual(manualLocation.trim())
  }

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/25 px-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl rounded-[2rem] border border-slate-200 bg-[#fcfcf9] p-6 shadow-[0_24px_90px_rgba(15,23,42,0.12)] sm:p-8"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="mt-3 text-3xl font-semibold tracking-tight text-slate-900">Where should Zwig search?</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 transition hover:border-slate-300 hover:bg-slate-50 hover:text-slate-700"
            aria-label="Close location prompt"
          >
            <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M5 5 15 15M15 5 5 15" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        <div className="mt-6 grid gap-4 sm:grid-cols-[minmax(0,1fr)_auto]">
          <button
            type="button"
            onClick={onUseCurrentLocation}
            disabled={isResolving}
            className="rounded-[1.5rem] border border-slate-200 bg-white px-5 py-4 text-left transition hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <p className="text-base font-semibold text-slate-900">
              {isResolving ? 'Detecting your location...' : 'Use my current location'}
            </p>
          </button>
        </div>

        <form onSubmit={handleSubmit} className="mt-5">
          <label className="block">
            <span className="mb-2 block text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">
              Enter manually
            </span>
            <div className="relative">
              <input
                value={manualLocation}
                onChange={(event) => {
                  const nextValue = event.target.value
                  setManualLocation(nextValue)
                  setShowSuggestions(nextValue.trim().length >= 2)
                }}
                onFocus={() => {
                  if (suggestions.length > 0) {
                    setShowSuggestions(true)
                  }
                }}
                onBlur={() => {
                  window.setTimeout(() => setShowSuggestions(false), 120)
                }}
                placeholder="Whitefield, Ahmedabad, 380015..."
                className="w-full rounded-[1.5rem] border border-slate-200 bg-white px-4 py-4 pr-20 text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-slate-400 focus:ring-2 focus:ring-slate-200"
                autoComplete="off"
              />
              {manualLocation ? (
                <button
                  type="button"
                  onClick={() => {
                    setManualLocation('')
                    setSuggestions([])
                    setShowSuggestions(false)
                  }}
                  className="absolute right-4 top-1/2 inline-flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 transition hover:border-slate-300 hover:bg-slate-50 hover:text-slate-700"
                  aria-label="Clear location"
                >
                  <svg viewBox="0 0 20 20" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.8">
                    <path d="M5 5 15 15M15 5 5 15" strokeLinecap="round" />
                  </svg>
                </button>
              ) : null}
              {isLoadingSuggestions ? (
                <div className="pointer-events-none absolute right-20 top-1/2 -translate-y-1/2 text-xs text-slate-400">
                  Searching...
                </div>
              ) : null}
              {showSuggestions && suggestions.length > 0 ? (
                <div className="absolute left-0 right-0 top-[calc(100%+0.5rem)] z-30 overflow-hidden rounded-[1.25rem] border border-slate-200 bg-white shadow-[0_18px_40px_rgba(15,23,42,0.10)]">
                  {suggestions.map((suggestion) => (
                    <button
                      key={suggestion.place_id}
                      type="button"
                      onMouseDown={(event) => {
                        event.preventDefault()
                        setManualLocation(suggestion.description)
                        setSuggestions([])
                        setShowSuggestions(false)
                      }}
                      className="block w-full border-b border-slate-100 px-4 py-3 text-left text-sm text-slate-700 transition last:border-b-0 hover:bg-slate-50"
                    >
                      {suggestion.description}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          </label>
          <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center">
            <button
              type="submit"
              className="rounded-full bg-slate-900 px-5 py-3 text-sm font-medium text-white transition hover:bg-slate-800"
            >
              Confirm area
            </button>
            <p className="text-sm text-slate-500">You can change this later from the sidebar at any time.</p>
          </div>
        </form>

        {error && (
          <div className="mt-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {error}
          </div>
        )}
      </div>
    </div>
  )
}

export default LocationPrompt

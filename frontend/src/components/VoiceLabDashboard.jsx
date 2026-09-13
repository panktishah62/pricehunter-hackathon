import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { formatPrice } from '../lib/format'
import LocationPrompt from './LocationPrompt'

const TOKEN_STORAGE_KEY = 'pricehunter-voice-lab-token'
const LOCATION_STORAGE_KEY = 'pricehunter-voice-lab-location'
const RECORDINGS_PAGE_SIZE = 20
const CAMPAIGN_SLOTS = [
  { id: 'availability', label: 'Availability' },
  { id: 'price', label: 'Price' },
  { id: 'warranty', label: 'Warranty' },
  { id: 'discount', label: 'Discount' },
]

function percent(value) {
  if (value === null || value === undefined) {
    return 'New'
  }
  return `${Math.round(Number(value) * 100)}%`
}

function formatDate(value) {
  if (!value) {
    return 'Never'
  }
  return new Intl.DateTimeFormat('en-IN', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Asia/Kolkata',
  }).format(new Date(value))
}

function signedMoney(value) {
  if (value === null || value === undefined) {
    return 'Pending'
  }
  const formatted = formatPrice(Math.abs(value))
  if (value > 0) {
    return `+${formatted}`
  }
  if (value < 0) {
    return `-${formatted}`
  }
  return formatted
}

function signedPercent(value) {
  if (value === null || value === undefined) {
    return 'Pending'
  }
  const sign = value > 0 ? '+' : ''
  return `${sign}${(Number(value) * 100).toFixed(2)}%`
}

function statusTone(status) {
  if (status === 'completed' || status === 'picked_up_no_quote' || status === 'picked_up') {
    return 'border-emerald-200 bg-emerald-50 text-emerald-800'
  }
  if (status === 'failed' || status === 'no_answer') {
    return 'border-rose-200 bg-rose-50 text-rose-800'
  }
  if (status === 'busy' || status === 'running') {
    return 'border-amber-200 bg-amber-50 text-amber-800'
  }
  return 'border-slate-200 bg-white text-slate-700'
}

function callStatusLabel(status, outcomeLabel) {
  const normalized = (outcomeLabel || '').trim()
  if (normalized && normalized !== 'pending') {
    if (normalized === 'no_answer') {
      return 'no answer'
    }
    return normalized.replace(/_/g, ' ')
  }
  if (status === 'busy' || status === 'running') {
    return 'calling'
  }
  if (status === 'no_answer') {
    return 'no answer'
  }
  return status || 'pending'
}

function authHeaders(token, json = false) {
  const headers = json ? { 'Content-Type': 'application/json' } : {}
  if (token?.trim()) {
    headers['X-Voice-Lab-Token'] = token.trim()
  }
  return headers
}

async function readApiPayload(response) {
  const text = await response.text()
  if (!text) {
    return {}
  }
  try {
    return JSON.parse(text)
  } catch (_err) {
    return { detail: text }
  }
}

function recordingUrl(apiBaseUrl, call, token) {
  if (!call?.recording_stream_url) {
    return null
  }
  const url = `${apiBaseUrl}${call.recording_stream_url}`
  if (!token?.trim()) {
    return url
  }
  return `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token.trim())}`
}

// Renders the captured slots + transcript panels for a call. Shared by the Lab
// and Recordings tabs so the markup (and any future fix, e.g. nested slot value
// handling) lives in one place.
function CallDetails({ call }) {
  if (!call) {
    return null
  }
  const slots = call.extracted_data
  const hasSlots = slots && typeof slots === 'object' && Object.keys(slots).length > 0
  return (
    <>
      {hasSlots ? (
        <div className="mt-4 rounded-lg border border-slate-200 bg-white p-3">
          <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-slate-500">Captured slots</p>
          <dl className="mt-2 grid gap-x-4 gap-y-1 sm:grid-cols-2">
            {Object.entries(slots)
              .filter(([, value]) => value !== null && value !== '' && value !== undefined)
              .map(([key, value]) => (
                <div key={key} className="flex gap-2 text-xs">
                  <dt className="shrink-0 font-bold text-slate-500">{key.replace(/_/g, ' ')}:</dt>
                  <dd className="min-w-0 break-words text-slate-800">{renderSlotValue(value)}</dd>
                </div>
              ))}
          </dl>
        </div>
      ) : null}
      {call.transcript ? (
        <pre className="mt-4 max-h-56 overflow-auto rounded-lg border border-slate-200 bg-slate-950 p-3 text-xs leading-relaxed text-slate-100">
          {call.transcript}
        </pre>
      ) : null}
    </>
  )
}

// Render a captured-slot value for display. Scalars print as-is; booleans as
// yes/no; nested objects/arrays are JSON-stringified instead of the useless
// "[object Object]" that String() would produce.
function renderSlotValue(value) {
  if (typeof value === 'boolean') {
    return value ? 'yes' : 'no'
  }
  if (value !== null && typeof value === 'object') {
    try {
      return JSON.stringify(value)
    } catch {
      return String(value)
    }
  }
  return String(value)
}

function defaultOpeningLine(product) {
  return `Hello, आपके पास ${product || 'this product'} available है क्या?`
}

function VoiceLabDashboard() {
  const isHostedDashboard = window.location.hostname === 'dashboard.zwig.in'
  const apiBaseUrl = isHostedDashboard ? '' : import.meta.env.VITE_API_URL || 'http://localhost:8000'
  const [token, setToken] = useState(() => window.localStorage.getItem(TOKEN_STORAGE_KEY) || '')
  const [requiresLogin, setRequiresLogin] = useState(false)
  const [loginPassword, setLoginPassword] = useState('')
  const [isLoggingIn, setIsLoggingIn] = useState(false)
  const [configReload, setConfigReload] = useState(0)
  const [config, setConfig] = useState(null)
  const [query, setQuery] = useState('24K gold coin 10g in Bengaluru')
  const [location, setLocation] = useState(() => window.localStorage.getItem(LOCATION_STORAGE_KEY) || '')
  const [isLocationPromptOpen, setIsLocationPromptOpen] = useState(false)
  const [locationError, setLocationError] = useState('')
  const [isResolvingLocation, setIsResolvingLocation] = useState(false)
  const [maxVendors, setMaxVendors] = useState(8)
  const [includeOnline, setIncludeOnline] = useState(true)
  const [providerId, setProviderId] = useState('pipecat')
  const [callMode, setCallMode] = useState('vendor')
  const [directPhone, setDirectPhone] = useState('')
  const [directName, setDirectName] = useState('')
  const [campaignPreset, setCampaignPreset] = useState('auto')
  const [campaignProductSpoken, setCampaignProductSpoken] = useState('')
  const [campaignRequestedQuantity, setCampaignRequestedQuantity] = useState('एक piece')
  const [campaignOpeningLine, setCampaignOpeningLine] = useState('')
  const [campaignSlots, setCampaignSlots] = useState({
    availability: true,
    price: true,
    warranty: true,
    discount: true,
  })
  const [campaignAdvancedOpen, setCampaignAdvancedOpen] = useState(false)
  const [campaignJson, setCampaignJson] = useState('')
  const [savedPresets, setSavedPresets] = useState([])
  const [selectedSavedPresetId, setSelectedSavedPresetId] = useState('')
  const [presetSaveBusy, setPresetSaveBusy] = useState(false)
  const [activeTab, setActiveTab] = useState('lab')
  // Gold fulfilment requests (human-in-the-loop)
  const [goldRequests, setGoldRequests] = useState([])
  const [goldRequestsBusy, setGoldRequestsBusy] = useState(false)
  const [selectedRequestId, setSelectedRequestId] = useState('')
  const [requestDetail, setRequestDetail] = useState(null)
  const [respSupplier, setRespSupplier] = useState('')
  const [respPrice, setRespPrice] = useState('')
  const [respNote, setRespNote] = useState('')
  const [respUrl, setRespUrl] = useState('')
  const [respBusy, setRespBusy] = useState(false)
  const [bulkCallBusy, setBulkCallBusy] = useState(false)
  const [bulkConcurrency, setBulkConcurrency] = useState(10)
  const [bulkRunCallIds, setBulkRunCallIds] = useState([])
  const [session, setSession] = useState(null)
  const [selectedVendorId, setSelectedVendorId] = useState('')
  const [latestCall, setLatestCall] = useState(null)
  const [recentCalls, setRecentCalls] = useState([])
  const [recentCallsLoading, setRecentCallsLoading] = useState(false)
  const [recentCallsHasMore, setRecentCallsHasMore] = useState(false)
  const [recentCallsLoadingMore, setRecentCallsLoadingMore] = useState(false)
  const recordingsSentinelRef = useRef(null)
  // Mirror of recentCalls.length read inside paging callbacks, so those
  // callbacks don't depend on recentCalls.length (which would change their
  // identity on every page load and reset the 5s poll interval).
  const recentCallsCountRef = useRef(0)
  const [isFinding, setIsFinding] = useState(false)
  const [isCalling, setIsCalling] = useState(false)
  const [error, setError] = useState('')

  const providerOptions = config?.provider_options || session?.provider_options || []
  const bulkMaxConcurrency = config?.bulk_max_concurrency || 10
  const enabledProviders = providerOptions.filter((provider) => provider.enabled)
  const vendors = session?.vendors || []
  const calls = session?.calls || []
  const activeCall = latestCall || calls[0] || null
  const activeOutcome = activeCall?.outcome || null
  const selectedVendor = vendors.find((vendor) => vendor.id === selectedVendorId)
  const effectiveToken = isHostedDashboard ? '' : token
  const directCallSelected = callMode === 'direct'
  // Both the Pipecat cascade and Gemini Live accept a per-call campaign config
  // (same dict shape, threaded through the agent's /start). Gate the config UI
  // and payload on either, so operators can tailor Gemini Live calls too.
  const campaignConfigSupported = providerId === 'pipecat' || providerId === 'gemini_live'
  const callIsPending = activeCall?.status === 'busy' || activeCall?.status === 'running'
  const campaignProduct = campaignProductSpoken.trim() || session?.query?.product || query.trim()
  const campaignSlotOrder = CAMPAIGN_SLOTS.filter((slot) => campaignSlots[slot.id]).map((slot) => slot.id)
  const campaignPreview = {
    product: campaignProduct,
    product_spoken: campaignProduct,
    requested_quantity: campaignRequestedQuantity.trim() || 'एक piece',
    goal: campaignPreset === 'gold' ? 'gold price discovery' : 'buying price discovery',
    opening_line: campaignOpeningLine.trim() || defaultOpeningLine(campaignProduct),
    opening_segments: [campaignOpeningLine.trim() || defaultOpeningLine(campaignProduct)],
    slot_order: campaignSlotOrder.length ? campaignSlotOrder : ['availability', 'price'],
  }
  const requestHeaders = (json = false) => {
    const headers = authHeaders(effectiveToken, json)
    if (isHostedDashboard) {
      headers['X-Voice-Lab-Dashboard'] = '1'
    }
    return headers
  }

  const sessionLatestCall = calls[0] || null
  const sessionLatestOutcome = sessionLatestCall?.outcome || null

  const stages = useMemo(() => {
    return [
      {
        id: 'query',
        label: 'Query',
        status: session ? 'completed' : isFinding ? 'running' : 'pending',
        detail: session?.query?.product || 'Ready',
      },
      {
        id: 'vendors',
        label: 'Vendors',
        status: session ? 'completed' : 'pending',
        detail: session ? `${vendors.length} found` : 'Waiting',
      },
      {
        id: 'call',
        label: 'Call',
        status: isCalling ? 'running' : sessionLatestCall ? sessionLatestCall.status : 'pending',
        detail: directCallSelected
          ? directPhone || 'Enter number'
          : selectedVendor?.name || sessionLatestCall?.vendor?.canonical_name || 'Select vendor',
      },
      {
        id: 'outcome',
        label: 'Outcome',
        status: sessionLatestOutcome?.label ? 'completed' : 'pending',
        detail: sessionLatestOutcome?.label || (sessionLatestCall?.status === 'busy' ? 'Calling' : 'Pending'),
      },
    ]
  }, [directCallSelected, directPhone, isCalling, isFinding, selectedVendor?.name, session, sessionLatestCall, sessionLatestOutcome, vendors.length])

  useEffect(() => {
    if (!isHostedDashboard) {
      window.localStorage.setItem(TOKEN_STORAGE_KEY, token)
    }
  }, [isHostedDashboard, token])

  useEffect(() => {
    let cancelled = false
    async function loadConfig() {
      try {
        const response = await fetch(`${apiBaseUrl}/api/voice-lab/config`, {
          headers: requestHeaders(),
          credentials: 'same-origin',
        })
        const payload = await readApiPayload(response)
        if (!response.ok) {
          if (response.status === 401 && String(payload.detail || '').toLowerCase().includes('login')) {
            setRequiresLogin(true)
            return
          }
          throw new Error(response.status === 401 ? 'Voice Lab proxy is not configured yet.' : payload.detail || 'Could not load Voice Lab config.')
        }
        if (cancelled) {
          return
        }
        setRequiresLogin(false)
        setConfig(payload)
        const enabledOptions = (payload.provider_options || []).filter((provider) => provider.enabled)
        const preferred = enabledOptions.find((provider) => provider.id === 'pipecat') || enabledOptions[0]
        if (preferred) {
          setProviderId((current) => (enabledOptions.some((provider) => provider.id === current) ? current : preferred.id))
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message)
        }
      }
    }
    loadConfig()
    return () => {
      cancelled = true
    }
  }, [apiBaseUrl, effectiveToken, configReload])

  const fetchCallsPage = useCallback(async (limit, offset) => {
    const url = new URL(`${apiBaseUrl}/api/voice-lab/calls`, window.location.origin)
    url.searchParams.set('limit', String(limit))
    url.searchParams.set('offset', String(offset))
    if (effectiveToken.trim()) {
      url.searchParams.set('token', effectiveToken.trim())
    }
    const response = await fetch(url.toString(), {
      headers: requestHeaders(),
      credentials: 'same-origin',
    })
    if (!response.ok) {
      return null
    }
    const payload = await readApiPayload(response)
    return {
      calls: Array.isArray(payload.calls) ? payload.calls : [],
      hasMore: Boolean(payload.has_more),
    }
  }, [apiBaseUrl, effectiveToken])

  const fetchGoldRequests = useCallback(async () => {
    setGoldRequestsBusy(true)
    try {
      const url = new URL(`${apiBaseUrl}/api/voice-lab/requests`, window.location.origin)
      if (effectiveToken.trim()) url.searchParams.set('token', effectiveToken.trim())
      const response = await fetch(url.toString(), { headers: requestHeaders(), credentials: 'same-origin' })
      if (!response.ok) return
      const payload = await readApiPayload(response)
      setGoldRequests(Array.isArray(payload.requests) ? payload.requests : [])
    } catch (err) {
      setError(err.message)
    } finally {
      setGoldRequestsBusy(false)
    }
  }, [apiBaseUrl, effectiveToken])

  const openGoldRequest = useCallback(async (requestId) => {
    setSelectedRequestId(requestId)
    setRequestDetail(null)
    try {
      const url = new URL(`${apiBaseUrl}/api/voice-lab/requests/${requestId}`, window.location.origin)
      if (effectiveToken.trim()) url.searchParams.set('token', effectiveToken.trim())
      const response = await fetch(url.toString(), { headers: requestHeaders(), credentials: 'same-origin' })
      if (!response.ok) return
      setRequestDetail(await readApiPayload(response))
    } catch (err) {
      setError(err.message)
    }
  }, [apiBaseUrl, effectiveToken])

  const submitGoldResponse = useCallback(async (event) => {
    event.preventDefault()
    if (!selectedRequestId || !respSupplier.trim()) return
    setRespBusy(true)
    try {
      const url = new URL(`${apiBaseUrl}/api/voice-lab/requests/${selectedRequestId}/responses`, window.location.origin)
      if (effectiveToken.trim()) url.searchParams.set('token', effectiveToken.trim())
      const response = await fetch(url.toString(), {
        method: 'POST',
        headers: requestHeaders(true),
        credentials: 'same-origin',
        body: JSON.stringify({
          supplier: respSupplier.trim(),
          price: respPrice.trim() || null,
          note: respNote.trim() || null,
          url: respUrl.trim() || null,
        }),
      })
      if (!response.ok) {
        setError('Failed to post supplier response.')
        return
      }
      const payload = await readApiPayload(response)
      setRequestDetail(payload.request || null)
      setRespSupplier('')
      setRespPrice('')
      setRespNote('')
      setRespUrl('')
      fetchGoldRequests()
    } catch (err) {
      setError(err.message)
    } finally {
      setRespBusy(false)
    }
  }, [apiBaseUrl, effectiveToken, selectedRequestId, respSupplier, respPrice, respNote, respUrl, fetchGoldRequests])

  useEffect(() => {
    if (activeTab !== 'requests') return undefined
    fetchGoldRequests()
    const intervalId = window.setInterval(fetchGoldRequests, 8000)
    return () => window.clearInterval(intervalId)
  }, [activeTab, fetchGoldRequests])

  // Refresh the currently-loaded window from the top without collapsing how far
  // the user has scrolled. Re-fetches `max(loaded, pageSize)` rows so the 5s
  // poll keeps in-flight statuses fresh across every loaded page. Reads the
  // loaded count from a ref so its identity stays stable (see ref comment).
  const loadRecentCalls = useCallback(async () => {
    if (requiresLogin) {
      return
    }
    setRecentCallsLoading(true)
    try {
      const windowSize = Math.min(
        100,
        Math.max(RECORDINGS_PAGE_SIZE, recentCallsCountRef.current),
      )
      const page = await fetchCallsPage(windowSize, 0)
      if (page) {
        recentCallsCountRef.current = page.calls.length
        setRecentCalls(page.calls)
        setRecentCallsHasMore(page.hasMore)
      }
    } catch {
      // best-effort, swallow
    } finally {
      setRecentCallsLoading(false)
    }
  }, [fetchCallsPage, requiresLogin])

  // Append the next page when the user scrolls to the bottom of the list.
  const loadMoreRecentCalls = useCallback(async () => {
    if (requiresLogin || recentCallsLoadingMore || !recentCallsHasMore) {
      return
    }
    setRecentCallsLoadingMore(true)
    try {
      const page = await fetchCallsPage(RECORDINGS_PAGE_SIZE, recentCallsCountRef.current)
      if (page) {
        setRecentCalls((current) => {
          const seen = new Set(current.map((call) => call.call_id))
          const merged = [...current]
          for (const call of page.calls) {
            if (!seen.has(call.call_id)) {
              merged.push(call)
            }
          }
          recentCallsCountRef.current = merged.length
          return merged
        })
        setRecentCallsHasMore(page.hasMore)
      }
    } catch {
      // best-effort, swallow
    } finally {
      setRecentCallsLoadingMore(false)
    }
  }, [fetchCallsPage, requiresLogin, recentCallsLoadingMore, recentCallsHasMore])

  useEffect(() => {
    loadRecentCalls()
  }, [loadRecentCalls, configReload])

  // Bump the recent calls list while a call is in flight so the right pane updates.
  useEffect(() => {
    const intervalId = window.setInterval(() => {
      loadRecentCalls()
    }, 5000)
    return () => window.clearInterval(intervalId)
  }, [loadRecentCalls])

  // Infinite scroll: load the next page when the sentinel at the bottom of the
  // recordings list scrolls into view. Only active on the recordings tab.
  useEffect(() => {
    if (activeTab !== 'recordings') {
      return undefined
    }
    const sentinel = recordingsSentinelRef.current
    if (!sentinel || !recentCallsHasMore) {
      return undefined
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          loadMoreRecentCalls()
        }
      },
      { rootMargin: '200px' },
    )
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [activeTab, recentCallsHasMore, loadMoreRecentCalls])

  // While a bulk campaign is running, refresh the session so per-call statuses
  // update and we can render accurate progress counts on the button.
  useEffect(() => {
    if (!bulkRunCallIds.length || !session?.search_id) {
      return undefined
    }
    const trackedIds = new Set(bulkRunCallIds)
    const trackedCalls = (session.calls || []).filter((call) => trackedIds.has(call.call_id))
    const allTerminal =
      trackedCalls.length === trackedIds.size &&
      trackedCalls.every((call) => call.status !== 'busy' && call.status !== 'running')
    if (allTerminal) {
      return undefined
    }
    const intervalId = window.setInterval(() => {
      refreshSession({ quiet: true })
    }, 5000)
    return () => window.clearInterval(intervalId)
  }, [bulkRunCallIds, session?.search_id, session?.calls])

  const loadSavedPresets = useCallback(async () => {
    if (requiresLogin) {
      return
    }
    try {
      const url = new URL(`${apiBaseUrl}/api/voice-lab/campaign-presets`, window.location.origin)
      if (effectiveToken.trim()) {
        url.searchParams.set('token', effectiveToken.trim())
      }
      const response = await fetch(url.toString(), {
        headers: requestHeaders(),
        credentials: 'same-origin',
      })
      if (!response.ok) {
        return
      }
      const payload = await readApiPayload(response)
      setSavedPresets(Array.isArray(payload.presets) ? payload.presets : [])
    } catch (_err) {
      // best-effort
    }
  }, [apiBaseUrl, effectiveToken, requiresLogin])

  useEffect(() => {
    loadSavedPresets()
  }, [loadSavedPresets, configReload])

  function applySavedPreset(presetId) {
    setSelectedSavedPresetId(presetId)
    if (!presetId) {
      return
    }
    const preset = savedPresets.find((item) => item.preset_id === presetId)
    if (!preset) {
      return
    }
    const cfg = preset.config || {}
    if (typeof cfg.product_spoken === 'string') {
      setCampaignProductSpoken(cfg.product_spoken)
    }
    if (typeof cfg.requested_quantity === 'string') {
      setCampaignRequestedQuantity(cfg.requested_quantity)
    }
    if (typeof cfg.opening_line === 'string') {
      setCampaignOpeningLine(cfg.opening_line)
    }
    if (Array.isArray(cfg.slot_order)) {
      const next = { availability: false, price: false, warranty: false, discount: false }
      for (const slot of cfg.slot_order) {
        if (slot in next) {
          next[slot] = true
        }
      }
      setCampaignSlots(next)
    }
    setCampaignPreset('custom')
    setCampaignAdvancedOpen(true)
    setCampaignJson(JSON.stringify(cfg, null, 2))
  }

  async function saveCurrentAsPreset() {
    if (presetSaveBusy) {
      return
    }
    const defaultName = savedPresets.find((item) => item.preset_id === selectedSavedPresetId)?.name
      || (campaignProductSpoken || query || 'preset').slice(0, 60)
    const name = window.prompt('Preset name', defaultName)
    if (!name || !name.trim()) {
      return
    }
    setPresetSaveBusy(true)
    try {
      let body
      try {
        body = campaignAdvancedOpen && campaignJson.trim()
          ? JSON.parse(campaignJson)
          : campaignPreview
      } catch (err) {
        setError(`Invalid JSON in campaign config: ${err.message}`)
        return
      }
      const response = await fetch(`${apiBaseUrl}/api/voice-lab/campaign-presets`, {
        method: 'POST',
        headers: requestHeaders(true),
        credentials: 'same-origin',
        body: JSON.stringify({
          preset_id: selectedSavedPresetId || null,
          name: name.trim(),
          config: body,
        }),
      })
      const payload = await readApiPayload(response)
      if (!response.ok) {
        throw new Error(payload.detail || 'Could not save preset.')
      }
      await loadSavedPresets()
      if (payload.preset?.preset_id) {
        setSelectedSavedPresetId(payload.preset.preset_id)
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setPresetSaveBusy(false)
    }
  }

  async function deleteSavedPreset() {
    if (!selectedSavedPresetId) {
      return
    }
    if (!window.confirm('Delete this preset?')) {
      return
    }
    try {
      await fetch(`${apiBaseUrl}/api/voice-lab/campaign-presets/${encodeURIComponent(selectedSavedPresetId)}`, {
        method: 'DELETE',
        headers: requestHeaders(),
        credentials: 'same-origin',
      })
      setSelectedSavedPresetId('')
      await loadSavedPresets()
    } catch (err) {
      setError(err.message)
    }
  }

  function rememberLocation(value) {
    const trimmed = (value || '').trim()
    setLocation(trimmed)
    setIsLocationPromptOpen(false)
    setLocationError('')
    if (trimmed) {
      window.localStorage.setItem(LOCATION_STORAGE_KEY, trimmed)
    } else {
      window.localStorage.removeItem(LOCATION_STORAGE_KEY)
    }
  }

  function handleConfirmManualLocation(value) {
    rememberLocation(value)
  }

  async function handleUseCurrentLocation() {
    setLocationError('')
    setIsResolvingLocation(true)
    try {
      if (!navigator.geolocation) {
        throw new Error('Geolocation is not supported in this browser.')
      }
      const position = await new Promise((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, {
          enableHighAccuracy: false,
          timeout: 8000,
        })
      })
      const response = await fetch(`${apiBaseUrl}/api/location/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
        }),
      })
      if (!response.ok) {
        throw new Error('Location resolve failed.')
      }
      const payload = await response.json()
      rememberLocation(payload.location || '')
    } catch (err) {
      setLocationError(err.message || 'Could not detect location.')
    } finally {
      setIsResolvingLocation(false)
    }
  }

  async function callAllVendors() {
    if (!session?.search_id) {
      return
    }    if (!enabledProviders.length) {
      return
    }
    setBulkCallBusy(true)
    setError('')
    try {
      const callableVendors = vendors.filter((vendor) => vendor.callable && vendor.id)
      if (!callableVendors.length) {
        throw new Error('No callable vendors in this session.')
      }
      const response = await fetch(`${apiBaseUrl}/api/voice-lab/sessions/${session.search_id}/calls/bulk`, {
        method: 'POST',
        headers: requestHeaders(true),
        credentials: 'same-origin',
        body: JSON.stringify({
          vendor_ids: callableVendors.map((vendor) => vendor.id),
          provider_id: providerId,
          campaign_config: campaignConfigForCall(),
          concurrency: Math.max(1, Math.min(Number(bulkConcurrency) || 1, bulkMaxConcurrency)),
        }),
      })
      const payload = await readApiPayload(response)
      if (!response.ok) {
        throw new Error(payload.detail || 'Bulk call failed.')
      }
      if (payload.session) {
        setSession(payload.session)
      }
      const startedIds = (payload.started || []).map((entry) => entry.call_id).filter(Boolean)
      setBulkRunCallIds(startedIds)
      loadRecentCalls()
    } catch (err) {
      setError(err.message)
    } finally {
      setBulkCallBusy(false)
    }
  }

  async function login(event) {
    event.preventDefault()
    setError('')
    setIsLoggingIn(true)
    try {
      const response = await fetch(`${apiBaseUrl}/api/voice-lab/login`, {
        method: 'POST',
        headers: requestHeaders(true),
        credentials: 'same-origin',
        body: JSON.stringify({ password: loginPassword }),
      })
      const payload = await readApiPayload(response)
      if (!response.ok) {
        throw new Error(payload.detail || 'Login failed.')
      }
      setLoginPassword('')
      setRequiresLogin(false)
      setConfigReload((value) => value + 1)
    } catch (err) {
      setError(err.message)
    } finally {
      setIsLoggingIn(false)
    }
  }

  async function logout() {
    await fetch(`${apiBaseUrl}/api/voice-lab/logout`, {
      method: 'POST',
      headers: requestHeaders(),
      credentials: 'same-origin',
    })
    setRequiresLogin(true)
    setConfig(null)
    setSession(null)
    setLatestCall(null)
  }

  function applyCampaignPreset(nextPreset) {
    setCampaignPreset(nextPreset)
    if (nextPreset === 'gold') {
      setCampaignRequestedQuantity('10 gram')
      setCampaignSlots({
        availability: true,
        price: true,
        warranty: false,
        discount: true,
      })
      setCampaignOpeningLine('')
      return
    }
    if (nextPreset === 'electronics') {
      setCampaignRequestedQuantity('एक piece')
      setCampaignSlots({
        availability: true,
        price: true,
        warranty: true,
        discount: true,
      })
      setCampaignOpeningLine('')
      return
    }
    if (nextPreset === 'auto') {
      const goldLike = /gold|coin|bar|bullion|24k|22k/i.test(query)
      applyCampaignPreset(goldLike ? 'gold' : 'electronics')
      setCampaignPreset('auto')
    }
  }

  function campaignConfigForCall() {
    if (!campaignConfigSupported) {
      return null
    }
    if (campaignAdvancedOpen && campaignJson.trim()) {
      try {
        return JSON.parse(campaignJson)
      } catch (_err) {
        throw new Error('Campaign JSON is invalid.')
      }
    }
    const config = { ...campaignPreview }
    if (campaignPreset === 'gold') {
      config.slot_prompts = {
        availability: 'available है क्या?',
        price: 'आज का final rate क्या चल रहा है?',
        discount: 'cash या UPI पे कुछ discount मिलेगा?',
      }
      config.retry_prompts = {
        price: [
          'आज का rate कितना पड़ेगा?',
          'बस final buying rate बता दीजिए.',
        ],
        discount: [
          'payment तुरंत करूँ तो final में कुछ कम हो पाएगा?',
          'cash या UPI पे कुछ discount मिलेगा?',
        ],
      }
    }
    return config
  }

  async function createVoiceLabSession({ direct = false } = {}) {
    const response = await fetch(`${apiBaseUrl}/api/voice-lab/sessions`, {
      method: 'POST',
      headers: requestHeaders(true),
      credentials: 'same-origin',
      body: JSON.stringify({
        query,
        location: location.trim() || null,
        max_vendors: Number(maxVendors),
        include_online: direct ? false : includeOnline,
        include_vendors: !direct,
      }),
    })
    const payload = await readApiPayload(response)
    if (!response.ok) {
      throw new Error(payload.detail || 'Vendor discovery failed.')
    }
    setSession(payload)
    const firstCallable = payload.vendors?.find((vendor) => vendor.callable) || payload.vendors?.[0]
    setSelectedVendorId(firstCallable?.id || '')
    return payload
  }

  async function findVendors(event) {
    event.preventDefault()
    setError('')
    setIsFinding(true)
    // Reset prior run so the stage stepper reflects the new search instead of
    // staying frozen on the previous session's completed/call/outcome state.
    setSession(null)
    setSelectedVendorId('')
    setLatestCall(null)
    setBulkRunCallIds([])
    try {
      await createVoiceLabSession()
    } catch (err) {
      setError(err.message)
    } finally {
      setIsFinding(false)
    }
  }

  async function refreshSession({ quiet = false } = {}) {
    if (!session?.search_id) {
      return
    }
    if (!quiet) {
      setError('')
    }
    try {
      const url = new URL(`${apiBaseUrl}/api/voice-lab/sessions/${session.search_id}`, window.location.origin)
      if (effectiveToken.trim()) {
        url.searchParams.set('token', effectiveToken.trim())
      }
      const response = await fetch(url.toString(), {
        headers: requestHeaders(),
        credentials: 'same-origin',
      })
      const payload = await readApiPayload(response)
      if (!response.ok) {
        throw new Error(payload.detail || 'Session refresh failed.')
      }
      setSession(payload)
      setLatestCall(payload.calls?.[0] || null)
    } catch (err) {
      if (!quiet) {
        setError(err.message)
      }
    }
  }

  useEffect(() => {
    if (!session?.search_id || !callIsPending) {
      return undefined
    }
    const intervalId = window.setInterval(() => {
      refreshSession({ quiet: true })
    }, 5000)
    return () => window.clearInterval(intervalId)
  }, [session?.search_id, activeCall?.call_id, activeCall?.status, apiBaseUrl, effectiveToken])

  async function callVendor() {
    if ((!directCallSelected && (!session?.search_id || !selectedVendorId)) || (directCallSelected && (!query.trim() || !directPhone.trim()))) {
      return
    }
    setError('')
    setIsCalling(true)
    try {
      const activeSession = directCallSelected ? await createVoiceLabSession({ direct: true }) : session
      const campaignConfig = campaignConfigForCall()
      const response = await fetch(`${apiBaseUrl}/api/voice-lab/sessions/${activeSession.search_id}/calls`, {
        method: 'POST',
        headers: requestHeaders(true),
        credentials: 'same-origin',
        body: JSON.stringify({
          vendor_id: directCallSelected ? null : selectedVendorId,
          provider_id: providerId,
          direct_phone: directCallSelected ? directPhone.trim() : null,
          direct_name: directCallSelected ? directName.trim() || null : null,
          campaign_config: campaignConfig,
        }),
      })
      const payload = await readApiPayload(response)
      if (!response.ok) {
        throw new Error(payload.detail || 'Call failed.')
      }
      setSession(payload.session)
      setLatestCall(payload.call)
    } catch (err) {
      setError(err.message)
    } finally {
      setIsCalling(false)
    }
  }

  return (
    <div className="min-h-screen bg-[#f4f5ef] text-slate-900">
      <LocationPrompt
        isOpen={isLocationPromptOpen}
        isResolving={isResolvingLocation}
        error={locationError}
        initialValue={location}
        apiBaseUrl={apiBaseUrl}
        onClose={() => setIsLocationPromptOpen(false)}
        onUseCurrentLocation={handleUseCurrentLocation}
        onConfirmManual={handleConfirmManualLocation}
      />
      <header className="border-b border-slate-200 bg-white/90">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-5 py-4">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.28em] text-slate-500">Zwig</p>
            <h1 className="font-display text-2xl font-black text-slate-950">Voice Lab</h1>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {isHostedDashboard && !requiresLogin ? (
              <button
                type="button"
                onClick={logout}
                className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:border-slate-500"
              >
                Log out
              </button>
            ) : null}
          </div>
        </div>
      </header>

      {isHostedDashboard && requiresLogin ? (
        <main className="mx-auto flex min-h-[calc(100vh-86px)] max-w-md items-center px-5">
          <form onSubmit={login} className="w-full rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
            <p className="text-xs font-bold uppercase tracking-[0.24em] text-slate-500">Dashboard login</p>
            <h2 className="mt-2 font-display text-2xl font-black text-slate-950">Voice Lab</h2>
            {error ? (
              <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm font-semibold text-rose-800">
                {error}
              </div>
            ) : null}
            <label className="mt-4 block text-sm font-bold text-slate-800" htmlFor="voice-lab-login">
              Password
            </label>
            <input
              id="voice-lab-login"
              type="password"
              value={loginPassword}
              onChange={(event) => setLoginPassword(event.target.value)}
              className="mt-2 w-full rounded-lg border border-slate-300 bg-white px-3 py-3 text-sm outline-none focus:border-slate-800"
              autoFocus
            />
            <button
              type="submit"
              disabled={isLoggingIn || !loginPassword.trim()}
              className="mt-4 w-full rounded-lg bg-slate-900 px-4 py-3 text-sm font-bold text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isLoggingIn ? 'Signing in' : 'Sign in'}
            </button>
          </form>
        </main>
      ) : (

      <main className="mx-auto grid max-w-7xl gap-5 px-5 py-5 lg:grid-cols-[380px_minmax(0,1fr)]">
        <aside className="space-y-5">
          <form onSubmit={findVendors} className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            <label className="text-sm font-bold text-slate-800" htmlFor="voice-lab-query">
              Query
            </label>
            <textarea
              id="voice-lab-query"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              rows={4}
              className="mt-2 w-full resize-none rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-slate-800"
            />

            <label className="mt-4 block text-sm font-bold text-slate-800" htmlFor="voice-lab-location">
              Location override
            </label>
            <button
              id="voice-lab-location"
              type="button"
              onClick={() => setIsLocationPromptOpen(true)}
              className="mt-2 flex w-full items-center justify-between rounded-lg border border-slate-300 bg-white px-3 py-2 text-left text-sm hover:border-slate-500"
            >
              <span className={location ? 'text-slate-900' : 'text-slate-400'}>
                {location || 'Auto (from query)'}
              </span>
              {location ? (
                <span
                  role="button"
                  tabIndex={0}
                  onClick={(event) => {
                    event.stopPropagation()
                    rememberLocation('')
                  }}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      event.stopPropagation()
                      rememberLocation('')
                    }
                  }}
                  className="text-xs font-semibold text-slate-500 hover:text-slate-900"
                  aria-label="Clear location override"
                >
                  Clear
                </span>
              ) : (
                <span className="text-xs font-semibold text-slate-500">Set</span>
              )}
            </button>

            <div className="mt-4 grid grid-cols-2 gap-3">
              <label className="block text-sm font-bold text-slate-800" htmlFor="voice-lab-max">
                Max vendors
                <input
                  id="voice-lab-max"
                  type="number"
                  min="1"
                  max="25"
                  value={maxVendors}
                  onChange={(event) => setMaxVendors(event.target.value)}
                  className="mt-2 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-slate-800"
                />
              </label>
              <label className="flex items-end gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm font-semibold text-slate-700">
                <input
                  type="checkbox"
                  checked={includeOnline}
                  onChange={(event) => setIncludeOnline(event.target.checked)}
                  className="h-4 w-4 accent-slate-900"
                />
                Online benchmark
              </label>
            </div>

            {!isHostedDashboard ? (
              <>
                <label className="mt-4 block text-sm font-bold text-slate-800" htmlFor="voice-lab-token">
                  Admin token
                </label>
                <input
                  id="voice-lab-token"
                  value={token}
                  onChange={(event) => setToken(event.target.value)}
                  placeholder={config?.requires_token ? 'Required' : 'Optional'}
                  className="mt-2 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-slate-800"
                />
              </>
            ) : null}

            <button
              type="submit"
              disabled={isFinding || !query.trim()}
              className="mt-4 w-full rounded-lg bg-slate-900 px-4 py-3 text-sm font-bold text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isFinding ? 'Finding vendors' : 'Find vendors'}
            </button>
          </form>
        </aside>

        <section className="space-y-5">
          <section className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
            <div className="flex items-center justify-between gap-3">
              <ol className="flex flex-wrap items-center gap-x-2 gap-y-2">
                {stages.map((stage, index) => {
                  const isLast = index === stages.length - 1
                  const dotClasses = stage.status === 'completed'
                    ? 'border-emerald-500 bg-emerald-500'
                    : stage.status === 'running' || stage.status === 'busy'
                    ? 'border-amber-500 bg-amber-400'
                    : 'border-slate-300 bg-white'
                  const labelClasses = stage.status === 'completed'
                    ? 'text-slate-900'
                    : stage.status === 'running' || stage.status === 'busy'
                    ? 'text-amber-900'
                    : 'text-slate-400'
                  return (
                    <li key={stage.id} className="flex items-center gap-2 min-w-0">
                      <div className={`h-2.5 w-2.5 rounded-full border ${dotClasses}`} />
                      <span className={`text-xs font-bold uppercase tracking-[0.16em] whitespace-nowrap ${labelClasses}`}>{stage.label}</span>
                      <span className="hidden sm:inline truncate text-xs text-slate-500" title={stage.detail}>· {stage.detail}</span>
                      {!isLast ? <span className="mx-1 text-slate-300">›</span> : null}
                    </li>
                  )
                })}
              </ol>
              <div className="flex shrink-0 gap-1 rounded-md border border-slate-200 bg-slate-50 p-1">
                <button
                  type="button"
                  onClick={() => setActiveTab('lab')}
                  className={`rounded px-3 py-1 text-xs font-bold ${activeTab === 'lab' ? 'bg-white text-slate-950 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}
                >
                  Lab
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab('recordings')}
                  className={`rounded px-3 py-1 text-xs font-bold ${activeTab === 'recordings' ? 'bg-white text-slate-950 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}
                >
                  Recordings
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab('requests')}
                  className={`rounded px-3 py-1 text-xs font-bold ${activeTab === 'requests' ? 'bg-white text-slate-950 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}
                >
                  Requests
                </button>
              </div>
            </div>
          </section>

          {error ? (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm font-semibold text-rose-800">
              {error}
            </div>
          ) : null}

          {activeTab === 'recordings' ? (
            <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs font-bold uppercase tracking-[0.24em] text-slate-500">All recordings</p>
                <button
                  type="button"
                  onClick={loadRecentCalls}
                  disabled={recentCallsLoading}
                  className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-bold text-slate-700 hover:border-slate-500 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {recentCallsLoading ? 'Refreshing' : 'Refresh'}
                </button>
              </div>
              {activeCall?.recording_stream_url ? (
                <div className="mt-4 rounded-md border border-slate-200 bg-slate-50 p-3">
                  <p className="text-xs font-bold text-slate-700">Now playing: {activeCall.vendor?.canonical_name || activeCall.vendor?.name || activeCall.call_id}</p>
                  <audio controls src={recordingUrl(apiBaseUrl, activeCall, effectiveToken)} className="mt-2 w-full" />
                </div>
              ) : null}
              {/* Gate the details on a playable recording so a lab call left in
                  the shared `activeCall` doesn't surface its transcript/slots
                  here before the user has actually selected a recording. */}
              {activeCall?.recording_stream_url ? <CallDetails call={activeCall} /> : null}
              <div className="mt-4 space-y-2">
                {recentCalls.length ? recentCalls.map((call) => (
                  <button
                    type="button"
                    key={call.call_id}
                    onClick={() => setLatestCall(call)}
                    className={`w-full rounded-lg border px-3 py-2 text-left text-sm ${activeCall?.call_id === call.call_id ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-200 bg-white text-slate-700 hover:border-slate-400'}`}
                  >
                    <span className="block font-bold">{call.vendor?.canonical_name || call.vendor?.name || call.session?.product?.name || call.call_id}</span>
                    <span className="block text-xs opacity-75">{[call.provider_label || call.provider, callStatusLabel(call.status, call.outcome?.label), formatDate(call.started_at || call.called_at || call.updated_at)].filter(Boolean).join(' · ')}</span>
                  </button>
                )) : (
                  <p className="text-sm text-slate-400">{recentCallsLoading ? 'Loading…' : 'No calls yet.'}</p>
                )}
                {recentCalls.length ? (
                  <div ref={recordingsSentinelRef} className="pt-1 text-center">
                    {recentCallsLoadingMore ? (
                      <p className="text-xs text-slate-400">Loading more…</p>
                    ) : recentCallsHasMore ? (
                      <button
                        type="button"
                        onClick={loadMoreRecentCalls}
                        className="text-xs font-bold text-slate-500 hover:text-slate-900"
                      >
                        Load more
                      </button>
                    ) : (
                      <p className="text-xs text-slate-300">End of recordings</p>
                    )}
                  </div>
                ) : null}
              </div>
            </section>
          ) : null}

          {activeTab === 'requests' ? (
            <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h2 className="text-sm font-bold text-slate-900">Gold fulfilment requests</h2>
                  <p className="text-xs text-slate-500">Queries from gold.zwig.in. Post supplier quotes — they appear in the user’s chat instantly.</p>
                </div>
                <button
                  type="button"
                  onClick={fetchGoldRequests}
                  className="rounded border border-slate-300 px-3 py-1 text-xs font-semibold text-slate-700 hover:bg-slate-50"
                >
                  {goldRequestsBusy ? 'Refreshing…' : 'Refresh'}
                </button>
              </div>

              <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,360px)_minmax(0,1fr)]">
                {/* List */}
                <div className="max-h-[70vh] space-y-2 overflow-y-auto">
                  {goldRequests.length === 0 ? (
                    <p className="text-xs text-slate-500">No requests yet.</p>
                  ) : (
                    goldRequests.map((req) => (
                      <button
                        key={req.request_id}
                        type="button"
                        onClick={() => openGoldRequest(req.request_id)}
                        className={`w-full rounded-lg border p-3 text-left ${selectedRequestId === req.request_id ? 'border-amber-400 bg-amber-50' : 'border-slate-200 hover:bg-slate-50'}`}
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-mono text-xs font-bold text-slate-800">{req.request_id}</span>
                          <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${req.status === 'responded' ? 'bg-emerald-100 text-emerald-700' : req.status === 'closed' ? 'bg-slate-200 text-slate-600' : 'bg-amber-100 text-amber-700'}`}>
                            {req.status}
                          </span>
                        </div>
                        <p className="mt-1 text-sm font-semibold text-slate-900">{req.product || '—'}{(req.quantity || req.query?.quantity) ? ` · ${req.quantity || req.query?.quantity}` : ''}</p>
                        <p className="text-xs text-slate-500">{(req.category || 'gold')} · {req.location || 'location n/a'} · {(req.responses || []).length} quote(s)</p>
                      </button>
                    ))
                  )}
                </div>

                {/* Detail + reply */}
                <div className="rounded-lg border border-slate-200 p-3">
                  {!requestDetail ? (
                    <p className="text-xs text-slate-500">Select a request to view it and post supplier quotes.</p>
                  ) : (
                    <div className="space-y-3">
                      <div>
                        <p className="font-mono text-xs font-bold text-slate-800">{requestDetail.request_id}</p>
                        <p className="text-sm font-semibold text-slate-900">{requestDetail.product || '—'} · {requestDetail.location || 'location n/a'}</p>
                        <p className="text-xs text-slate-500">{(requestDetail.category || 'gold')} · Weight/qty asked: <span className="font-semibold text-slate-700">{requestDetail.quantity || requestDetail.query?.quantity || 'not specified'}</span></p>
                      </div>

                      {(requestDetail.live_rates || []).length > 0 ? (
                        <div>
                          <p className="text-[11px] font-bold uppercase tracking-wide text-slate-400">Results shown to user</p>
                          <div className="mt-1 max-h-32 space-y-1 overflow-y-auto">
                            {requestDetail.live_rates.map((lr, i) => (
                              <div key={i} className="flex justify-between text-xs text-slate-600">
                                <span>{lr.vendor_name || lr.label || '—'}{lr.city ? ` · ${lr.city}` : ''}</span>
                                <span className="font-semibold">{lr.sell_rate ?? ''}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      ) : null}

                      <div>
                        <p className="text-[11px] font-bold uppercase tracking-wide text-slate-400">Supplier quotes ({(requestDetail.responses || []).length})</p>
                        <div className="mt-1 space-y-1">
                          {(requestDetail.responses || []).map((r) => (
                            <div key={r.response_id} className="rounded border border-slate-100 bg-slate-50 px-2 py-1 text-xs">
                              <span className="font-semibold text-slate-800">{r.supplier}</span>
                              {r.price ? <span className="text-slate-700"> — {r.price}</span> : null}
                              {r.note ? <p className="text-slate-500">{r.note}</p> : null}
                              {r.url && /^https?:\/\//i.test(r.url) ? <a href={r.url} target="_blank" rel="noreferrer" className="block truncate text-blue-600 underline">{r.url}</a> : r.url ? <span className="block truncate text-slate-500">{r.url}</span> : null}
                            </div>
                          ))}
                        </div>
                      </div>

                      <form onSubmit={submitGoldResponse} className="space-y-2 border-t border-slate-200 pt-3">
                        <p className="text-[11px] font-bold uppercase tracking-wide text-slate-400">Post a quote</p>
                        <input
                          value={respSupplier}
                          onChange={(e) => setRespSupplier(e.target.value)}
                          placeholder="Supplier name"
                          className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
                        />
                        <input
                          value={respPrice}
                          onChange={(e) => setRespPrice(e.target.value)}
                          placeholder="Quote / price (e.g. ₹74,200 /10g)"
                          className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
                        />
                        <textarea
                          value={respNote}
                          onChange={(e) => setRespNote(e.target.value)}
                          placeholder="Note (optional)"
                          rows={2}
                          className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
                        />
                        <input
                          value={respUrl}
                          onChange={(e) => setRespUrl(e.target.value)}
                          placeholder="Link (optional) — product / quote URL"
                          className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
                        />
                        <button
                          type="submit"
                          disabled={respBusy || !respSupplier.trim()}
                          className="rounded bg-amber-500 px-3 py-1.5 text-xs font-bold text-white hover:bg-amber-600 disabled:opacity-50"
                        >
                          {respBusy ? 'Posting…' : 'Send to user'}
                        </button>
                      </form>
                    </div>
                  )}
                </div>
              </div>
            </section>
          ) : null}

          {activeTab === 'lab' ? (
          <>

          <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
            <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-xs font-bold uppercase tracking-[0.24em] text-slate-500">Vendor shortlist</p>
                  <h2 className="mt-1 font-display text-xl font-black text-slate-950">
                    {session ? session.query.product : 'Run a query'}
                  </h2>
                </div>
                <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-right">
                  <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Benchmark</p>
                  <p className="text-sm font-black text-slate-950">{formatPrice(session?.benchmark_price) || 'Pending'}</p>
                </div>
              </div>

              <div className="mt-4 overflow-x-auto rounded-lg border border-slate-200">
                <div className="grid min-w-[620px] grid-cols-[42px_minmax(180px,1fr)_120px_120px_120px] bg-slate-50 px-3 py-2 text-xs font-bold uppercase tracking-[0.16em] text-slate-500">
                  <span />
                  <span>Vendor</span>
                  <span>Reliability</span>
                  <span>Pickup</span>
                  <span>Calls</span>
                </div>
                <div className="divide-y divide-slate-200">
                  {vendors.length ? (
                    vendors.map((vendor) => (
                      <label
                        key={vendor.id}
                        className={`grid min-w-[620px] cursor-pointer grid-cols-[42px_minmax(180px,1fr)_120px_120px_120px] items-center px-3 py-3 text-sm hover:bg-slate-50 ${selectedVendorId === vendor.id ? 'bg-emerald-50/70' : 'bg-white'}`}
                      >
                        <input
                          type="radio"
                          name="voice-lab-vendor"
                          checked={selectedVendorId === vendor.id}
                          onChange={() => {
                            setSelectedVendorId(vendor.id)
                            setCallMode('vendor')
                          }}
                          disabled={!vendor.callable}
                          className="h-4 w-4 accent-slate-900"
                        />
                        <span className="min-w-0">
                          <span className="block truncate font-bold text-slate-900" title={vendor.name}>{vendor.name}</span>
                          <span
                            className="block truncate text-xs text-slate-500"
                            title={`${vendor.phone || 'No phone'} · ${vendor.address || vendor.city || 'No address'}`}
                          >
                            {vendor.phone || 'No phone'} · {vendor.address || vendor.city || 'No address'}
                          </span>
                          {!vendor.callable ? (
                            <span className="mt-1 block text-xs font-semibold text-rose-700">{vendor.eligibility_reason || 'Not callable'}</span>
                          ) : null}
                        </span>
                        <span className="font-semibold text-slate-800">{percent(vendor.reliability?.score)}</span>
                        <span className="text-slate-600">{percent(vendor.reliability?.pickup_rate)}</span>
                        <span className="text-slate-600">{vendor.reliability?.n_calls || 0}</span>
                      </label>
                    ))
                  ) : (
                    <div className="px-3 py-10 text-center text-sm text-slate-500">No vendors loaded.</div>
                  )}
                </div>
              </div>
            </section>

            <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
              <p className="text-xs font-bold uppercase tracking-[0.24em] text-slate-500">Call setup</p>
              <label className="mt-3 block text-xs font-bold text-slate-800" htmlFor="voice-lab-provider">
                Provider
              </label>
              <select
                id="voice-lab-provider"
                value={providerId}
                onChange={(event) => setProviderId(event.target.value)}
                className="mt-2 h-10 w-full rounded-md border border-slate-300 bg-white px-3 text-[13px] font-semibold leading-none text-slate-950 outline-none focus:border-slate-800"
              >
                {providerOptions.map((provider) => (
                  <option key={provider.id} value={provider.id} disabled={!provider.enabled}>
                    {provider.label}{provider.enabled ? '' : ' - disabled'}
                  </option>
                ))}
              </select>
              <p className="mt-2 text-[12px] leading-5 text-slate-500">
                {providerOptions.find((provider) => provider.id === providerId)?.detail || 'No provider selected.'}
              </p>

              <div className="mt-4 grid grid-cols-2 gap-1 rounded-md border border-slate-200 bg-slate-50 p-1">
                <button
                  type="button"
                  onClick={() => setCallMode('vendor')}
                  className={`rounded px-3 py-2 text-xs font-bold ${callMode === 'vendor' ? 'bg-white text-slate-950 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}
                >
                  Vendor
                </button>
                <button
                  type="button"
                  onClick={() => setCallMode('direct')}
                  className={`rounded px-3 py-2 text-xs font-bold ${callMode === 'direct' ? 'bg-white text-slate-950 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}
                >
                  Direct number
                </button>
              </div>

              <div className="mt-4 rounded-md border border-slate-200 bg-slate-50 p-3">
                {directCallSelected ? (
                  <>
                    <p className="mb-3 rounded border border-slate-200 bg-white px-2 py-2 text-[11px] font-semibold leading-4 text-slate-500">
                      Query defines what the agent asks. Direct number defines who to call.
                    </p>
                    <label className="block text-xs font-bold text-slate-900" htmlFor="voice-lab-direct-phone">
                      Phone number
                    </label>
                    <input
                      id="voice-lab-direct-phone"
                      value={directPhone}
                      onChange={(event) => setDirectPhone(event.target.value)}
                      placeholder="+91..."
                      className="mt-2 h-10 w-full rounded-md border border-slate-300 bg-white px-3 text-sm outline-none focus:border-slate-800"
                    />
                    <label className="mt-3 block text-xs font-bold text-slate-900" htmlFor="voice-lab-direct-name">
                      Label
                    </label>
                    <input
                      id="voice-lab-direct-name"
                      value={directName}
                      onChange={(event) => setDirectName(event.target.value)}
                      placeholder="Optional"
                      className="mt-2 h-10 w-full rounded-md border border-slate-300 bg-white px-3 text-sm outline-none focus:border-slate-800"
                    />
                  </>
                ) : (
                  <>
                    <p className="text-sm font-bold leading-5 text-slate-900">{selectedVendor?.name || 'No vendor selected'}</p>
                    <p className="mt-1 text-xs text-slate-500">{selectedVendor?.phone || 'Select a callable vendor'}</p>
                  </>
                )}
              </div>

              {campaignConfigSupported ? (
                <div className="mt-4 rounded-md border border-slate-200 bg-white p-3">
                  <div>
                    <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-500">{(providerOptions.find((provider) => provider.id === providerId)?.label || providerId) + ' campaign'}</p>
                    <p className="mt-1 text-[11px] leading-4 text-slate-500">Per-call config. Env JSON remains the fallback.</p>
                  </div>

                  <div className="mt-3 flex items-center gap-2">
                    <select
                      value={selectedSavedPresetId || campaignPreset}
                      onChange={(event) => {
                        const value = event.target.value
                        if (value === 'auto' || value === 'electronics' || value === 'gold' || value === 'custom') {
                          setSelectedSavedPresetId('')
                          applyCampaignPreset(value)
                        } else {
                          applySavedPreset(value)
                        }
                      }}
                      className="h-8 flex-1 rounded border border-slate-300 bg-white px-2 text-xs font-bold text-slate-800 outline-none focus:border-slate-800"
                    >
                      <optgroup label="Built-in">
                        <option value="auto">Auto (detect from query)</option>
                        <option value="electronics">Electronics</option>
                        <option value="gold">Gold</option>
                        <option value="custom">Custom</option>
                      </optgroup>
                      {savedPresets.length ? (
                        <optgroup label="Saved">
                          {savedPresets.map((preset) => (
                            <option key={preset.preset_id} value={preset.preset_id}>{preset.name}</option>
                          ))}
                        </optgroup>
                      ) : null}
                    </select>
                    <button
                      type="button"
                      onClick={saveCurrentAsPreset}
                      disabled={presetSaveBusy}
                      className="h-8 rounded border border-slate-300 bg-white px-2 text-xs font-bold text-slate-800 hover:border-slate-500 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {selectedSavedPresetId ? 'Update' : 'Save as'}
                    </button>
                    {selectedSavedPresetId ? (
                      <button
                        type="button"
                        onClick={deleteSavedPreset}
                        className="h-8 rounded border border-rose-200 bg-white px-2 text-xs font-bold text-rose-700 hover:border-rose-400"
                      >
                        Delete
                      </button>
                    ) : null}
                  </div>

                  <label className="mt-3 block text-xs font-bold text-slate-900" htmlFor="voice-lab-campaign-product">
                    Product spoken
                  </label>
                  <input
                    id="voice-lab-campaign-product"
                    value={campaignProductSpoken}
                    onChange={(event) => {
                      setCampaignProductSpoken(event.target.value)
                      setCampaignPreset('custom')
                    }}
                    placeholder={session?.query?.product || query}
                    className="mt-2 h-9 w-full rounded-md border border-slate-300 bg-white px-3 text-sm outline-none focus:border-slate-800"
                  />

                  <label className="mt-3 block text-xs font-bold text-slate-900" htmlFor="voice-lab-campaign-quantity">
                    Requested quantity
                  </label>
                  <input
                    id="voice-lab-campaign-quantity"
                    value={campaignRequestedQuantity}
                    onChange={(event) => {
                      setCampaignRequestedQuantity(event.target.value)
                      setCampaignPreset('custom')
                    }}
                    className="mt-2 h-9 w-full rounded-md border border-slate-300 bg-white px-3 text-sm outline-none focus:border-slate-800"
                  />

                  <label className="mt-3 block text-xs font-bold text-slate-900" htmlFor="voice-lab-campaign-opening">
                    Opening line
                  </label>
                  <input
                    id="voice-lab-campaign-opening"
                    value={campaignOpeningLine}
                    onChange={(event) => {
                      setCampaignOpeningLine(event.target.value)
                      setCampaignPreset('custom')
                    }}
                    placeholder={defaultOpeningLine(campaignProduct)}
                    className="mt-2 h-9 w-full rounded-md border border-slate-300 bg-white px-3 text-sm outline-none focus:border-slate-800"
                  />

                  <div className="mt-3 grid grid-cols-2 gap-2">
                    {CAMPAIGN_SLOTS.map((slot) => (
                      <label key={slot.id} className="flex items-center gap-2 rounded border border-slate-200 bg-slate-50 px-2 py-2 text-xs font-bold text-slate-700">
                        <input
                          type="checkbox"
                          checked={campaignSlots[slot.id]}
                          onChange={(event) => {
                            setCampaignSlots((current) => ({ ...current, [slot.id]: event.target.checked }))
                            setCampaignPreset('custom')
                          }}
                          className="h-3.5 w-3.5 accent-slate-900"
                        />
                        {slot.label}
                      </label>
                    ))}
                  </div>

                  <button
                    type="button"
                    onClick={() => {
                      const nextOpen = !campaignAdvancedOpen
                      setCampaignAdvancedOpen(nextOpen)
                      if (nextOpen && !campaignJson.trim()) {
                        setCampaignJson(JSON.stringify(campaignPreview, null, 2))
                      }
                    }}
                    className="mt-3 text-xs font-bold text-slate-600 hover:text-slate-950"
                  >
                    {campaignAdvancedOpen ? 'Hide JSON' : 'Advanced JSON'}
                  </button>

                  {campaignAdvancedOpen ? (
                    <textarea
                      value={campaignJson}
                      onChange={(event) => {
                        setCampaignJson(event.target.value)
                        setCampaignPreset('custom')
                      }}
                      rows={7}
                      className="mt-2 w-full resize-y rounded-md border border-slate-300 bg-slate-950 px-3 py-2 font-mono text-[11px] leading-4 text-slate-50 outline-none focus:border-slate-700"
                    />
                  ) : null}
                </div>
              ) : null}

              <button
                type="button"
                onClick={callVendor}
                disabled={
                  isCalling ||
                  !enabledProviders.length ||
                  (directCallSelected ? !query.trim() || !directPhone.trim() : !session || !selectedVendorId)
                }
                className="mt-4 h-11 w-full rounded-md bg-emerald-600 px-4 text-sm font-black text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {isCalling ? 'Calling' : directCallSelected ? 'Call direct number' : 'Call selected vendor'}
              </button>

              {!directCallSelected && session && vendors.some((vendor) => vendor.callable) ? (
                (() => {
                  const callableCount = vendors.filter((v) => v.callable).length
                  const trackedIds = new Set(bulkRunCallIds)
                  const trackedCalls = trackedIds.size
                    ? (session.calls || []).filter((call) => trackedIds.has(call.call_id))
                    : []
                  const calling = trackedCalls.filter((call) => call.status === 'busy' || call.status === 'running').length
                  const completed = trackedCalls.filter((call) => call.status === 'completed').length
                  const failed = trackedCalls.filter((call) => call.status === 'failed' || call.status === 'no_answer').length
                  const total = trackedIds.size || callableCount
                  const stillRunning = trackedIds.size > 0 && (calling > 0 || trackedCalls.length < trackedIds.size)
                  const summary = trackedIds.size
                    ? `${completed} done · ${calling} calling · ${failed} failed of ${total}`
                    : null
                  return (
                    <>
                      <div className="mt-2 flex items-center gap-2">
                        <label htmlFor="voice-lab-bulk-concurrency" className="text-[12px] font-semibold text-slate-700 whitespace-nowrap">
                          Parallel calls
                        </label>
                        <input
                          id="voice-lab-bulk-concurrency"
                          type="number"
                          min={1}
                          max={bulkMaxConcurrency}
                          value={bulkConcurrency}
                          onChange={(event) => {
                            const next = Number(event.target.value)
                            if (Number.isNaN(next)) {
                              setBulkConcurrency('')
                              return
                            }
                            setBulkConcurrency(Math.max(1, Math.min(next, bulkMaxConcurrency)))
                          }}
                          disabled={bulkCallBusy || stillRunning}
                          className="h-9 w-20 rounded-md border border-slate-300 bg-white px-2 text-[13px] font-semibold text-slate-950 outline-none focus:border-slate-800 disabled:opacity-50"
                        />
                        <span className="text-[11px] text-slate-500">max {bulkMaxConcurrency}</span>
                      </div>
                      <button
                        type="button"
                        onClick={callAllVendors}
                        disabled={bulkCallBusy || isCalling || stillRunning || !enabledProviders.length}
                        className="mt-2 h-10 w-full rounded-md border border-slate-900 bg-white px-4 text-sm font-bold text-slate-900 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {bulkCallBusy
                          ? 'Queuing calls'
                          : stillRunning
                          ? `Campaign in progress · ${completed + failed}/${total}`
                          : `Call all ${callableCount} callable vendors`}
                      </button>
                      {summary ? (
                        <p className="mt-2 text-center text-xs text-slate-500">{summary}</p>
                      ) : null}
                    </>
                  )
                })()
              ) : null}
            </section>
          </div>

          <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
            <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-[11px] font-bold uppercase tracking-[0.2em] text-slate-500">After call</p>
                  <h2 className="mt-1 font-display text-lg font-black leading-6 text-slate-950">
                    {activeOutcome?.label || 'No call outcome yet'}
                  </h2>
                </div>
                {activeCall ? (
                  <span className={`rounded-full border px-3 py-1 text-xs font-bold uppercase tracking-[0.16em] ${statusTone(activeCall.outcome?.label || activeCall.status)}`}>
                    {callStatusLabel(activeCall.status, activeCall.outcome?.label)}
                  </span>
                ) : null}
              </div>

              <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                <div className="min-w-0 rounded-md border border-slate-200 bg-slate-50 px-3 py-2.5">
                  <p className="truncate text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">Quote</p>
                  <p className="mt-1 truncate text-base font-black leading-6 text-slate-950">{formatPrice(activeOutcome?.quoted_price) || 'Pending'}</p>
                </div>
                <div className="min-w-0 rounded-md border border-slate-200 bg-slate-50 px-3 py-2.5">
                  <p className="truncate text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">Delta</p>
                  <p className="mt-1 truncate text-base font-black leading-6 text-slate-950">{signedMoney(activeOutcome?.discount_amount)}</p>
                  <p className="truncate text-[11px] font-semibold leading-4 text-slate-500">{signedPercent(activeOutcome?.discount_percent)}</p>
                </div>
                <div className="min-w-0 rounded-md border border-slate-200 bg-slate-50 px-3 py-2.5">
                  <p className="truncate text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">Stock</p>
                  <p className="mt-1 truncate text-base font-black leading-6 text-slate-950">{activeOutcome?.stock_status || 'Unknown'}</p>
                </div>
                <div className="min-w-0 rounded-md border border-slate-200 bg-slate-50 px-3 py-2.5">
                  <p className="truncate text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">Negotiated</p>
                  <p className="mt-1 truncate text-base font-black leading-6 text-slate-950">{activeOutcome?.negotiated ? 'Yes' : 'No'}</p>
                </div>
              </div>

              {activeOutcome?.notes ? (
                <p className="mt-4 rounded-lg border border-slate-200 bg-white px-3 py-3 text-sm text-slate-700">{activeOutcome.notes}</p>
              ) : null}

              <CallDetails call={activeCall} />
            </section>

            <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs font-bold uppercase tracking-[0.24em] text-slate-500">Recordings</p>
                <button
                  type="button"
                  onClick={() => {
                    refreshSession()
                    loadRecentCalls()
                  }}
                  disabled={recentCallsLoading || isCalling || isFinding}
                  className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-bold text-slate-700 hover:border-slate-500 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {recentCallsLoading ? 'Syncing' : 'Sync'}
                </button>
              </div>
              {activeCall?.recording_stream_url ? (
                <div className="mt-4">
                  <audio controls src={recordingUrl(apiBaseUrl, activeCall, effectiveToken)} className="w-full" />
                </div>
              ) : (
                <p className="mt-4 text-sm text-slate-500">No recording available.</p>
              )}

              <div className="mt-5 space-y-2">
                {(calls.length ? calls : recentCalls).slice(0, 10).map((call) => (
                  <button
                    type="button"
                    key={call.call_id}
                    onClick={() => setLatestCall(call)}
                    className={`w-full rounded-lg border px-3 py-2 text-left text-sm ${activeCall?.call_id === call.call_id ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-200 bg-white text-slate-700 hover:border-slate-400'}`}
                  >
                    <span className="block font-bold">{call.vendor?.canonical_name || call.vendor?.name || call.session?.product?.name || call.call_id}</span>
                    <span className="block text-xs opacity-75">{[call.provider_label || call.provider, callStatusLabel(call.status, call.outcome?.label), formatDate(call.started_at || call.called_at || call.updated_at)].filter(Boolean).join(' · ')}</span>
                  </button>
                ))}
                {!calls.length && !recentCalls.length ? (
                  <p className="text-sm text-slate-400">{recentCallsLoading ? 'Loading recent calls' : 'No calls yet.'}</p>
                ) : null}
                {(calls.length ? calls : recentCalls).length > 10 ? (
                  <button
                    type="button"
                    onClick={() => setActiveTab('recordings')}
                    className="block w-full text-center text-xs font-bold text-slate-500 hover:text-slate-900"
                  >
                    View all {(calls.length ? calls : recentCalls).length} recordings →
                  </button>
                ) : null}
              </div>
            </section>
          </div>

          {session?.online_results?.length ? (
            <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
              <p className="text-xs font-bold uppercase tracking-[0.24em] text-slate-500">Benchmark sources</p>
              <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {session.online_results.slice(0, 6).map((result) => (
                  <div key={result.id} className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                    <p className="truncate text-sm font-bold text-slate-900">{result.name}</p>
                    <p className="mt-1 text-lg font-black text-slate-950">{formatPrice(result.price) || 'Price on request'}</p>
                    {!formatPrice(result.price) ? (
                      <p className="mt-1 text-xs font-bold text-slate-800">
                        Availability checked. Contact the vendor for exact price and product specifications.
                      </p>
                    ) : null}
                    <p className="mt-1 truncate text-xs text-slate-500">{result.delivery_time || result.result_type || result.source_type}</p>
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          </>
          ) : null}
        </section>
      </main>
      )}
    </div>
  )
}

export default VoiceLabDashboard

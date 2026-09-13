import { useEffect, useMemo, useReducer, useRef, useState } from 'react'
import ChatBubble from './components/ChatBubble'
import { ensurePushSubscribed } from './lib/push'
import LocationPrompt from './components/LocationPrompt'
import SessionSidebar from './components/SessionSidebar'
import VoiceLabDashboard from './components/VoiceLabDashboard'
import LandingPage from './landing-page/LandingPage'
import { AboutPage, ContactPage, PrivacyPolicyPage, SecurityPage, SupportPage, TermsPage } from './landing-page/LegalPages'
import SupplierOnboardingPage from './supplier-onboarding/SupplierOnboardingPage'
import SupplierPilotPage from './supplier-onboarding/SupplierPilotPage'
import SupplierCrmDashboard from './supplier-crm/SupplierCrmDashboard'
import SupplierDetailPage from './supplier-crm/SupplierDetailPage'
import SupplierLoiPage from './supplier-crm/SupplierLoiPage'
import SupplierPublicPage from './supplier-crm/SupplierPublicPage'
import { trackAmplitudeEvent } from './lib/amplitude'
import { formatPrice } from './lib/format'
import { isIndiaMartUrl } from './lib/resultPresentation'

const LOCATION_STORAGE_KEY = 'pricehunter-location'
const DEFAULT_LOCATION = 'Ahmedabad'
const DISPLAY_CURRENCY_STORAGE_KEY = 'pricehunter-display-currency'
const DEVICE_ID_STORAGE_KEY = 'pricehunter-device-id'
const LANDING_SEARCH_STORAGE_KEY = 'zwig-landing-search'
const RAZORPAY_SCRIPT_SRC = 'https://checkout.razorpay.com/v1/checkout.js'
const VOICE_LAB_HOSTS = new Set(['dashboard.zwig.in'])
const GOLD_HOSTS = new Set(['gold.zwig.in'])
const SUPPLIER_LISTING_INITIAL_DELAY_MS = 60_000
const SUPPLIER_LISTING_MIN_GAP_MS = 3_000
const SUPPLIER_LISTING_MAX_GAP_MS = 7_000
const DISPLAY_CURRENCIES = ['INR', 'USD', 'AED']
// Keep in sync with backend app/services/search_wait_copy.py (web + WhatsApp).
const SEARCH_WAIT_START =
  'I’m contacting suppliers in real time for the best prices. This usually takes a few minutes and won’t take more than about 5 — hang tight.'
const SEARCH_WAIT_NUDGE =
  'Still contacting suppliers in real time — please hold on a bit longer. This typically finishes within a few minutes.'
const SEARCH_WAIT_TIMEOUT =
  'This search is taking longer than usual. I’m still contacting suppliers — please check back in a few minutes if you need updated quotes.'
const SEARCH_WAIT_SILENCE_MS = 20_000
// Match backend settings.whatsapp_result_timeout_seconds (web parity).
const SEARCH_WAIT_TIMEOUT_MS = 600_000
const SEARCH_GONE_MESSAGE =
  'I couldn’t find that search anymore. Please send your requirement again.'
const EXPAND_GENERIC_ERROR_MESSAGE =
  'I could not load those suppliers right now. Please try again.'
const MORE_RESULTS_GONE_MESSAGE =
  'I could not find the previous search anymore. Please send the product and city again.'
const MORE_RESULTS_EMPTY_MESSAGE = 'No more vendor prices are available for this search.'
const DISMISS_MORE_RESULTS_MESSAGE =
  'Perfect. Send another product and city whenever you want me to search again.'

function expandErrorMessage(error) {
  const status = error?.status || error?.responseStatus
  if (status === 404) {
    return SEARCH_GONE_MESSAGE
  }
  const text = String(error?.message || '')
  if (/\b404\b/.test(text)) {
    return SEARCH_GONE_MESSAGE
  }
  return EXPAND_GENERIC_ERROR_MESSAGE
}

function loadRazorpayCheckout() {
  if (typeof window === 'undefined') {
    return Promise.reject(new Error('Checkout can only run in the browser.'))
  }

  if (window.Razorpay) {
    return Promise.resolve()
  }

  const existingScript = document.querySelector(`script[src="${RAZORPAY_SCRIPT_SRC}"]`)
  if (existingScript) {
    return new Promise((resolve, reject) => {
      existingScript.addEventListener('load', resolve, { once: true })
      existingScript.addEventListener('error', reject, { once: true })
    })
  }

  return new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = RAZORPAY_SCRIPT_SRC
    script.async = true
    script.onload = resolve
    script.onerror = reject
    document.body.appendChild(script)
  })
}

function getRouteFromLocation() {
  const pathname = window.location.pathname.replace(/\/+$/, '') || '/'
  if (pathname === '/internal/suppliers') {
    return 'supplierCrm'
  }
  if (pathname.startsWith('/internal/suppliers/')) {
    return 'supplierDetail'
  }
  if (pathname.startsWith('/suppliers/')) {
    return 'supplierPublic'
  }
  if (VOICE_LAB_HOSTS.has(window.location.hostname)) {
    return 'voiceLab'
  }

  if (pathname === '/about') {
    return 'about'
  }
  if (pathname === '/contact') {
    return 'contact'
  }
  if (pathname === '/privacy-policy') {
    return 'privacy'
  }
  if (pathname === '/terms') {
    return 'terms'
  }
  if (pathname === '/security') {
    return 'security'
  }
  if (pathname === '/support') {
    return 'support'
  }
  if (pathname === '/app') {
    return 'app'
  }
  if (pathname === '/supplier-onboarding') {
    return 'supplierOnboarding'
  }
  if (pathname === '/supplier-onboarding/start') {
    return 'supplierOnboardingForm'
  }
  if (pathname.startsWith('/supplier/pilot/')) {
    return 'supplierPilot'
  }
  if (pathname.startsWith('/supplier/loi/')) {
    return 'supplierLoi'
  }

  const normalized = (window.location.hash || '').replace(/^#/, '')

  // Gold-locked host (gold.zwig.in): gold landing by default, gold-locked app on #/app.
  if (GOLD_HOSTS.has(window.location.hostname)) {
    if (normalized === '/app') return 'goldApp'
    if (normalized === '/privacy') return 'privacy'
    return 'goldLanding'
  }

  if (normalized === '/app') {
    return 'app'
  }
  if (normalized === '/about') {
    return 'about'
  }
  if (normalized === '/contact') {
    return 'contact'
  }
  if (normalized === '/privacy') {
    return 'privacy'
  }
  if (normalized === '/privacy-policy') {
    return 'privacy'
  }
  if (normalized === '/terms') {
    return 'terms'
  }
  if (normalized === '/security') {
    return 'security'
  }
  if (normalized === '/support') {
    return 'support'
  }
  if (normalized === '/supplier-onboarding') {
    return 'supplierOnboarding'
  }
  if (normalized === '/supplier-onboarding/start') {
    return 'supplierOnboardingForm'
  }
  if (normalized.startsWith('/supplier/pilot/')) {
    return 'supplierPilot'
  }
  if (normalized.startsWith('/supplier/loi/')) {
    return 'supplierLoi'
  }
  if (normalized === '/internal/suppliers') {
    return 'supplierCrm'
  }
  if (normalized.startsWith('/internal/suppliers/')) {
    return 'supplierDetail'
  }
  if (normalized.startsWith('/suppliers/')) {
    return 'supplierPublic'
  }
  if (normalized === '/find') {
    return 'demoPremium'
  }
  if (normalized === '/voice-lab') {
    return 'voiceLab'
  }
  return 'landing'
}

function getSupplierPilotTokenFromLocation() {
  const pathname = window.location.pathname.replace(/\/+$/, '') || '/'
  const pathMatch = pathname.match(/^\/supplier\/pilot\/([^/]+)$/)
  if (pathMatch) {
    return decodeURIComponent(pathMatch[1])
  }
  const normalized = (window.location.hash || '').replace(/^#/, '').replace(/\/+$/, '')
  const hashMatch = normalized.match(/^\/supplier\/pilot\/([^/]+)$/)
  if (hashMatch) {
    return decodeURIComponent(hashMatch[1])
  }
  return ''
}

function getSupplierLoiTokenFromLocation() {
  const pathname = window.location.pathname.replace(/\/+$/, '') || '/'
  const pathMatch = pathname.match(/^\/supplier\/loi\/([^/]+)$/)
  if (pathMatch) {
    return decodeURIComponent(pathMatch[1])
  }
  const normalized = (window.location.hash || '').replace(/^#/, '').replace(/\/+$/, '')
  const hashMatch = normalized.match(/^\/supplier\/loi\/([^/]+)$/)
  if (hashMatch) {
    return decodeURIComponent(hashMatch[1])
  }
  return ''
}

function getSupplierDetailIdFromLocation() {
  const pathname = window.location.pathname.replace(/\/+$/, '') || '/'
  const pathMatch = pathname.match(/^\/internal\/suppliers\/(.+)$/)
  if (pathMatch) {
    return decodeURIComponent(pathMatch[1])
  }
  const normalized = (window.location.hash || '').replace(/^#/, '').replace(/\/+$/, '')
  const hashMatch = normalized.match(/^\/internal\/suppliers\/(.+)$/)
  if (hashMatch) {
    return decodeURIComponent(hashMatch[1])
  }
  return ''
}

function getSupplierPublicSlugFromLocation() {
  const pathname = window.location.pathname.replace(/\/+$/, '') || '/'
  const pathMatch = pathname.match(/^\/suppliers\/([^/]+)$/)
  if (pathMatch) {
    return decodeURIComponent(pathMatch[1])
  }
  const normalized = (window.location.hash || '').replace(/^#/, '').replace(/\/+$/, '')
  const hashMatch = normalized.match(/^\/suppliers\/([^/]+)$/)
  if (hashMatch) {
    return decodeURIComponent(hashMatch[1])
  }
  return ''
}

function getOrCreateDeviceId() {
  const existing = window.localStorage.getItem(DEVICE_ID_STORAGE_KEY)
  if (existing) {
    return existing
  }
  const generated =
    window.crypto?.randomUUID?.() ||
    `device-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
  window.localStorage.setItem(DEVICE_ID_STORAGE_KEY, generated)
  return generated
}

function formatList(items, maxVisible = 6) {
  if (!items?.length) {
    return ''
  }
  if (items.length <= maxVisible) {
    return items.join(', ')
  }
  return `${items.slice(0, maxVisible).join(', ')}, and ${items.length - maxVisible} more`
}

function getVendorKey(vendor) {
  return String(vendor?.vendor_id || vendor?.place_id || vendor?.phone || vendor?.name || '')
}

function isGoldSearch(progress) {
  return progress?.query?.category === 'gold'
}

function isSilverSearch(progress) {
  const text = `${progress?.query?.product || ''} ${progress?.query?.raw_query || ''}`.toLowerCase()
  return isGoldSearch(progress) && /\b(silver|xag|slv)\b/.test(text)
}

function bullionMetalLabel(progress) {
  return isSilverSearch(progress) ? 'silver' : 'gold'
}

function isDelayedSupplierListing(result) {
  const notes = String(result?.notes || '').toLowerCase()
  if (result?.result_type === 'vendor_offering' || result?.result_type === 'vendor_capability') {
    return false
  }
  return (
    notes.includes('indiamart') ||
    notes.includes('indiamart supplier') ||
    isIndiaMartUrl(result?.url)
  )
}

function splitTimedResults(progress, results) {
  if (isGoldSearch(progress)) {
    return { immediate: results, delayed: [] }
  }
  return {
    immediate: results.filter((result) => !isDelayedSupplierListing(result)),
    delayed: results.filter(isDelayedSupplierListing),
  }
}

function supplierListingGapMs() {
  return (
    SUPPLIER_LISTING_MIN_GAP_MS +
    Math.floor(Math.random() * (SUPPLIER_LISTING_MAX_GAP_MS - SUPPLIER_LISTING_MIN_GAP_MS + 1))
  )
}

function buildGoldProgressMessage(step) {
  if (step.id === 'vendor-discovery') {
    return 'Prepared the dealer board. Checking live rates now.'
  }
  if (step.id === 'gold-live-rates') {
    if (step.status === 'running') {
      return 'Checking dealer live-rate pages.'
    }
    if (step.status === 'failed') {
      return step.detail || 'Live dealer-rate check hit a problem.'
    }
    return 'Live dealer-rate check finished. I’ll show the final board below.'
  }
  return step.detail || (step.status === 'failed' ? `${step.label} hit a problem.` : `${step.label} ${step.status}.`)
}

function buildInitialSearchMessages(progress) {
  if (!progress) {
    return []
  }

  if (isGoldSearch(progress)) {
    const product = progress.query?.product || 'your product'
    const location = progress.query?.location || 'your area'
    return [
      {
        message_id: `search-start-${progress.search_id}`,
        role: 'assistant',
        kind: 'status',
        content: `I’ve started the live ${bullionMetalLabel(progress)}-rate search for ${product} in ${location}. I’m checking dealer live-rate pages and local bullion options now.`,
        created_at: new Date().toISOString(),
      },
    ]
  }

  const vendorNames = (progress.discovered_vendors || []).map((vendor) => vendor.name)
  const messages = []

  if (vendorNames.length > 0) {
    messages.push({
      message_id: `vendor-list-${progress.search_id}`,
      role: 'assistant',
      kind: 'status',
      content: `I found ${vendorNames.length} matching supplier${vendorNames.length > 1 ? 's' : ''}: ${formatList(vendorNames)}.`,
      created_at: new Date().toISOString(),
    })
  }

  return messages
}

function buildOfflineShortlistMessage(progress, vendors) {
  const vendorNames = vendors.map((vendor) => vendor.name).filter(Boolean)
  const vendorKeySuffix =
    vendors
      .map(getVendorKey)
      .filter(Boolean)
      .slice(0, 8)
      .join('-') || vendorNames.length
  if (isGoldSearch(progress) || vendorNames.length === 0) {
    return null
  }
  return {
    message_id: `vendor-shortlist-${progress.search_id}-${vendorKeySuffix}`,
    role: 'assistant',
    kind: 'status',
    content: `I’ve shortlisted these offline vendors to contact: ${formatList(vendorNames)}.`,
    created_at: new Date().toISOString(),
  }
}

function buildResultMessages(progress, newResults) {
  const online = newResults.filter((result) => result.source_type === 'online')
  const offline = newResults.filter((result) => result.source_type === 'offline')
  const messages = []

  if (online.length > 0) {
    if (isGoldSearch(progress)) {
      messages.push({
        message_id: `partial-online-${progress.search_id}-${online.map((result) => result.id).join('-')}`,
        role: 'assistant',
        kind: 'results',
        content:
          online.length === 1
            ? 'Here’s a live dealer rate. I’ll keep the board updated as more dealers are checked.'
            : `Here are ${online.length} live dealer rates, cheapest first. I’ll keep the board updated as more dealers are checked.`,
        payload: { results: online },
        created_at: new Date().toISOString(),
      })
    } else {
      messages.push({
        message_id: `partial-online-${progress.search_id}-${online.map((result) => result.id).join('-')}`,
        role: 'assistant',
        kind: 'results',
        content:
          online.length === 1
            ? 'I found one available price. I’ll keep checking more suppliers.'
            : `I found ${online.length} available prices. I’ll keep checking more suppliers.`,
        payload: { results: online },
        created_at: new Date().toISOString(),
      })
    }
  }

  if (offline.length > 0) {
    if (isGoldSearch(progress)) {
      messages.push({
        message_id: `partial-offline-${progress.search_id}-${offline.map((result) => result.id).join('-')}`,
        role: 'assistant',
        kind: 'results',
        content:
          offline.length === 1
            ? 'Here’s a local bullion dealer for you.'
            : `Here are ${offline.length} local bullion dealers for you.`,
        payload: { results: offline },
        created_at: new Date().toISOString(),
      })
    } else {
      const dbOfferingCount = offline.filter((result) => result.result_type === 'vendor_offering').length
      messages.push({
        message_id: `partial-offline-${progress.search_id}-${offline.map((result) => result.id).join('-')}`,
        role: 'assistant',
        kind: 'results',
        content:
          dbOfferingCount === offline.length
            ? offline.length === 1
              ? 'I found one matching supplier.'
              : `I found ${offline.length} matching suppliers.`
            : offline.length === 1
              ? `I received an update from ${offline[0].name}.`
              : `I received ${offline.length} more supplier update${offline.length > 1 ? 's' : ''}.`,
        payload: { results: offline },
        created_at: new Date().toISOString(),
      })
    }
  }

  return messages
}

function unseenGoldFinalResults(progress, seenIds) {
  return (progress?.final_results?.results || []).filter(
    (result) => result?.id && !seenIds.has(result.id),
  )
}

function buildFinalSearchMessage(progress) {
  if (isGoldSearch(progress)) {
    return {
      message_id: `final-${progress.search_id}`,
      role: 'assistant',
      kind: 'status',
      content: `The live ${bullionMetalLabel(progress)}-rate board is ready.`,
      created_at: new Date().toISOString(),
    }
  }
  return {
    message_id: `final-${progress.search_id}`,
    role: 'assistant',
    kind: 'results',
    content: 'The search is complete. Here are the results I found.',
    payload: progress.final_results,
    created_at: new Date().toISOString(),
  }
}

function buildShowMoreResultsMessage(progress, offset = null) {
  const nextOffset = offset ?? progress.next_result_offset ?? progress.partial_results?.length ?? 5
  const totalResults =
    progress.total_ranked_results ?? progress.final_results?.results?.length ?? 0
  const remaining = Math.max(0, Number(totalResults) - Number(nextOffset))
  if (remaining <= 0 || isGoldSearch(progress)) {
    return null
  }
  return {
    message_id: `show-more-results-${progress.search_id}-${nextOffset}`,
    role: 'assistant',
    kind: 'text',
    content: 'Do you want more supplier quotes?',
    payload: {
      actions: [
        {
          type: 'show_more_results',
          searchId: progress.search_id,
          offset: nextOffset,
          label: 'Yes, Sure',
          loadingLabel: 'Loading more...',
        },
        {
          type: 'dismiss_more_results',
          label: 'No, I am satisfied',
        },
      ],
    },
    created_at: new Date().toISOString(),
  }
}

/** Keep React state light — expand/complete payloads can include hundreds of ranked rows. */
function slimSearchProgressForState(progress) {
  if (!progress) {
    return progress
  }
  const partial = progress.partial_results || []
  return {
    ...progress,
    pending_other_city_results: [],
    pending_brand_fallback_same_city: [],
    pending_brand_fallback_other_city: [],
    final_results: progress.final_results
      ? {
          ...progress.final_results,
          // Cards already stream via partial_results / synthetic messages.
          results: partial,
        }
      : progress.final_results,
  }
}

function maybeAppendShowMorePrompt(progress, tracker, appendFn) {
  if (!progress?.has_more_results || isGoldSearch(progress)) {
    return false
  }
  const showMoreMessage = buildShowMoreResultsMessage(progress)
  if (!showMoreMessage) {
    return false
  }
  const offsetKey = showMoreMessage.message_id
  if (!tracker.showMoreOffsetsShown) {
    tracker.showMoreOffsetsShown = new Set()
  }
  if (tracker.showMoreOffsetsShown.has(offsetKey)) {
    return false
  }
  tracker.showMoreOffsetsShown.add(offsetKey)
  appendFn(showMoreMessage)
  return true
}

function buildOtherCityPromptMessage(progress) {
  const prompt = progress?.other_city_prompt
  if (!prompt || prompt.status !== 'awaiting' || !prompt.message || isGoldSearch(progress)) {
    return null
  }
  return {
    message_id: `other-city-prompt-${progress.search_id}`,
    role: 'assistant',
    kind: 'text',
    content: prompt.message,
    payload: {
      actions: [
        {
          type: 'expand_other_cities',
          searchId: progress.search_id,
          accept: true,
          label: 'Yes, show them',
          loadingLabel: 'Loading...',
        },
        {
          type: 'expand_other_cities',
          searchId: progress.search_id,
          accept: false,
          label: 'No, thanks',
        },
      ],
    },
    created_at: new Date().toISOString(),
  }
}

function buildBrandFallbackPromptMessage(progress) {
  const prompt = progress?.brand_fallback_prompt
  if (!prompt || prompt.status !== 'awaiting' || !prompt.message || isGoldSearch(progress)) {
    return null
  }
  return {
    message_id: `brand-fallback-prompt-${progress.search_id}`,
    role: 'assistant',
    kind: 'text',
    content: prompt.message,
    payload: {
      actions: [
        {
          type: 'expand_brand_fallback',
          searchId: progress.search_id,
          accept: true,
          label: 'Yes, show others',
          loadingLabel: 'Loading...',
        },
        {
          type: 'expand_brand_fallback',
          searchId: progress.search_id,
          accept: false,
          label: 'No, thanks',
        },
      ],
    },
    created_at: new Date().toISOString(),
  }
}

const initialMessages = [
  {
    message_id: 'welcome',
    role: 'assistant',
    kind: 'text',
    created_at: new Date().toISOString(),
    content:
      'Describe your requirement naturally. I’ll identify the product, find verified suppliers, compare prices, and get the best deal for you.',
  },
]

const initialState = {
  isLoading: false,
  sessions: [],
  sessionsLoading: true,
  sessionId: '',
  messages: initialMessages,
  suggestedReplies: [],
  searchProgress: null,
  activeSearchId: '',
  error: '',
  conversationState: null,
}

// Gold-locked host (gold.zwig.in): the whole chat is pinned to gold.
const goldSuggestedReplies = ['Gold 999 in Rajkot', 'Gold 22K in Mumbai', 'Silver 999 in Ahmedabad']
const goldInitialMessages = [
  {
    message_id: 'welcome',
    role: 'assistant',
    kind: 'text',
    created_at: new Date().toISOString(),
    content:
      'Tell me the gold you want and your city — e.g. “gold 999 in Rajkot”. I’ll pull live rates from vendors near you and show the available rates.',
    suggestedReplies: goldSuggestedReplies,
  },
]

function normalizeChatMessage(message) {
  if (!message) {
    return message
  }
  return {
    ...message,
    suggestedReplies: message.suggestedReplies || message.payload?.suggested_replies || [],
  }
}

function normalizeChatMessages(messages) {
  return (messages || []).map(normalizeChatMessage)
}

function reducer(state, action) {
  switch (action.type) {
    case 'SET_SESSIONS':
      return {
        ...state,
        sessions: action.payload,
        sessionsLoading: false,
      }
    case 'LOAD_SESSION':
      return {
        ...state,
        isLoading: false,
        error: '',
        sessionId: action.payload.sessionId,
        messages: normalizeChatMessages(
          action.payload.messages.length > 0 ? action.payload.messages : initialMessages,
        ),
        conversationState: action.payload.conversationState,
        suggestedReplies: [],
        searchProgress: null,
        activeSearchId: action.payload.activeSearchId || '',
      }
    case 'SEND_START':
      return {
        ...state,
        isLoading: true,
        error: '',
        searchProgress: state.searchProgress?.status === 'completed' ? null : state.searchProgress,
        messages: [
          ...state.messages,
          {
            message_id: `user-${Date.now()}`,
            role: 'user',
            kind: 'text',
            content: action.payload,
            created_at: new Date().toISOString(),
          },
        ],
      }
    case 'SEND_SUCCESS':
      return {
        ...state,
        isLoading: false,
        sessionId: action.payload.sessionId,
        suggestedReplies: action.payload.suggestedReplies,
        conversationState: action.payload.conversationState,
        searchProgress: action.payload.searchProgress ?? state.searchProgress,
        activeSearchId: action.payload.searchProgress?.search_id || state.activeSearchId,
        messages: [
          ...state.messages,
          {
            message_id: `assistant-${Date.now()}`,
            role: 'assistant',
            kind: 'text',
            content: action.payload.assistantMessage,
            suggestedReplies: action.payload.suggestedReplies || [],
            created_at: new Date().toISOString(),
          },
        ],
      }
    case 'SEARCH_PROGRESS_UPDATE':
      return {
        ...state,
        searchProgress: action.payload,
        activeSearchId: action.payload.search_id || state.activeSearchId,
      }
    case 'APPEND_MESSAGE':
      if (state.messages.some((message) => message.message_id === action.payload.message_id)) {
        return state
      }
      return {
        ...state,
        messages: [...state.messages, action.payload],
      }
    case 'SEND_ERROR':
      return {
        ...state,
        isLoading: false,
        error: action.payload,
      }
    case 'DEMO_SEARCH_SUCCESS':
      return {
        ...state,
        isLoading: false,
        error: '',
        sessionId: action.payload.sessionId,
        suggestedReplies: [],
        conversationState: action.payload.conversationState,
        messages: [...state.messages, ...action.payload.messages],
      }
    case 'DEMO_UNLOCK_SUCCESS':
      return {
        ...state,
        isLoading: false,
        error: '',
        messages: [...state.messages, ...action.payload.messages],
      }
    case 'RESET_SESSION':
      return {
        ...initialState,
        sessions: state.sessions,
        sessionsLoading: state.sessionsLoading,
        messages: [...initialMessages],
      }
    default:
      return state
  }
}

function ChatApp({ demoMode = false, goldLocked = false }) {
  const [state, dispatch] = useReducer(
    reducer,
    goldLocked
      ? { ...initialState, messages: goldInitialMessages }
      : initialState,
  )
  const [input, setInput] = useState('')
  // gold.zwig.in: ids of supplier quotes already rendered, so the responses
  // poll doesn't re-append them.
  const seenQuoteIdsRef = useRef(new Set())
  const [location, setLocation] = useState(DEFAULT_LOCATION)
  const [displayCurrency, setDisplayCurrency] = useState('INR')
  const [attachedImage, setAttachedImage] = useState(null)
  const [attachedImagePreview, setAttachedImagePreview] = useState('')
  const [isLocationPromptOpen, setIsLocationPromptOpen] = useState(false)
  const [locationError, setLocationError] = useState('')
  const [isResolvingLocation, setIsResolvingLocation] = useState(false)
  const [locationAnnouncementShown, setLocationAnnouncementShown] = useState(false)
  const [demoSearch, setDemoSearch] = useState(null)
  const [demoUnlocking, setDemoUnlocking] = useState(false)
  const [messageActionLoading, setMessageActionLoading] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const messagesEndRef = useRef(null)
  const imageInputRef = useRef(null)
  const demoProgressTimersRef = useRef([])
  const delayedSupplierTimersRef = useRef([])
  // Prevent duplicate expand/more clicks from the same prompt (buttons stay on screen).
  const handledExpandKeysRef = useRef(new Set())
  const progressTrackerRef = useRef({
    searchId: '',
    stepStates: {},
    resultIds: new Set(),
    vendorIds: new Set(),
    finalMessageAdded: false,
    delayedStatusScheduled: false,
    otherCityPromptShown: false,
    brandFallbackPromptShown: false,
    waitStartShown: false,
    waitNudgeShown: false,
    waitTimeoutShown: false,
    showMoreOffsetsShown: new Set(),
    searchStartedAt: 0,
    lastActivityAt: 0,
  })
  const searchActive =
    Boolean(state.searchProgress?.search_id) &&
    !['completed', 'failed'].includes(state.searchProgress?.status)

  useEffect(() => {
    const savedLocation = window.localStorage.getItem(LOCATION_STORAGE_KEY)
    if (savedLocation) {
      setLocation(savedLocation)
    } else {
      setLocation(DEFAULT_LOCATION)
      window.localStorage.setItem(LOCATION_STORAGE_KEY, DEFAULT_LOCATION)
    }
    const savedCurrency = window.localStorage.getItem(DISPLAY_CURRENCY_STORAGE_KEY)
    if (DISPLAY_CURRENCIES.includes(savedCurrency)) {
      setDisplayCurrency(savedCurrency)
    }
  }, [])

  useEffect(() => {
    return () => {
      demoProgressTimersRef.current.forEach((timerId) => window.clearTimeout(timerId))
      demoProgressTimersRef.current = []
      delayedSupplierTimersRef.current.forEach((timerId) => window.clearTimeout(timerId))
      delayedSupplierTimersRef.current = []
      if (attachedImagePreview) {
        window.URL.revokeObjectURL(attachedImagePreview)
      }
    }
  }, [attachedImagePreview])

  // Do not auto-scroll when progressive search results / status messages arrive —
  // the user may be reading earlier cards. Only scroll after they send a message.
  const scrollChatToBottom = () => {
    requestAnimationFrame(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    })
  }

  const headline = useMemo(() => {
    const product = state.conversationState?.display_product || state.conversationState?.product
    if (product) return `Working on ${product}`
    return goldLocked ? 'Find the best live gold rate near you' : 'What can I help you find today?'
  }, [state.conversationState?.display_product, state.conversationState?.product, goldLocked])

  const apiBaseUrl = (import.meta.env.VITE_API_URL || (import.meta.env.PROD ? '' : 'http://localhost:8000')).replace(/\/$/, '')
  const deviceId = useMemo(() => getOrCreateDeviceId(), [])
  const composerDisabled = state.isLoading || demoUnlocking
  const displayCurrencyHeaders = useMemo(
    () => ({ 'X-Display-Currency': displayCurrency }),
    [displayCurrency],
  )

  const handleDisplayCurrencyChange = (value) => {
    const nextCurrency = DISPLAY_CURRENCIES.includes(value) ? value : 'INR'
    setDisplayCurrency(nextCurrency)
    window.localStorage.setItem(DISPLAY_CURRENCY_STORAGE_KEY, nextCurrency)
    trackAmplitudeEvent('Display Currency Changed', { currency: nextCurrency })
  }

  const handleImageSelected = (event) => {
    const file = event.target.files?.[0]
    if (!file) {
      return
    }
    if (!file.type.startsWith('image/')) {
      dispatch({ type: 'SEND_ERROR', payload: 'Please attach an image file.' })
      return
    }
    if (file.size > 20 * 1024 * 1024) {
      dispatch({ type: 'SEND_ERROR', payload: 'Please attach an image under 20 MB.' })
      return
    }
    if (attachedImagePreview) {
      window.URL.revokeObjectURL(attachedImagePreview)
    }
    setAttachedImage(file)
    setAttachedImagePreview(window.URL.createObjectURL(file))
    trackAmplitudeEvent('Product Image Attached', {
      fileType: file.type,
      fileSize: file.size,
    })
  }

  const clearAttachedImage = () => {
    if (attachedImagePreview) {
      window.URL.revokeObjectURL(attachedImagePreview)
    }
    setAttachedImage(null)
    setAttachedImagePreview('')
    if (imageInputRef.current) {
      imageInputRef.current.value = ''
    }
  }

  const fetchSessions = async () => {
    if (demoMode) {
      dispatch({ type: 'SET_SESSIONS', payload: [] })
      return
    }
    try {
      const response = await fetch(`${apiBaseUrl}/api/chat/sessions`, {
        headers: { 'X-Device-Id': deviceId },
      })
      if (!response.ok) {
        throw new Error('Failed to load chat sessions.')
      }
      const payload = await response.json()
      dispatch({ type: 'SET_SESSIONS', payload })
    } catch (error) {
      console.error(error)
      dispatch({ type: 'SET_SESSIONS', payload: [] })
    }
  }

  useEffect(() => {
    fetchSessions()
  }, [apiBaseUrl, demoMode, deviceId])

  useEffect(() => {
    if (demoMode) {
      return undefined
    }
    const searchId = state.activeSearchId
    const status = state.searchProgress?.status

    if (!searchId || status === 'completed' || status === 'failed') {
      return undefined
    }

    const intervalId = window.setInterval(async () => {
      try {
        const response = await fetch(`${apiBaseUrl}/api/chat/search/${searchId}`, {
          headers: displayCurrencyHeaders,
        })
        if (!response.ok) {
          throw new Error(`Search status failed with status ${response.status}`)
        }
        const payload = await response.json()
        dispatch({ type: 'SEARCH_PROGRESS_UPDATE', payload })
      } catch (error) {
        console.error(error)
      }
    }, 2000)

    return () => window.clearInterval(intervalId)
  }, [apiBaseUrl, demoMode, displayCurrencyHeaders, state.activeSearchId, state.searchProgress?.status])

  // Poll for supplier quotes ops posted on the dashboard and surface them
  // in-session. Runs on the gold-locked host, and on the normal flow once a
  // search has launched (a fulfilment request is opened in parallel there).
  useEffect(() => {
    if (!state.sessionId || (!goldLocked && !state.activeSearchId)) {
      return undefined
    }
    // Seed already-seen ids from loaded history so a reload doesn't duplicate.
    for (const m of state.messages) {
      if (typeof m.message_id === 'string' && m.message_id.startsWith('resp-')) {
        seenQuoteIdsRef.current.add(m.message_id.slice(5))
      }
    }
    const fetchResponses = async () => {
      try {
        const res = await fetch(`${apiBaseUrl}/api/gold-requests/session/${state.sessionId}/responses`)
        if (!res.ok) return
        const payload = await res.json()
        for (const r of payload.responses || []) {
          if (!r.response_id || seenQuoteIdsRef.current.has(r.response_id)) continue
          seenQuoteIdsRef.current.add(r.response_id)
          const content = `💬 ${r.supplier}${r.price ? ` — ${r.price}` : ''}${r.note ? `\n${r.note}` : ''}${r.url ? `\n${r.url}` : ''}`
          appendSyntheticMessage({
            message_id: `resp-${r.response_id}`,
            role: 'assistant',
            kind: 'text',
            created_at: r.created_at || new Date().toISOString(),
            content,
          })
        }
      } catch (error) {
        console.error(error)
      }
    }
    fetchResponses()
    const intervalId = window.setInterval(fetchResponses, 6000)
    return () => window.clearInterval(intervalId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [goldLocked, apiBaseUrl, state.sessionId, state.activeSearchId])

  const persistSyntheticMessage = async (sessionId, message) => {
    if (demoMode) {
      return
    }
    if (!sessionId) {
      return
    }
    try {
      await fetch(`${apiBaseUrl}/api/chat/sessions/${sessionId}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message_id: message.message_id,
          role: message.role,
          content: message.content,
          kind: message.kind,
          payload: message.payload || null,
        }),
      })
      fetchSessions()
    } catch (error) {
      console.error(error)
    }
  }

  const appendSyntheticMessage = (message, sessionId = state.sessionId) => {
    dispatch({ type: 'APPEND_MESSAGE', payload: message })
    persistSyntheticMessage(sessionId, message)
  }

  const clearDelayedSupplierTimers = () => {
    delayedSupplierTimersRef.current.forEach((timerId) => window.clearTimeout(timerId))
    delayedSupplierTimersRef.current = []
  }

  const scheduleSupplierCheckingMessages = (progress, sessionId) => {
    const tracker = progressTrackerRef.current
    if (tracker.delayedStatusScheduled) {
      return
    }
    tracker.delayedStatusScheduled = true

    const messages = [
      {
        delay: 12_000,
        suffix: 'checking-more',
        content: 'I’m checking a few more suppliers now.',
      },
      {
        delay: 42_000,
        suffix: 'still-checking',
        content: 'I’m still checking additional supplier options.',
      },
    ]

    messages.forEach((item) => {
      const timerId = window.setTimeout(() => {
        if (progressTrackerRef.current.searchId !== progress.search_id) {
          return
        }
        appendSyntheticMessage(
          {
            message_id: `supplier-status-${progress.search_id}-${item.suffix}`,
            role: 'assistant',
            kind: 'status',
            content: item.content,
            created_at: new Date().toISOString(),
          },
          sessionId,
        )
      }, item.delay)
      delayedSupplierTimersRef.current.push(timerId)
    })
  }

  const scheduleDelayedSupplierResults = (progress, results, sessionId) => {
    if (!results.length) {
      return
    }

    scheduleSupplierCheckingMessages(progress, sessionId)
    let offset = SUPPLIER_LISTING_INITIAL_DELAY_MS
    results.forEach((result, index) => {
      if (index > 0) {
        offset += supplierListingGapMs()
      }

      const timerId = window.setTimeout(() => {
        if (progressTrackerRef.current.searchId !== progress.search_id) {
          return
        }
        appendSyntheticMessage(
          {
            message_id: `delayed-supplier-${progress.search_id}-${result.id}`,
            role: 'assistant',
            kind: 'results',
            content: 'I found another supplier option.',
            payload: { results: [result] },
            created_at: new Date().toISOString(),
          },
          sessionId,
        )
      }, offset)
      delayedSupplierTimersRef.current.push(timerId)
    })
  }

  const fetchSearchSnapshot = async (searchId) => {
    if (!searchId) {
      return
    }
    try {
      const response = await fetch(`${apiBaseUrl}/api/chat/search/${searchId}`, {
        headers: displayCurrencyHeaders,
      })
      if (!response.ok) {
        throw new Error(`Search status failed with status ${response.status}`)
      }
      const payload = await response.json()
      dispatch({ type: 'SEARCH_PROGRESS_UPDATE', payload })
    } catch (error) {
      console.error(error)
    }
  }

  useEffect(() => {
    if (demoMode) {
      return
    }
    const progress = state.searchProgress
    if (!progress || !state.sessionId) {
      return
    }

    if (progressTrackerRef.current.searchId !== progress.search_id) {
      clearDelayedSupplierTimers()
      const initialMessages = buildInitialSearchMessages(progress)
      if (!isGoldSearch(progress) && !['completed', 'failed'].includes(progress.status)) {
        initialMessages.push({
          message_id: `search-wait-start-${progress.search_id}`,
          role: 'assistant',
          kind: 'status',
          content: SEARCH_WAIT_START,
          created_at: new Date().toISOString(),
        })
      }
      const initialResults = progress.partial_results || []
      const { immediate: immediateInitialResults, delayed: delayedInitialResults } = splitTimedResults(
        progress,
        initialResults,
      )
      if (immediateInitialResults.length > 0) {
        initialMessages.push(...buildResultMessages(progress, immediateInitialResults))
      }
      const seenInitialIds = new Set(initialResults.map((result) => result.id))
      const unseenGoldFinal = isGoldSearch(progress)
        ? unseenGoldFinalResults(progress, seenInitialIds)
        : []
      unseenGoldFinal.forEach((result) => seenInitialIds.add(result.id))
      if (unseenGoldFinal.length > 0) {
        initialMessages.push(...buildResultMessages(progress, unseenGoldFinal))
      }
      if (progress.final_results && isGoldSearch(progress)) {
        initialMessages.push(buildFinalSearchMessage(progress))
      }
      if (progress.final_results && progress.has_more_results && !isGoldSearch(progress)) {
        const showMoreMessage = buildShowMoreResultsMessage(progress)
        if (showMoreMessage) {
          initialMessages.push(showMoreMessage)
        }
      }
      let brandFallbackShown = false
      if (progress.brand_fallback_prompt?.status === 'awaiting') {
        const brandFallbackMessage = buildBrandFallbackPromptMessage(progress)
        if (brandFallbackMessage) {
          initialMessages.push(brandFallbackMessage)
          brandFallbackShown = true
        }
      } else if (progress.other_city_prompt?.status === 'awaiting') {
        const otherCityMessage = buildOtherCityPromptMessage(progress)
        if (otherCityMessage) {
          initialMessages.push(otherCityMessage)
        }
      }
      progressTrackerRef.current = {
        searchId: progress.search_id,
        stepStates: Object.fromEntries(progress.steps.map((step) => [step.id, step.status])),
        resultIds: seenInitialIds,
        vendorIds: new Set((progress.discovered_vendors || []).map(getVendorKey).filter(Boolean)),
        finalMessageAdded: Boolean(progress.final_results),
        delayedStatusScheduled: false,
        otherCityPromptShown: Boolean(
          progress.other_city_prompt?.status === 'awaiting' && !brandFallbackShown,
        ),
        brandFallbackPromptShown: brandFallbackShown,
        waitStartShown: true,
        waitNudgeShown: false,
        waitTimeoutShown: false,
        showMoreOffsetsShown: new Set(
          progress.has_more_results && progress.final_results
            ? [`show-more-results-${progress.search_id}-${progress.next_result_offset ?? 5}`]
            : [],
        ),
        searchStartedAt: Date.now(),
        lastActivityAt: Date.now(),
      }
      initialMessages.forEach((message) => {
        appendSyntheticMessage(message)
      })
      scheduleDelayedSupplierResults(progress, delayedInitialResults, state.sessionId)
      trackAmplitudeEvent('Search Started', {
        searchId: progress.search_id,
        searchQuery: progress.query?.product || '',
        searchCategory: progress.query?.category || '',
        searchLocation: progress.query?.location || '',
        stepCount: progress.steps.length,
        discoveredVendorCount: progress.discovered_vendors?.length || 0,
        onlinePlatformCount: progress.online_platforms?.length || 0,
      })
      return
    }

    const tracker = progressTrackerRef.current
    const nextMessages = []
    let sawActivity = false

    for (const step of progress.steps) {
      const previousStatus = tracker.stepStates[step.id]
      if (step.status !== previousStatus && step.status !== 'pending') {
        tracker.stepStates[step.id] = step.status
        sawActivity = true
        nextMessages.push({
          message_id: `progress-${progress.search_id}-${step.id}-${step.status}`,
          role: 'assistant',
          kind: 'status',
          content: isGoldSearch(progress)
            ? buildGoldProgressMessage(step)
            : step.detail ||
              (step.status === 'failed' ? `${step.label} hit a problem.` : `${step.label} ${step.status}.`),
          created_at: new Date().toISOString(),
        })
      }
    }

    const currentVendorIds = new Set((progress.discovered_vendors || []).map(getVendorKey).filter(Boolean))
    const hasNewVendors = [...currentVendorIds].some((vendorId) => !tracker.vendorIds.has(vendorId))
    if (!isGoldSearch(progress) && hasNewVendors && currentVendorIds.size > 0) {
      const shortlistMessage = buildOfflineShortlistMessage(progress, progress.discovered_vendors || [])
      if (shortlistMessage) {
        nextMessages.push(shortlistMessage)
      }
      tracker.vendorIds = currentVendorIds
    }

    let newResults = (progress.partial_results || []).filter((result) => !tracker.resultIds.has(result.id))
    if (!isGoldSearch(progress)) {
      const visibleCap = progress.visible_result_count || 5
      const remainingInitialSlots = Math.max(0, visibleCap - tracker.resultIds.size)
      newResults = newResults.slice(0, remainingInitialSlots)
    }
    if (newResults.length > 0) {
      sawActivity = true
      newResults.forEach((result) => tracker.resultIds.add(result.id))
      const { immediate: immediateNewResults, delayed: delayedNewResults } = splitTimedResults(progress, newResults)
      trackAmplitudeEvent('Search Results Received', {
        searchId: progress.search_id,
        searchQuery: progress.query?.product || '',
        searchCategory: progress.query?.category || '',
        searchLocation: progress.query?.location || '',
        resultCount: newResults.length,
        onlineCount: newResults.filter((result) => result.source_type === 'online').length,
        offlineCount: newResults.filter((result) => result.source_type === 'offline').length,
      })
      if (immediateNewResults.length > 0) {
        nextMessages.push(...buildResultMessages(progress, immediateNewResults))
      }
      scheduleDelayedSupplierResults(progress, delayedNewResults, state.sessionId)
    }

    if (sawActivity || hasNewVendors) {
      tracker.lastActivityAt = Date.now()
    }

    if (progress.final_results && !tracker.finalMessageAdded) {
      tracker.finalMessageAdded = true
      tracker.lastActivityAt = Date.now()
      trackAmplitudeEvent('Search Completed', {
        searchId: progress.search_id,
        searchQuery: progress.query?.product || '',
        searchCategory: progress.query?.category || '',
        searchLocation: progress.query?.location || '',
        resultCount: progress.final_results.results?.length || 0,
        onlineCount: progress.final_results.online_count || 0,
        offlineCount: progress.final_results.offline_count || 0,
        totalTimeSeconds: progress.final_results.total_time_seconds,
      })
      if (isGoldSearch(progress)) {
        const unseenFinal = unseenGoldFinalResults(progress, tracker.resultIds)
        unseenFinal.forEach((result) => tracker.resultIds.add(result.id))
        if (unseenFinal.length > 0) {
          nextMessages.push(...buildResultMessages(progress, unseenFinal))
        }
        nextMessages.push(buildFinalSearchMessage(progress))
      } else if (progress.has_more_results) {
        const showMoreMessage = buildShowMoreResultsMessage(progress)
        if (showMoreMessage) {
          if (!tracker.showMoreOffsetsShown) {
            tracker.showMoreOffsetsShown = new Set()
          }
          tracker.showMoreOffsetsShown.add(showMoreMessage.message_id)
          nextMessages.push(showMoreMessage)
        }
      }
    } else if (
      progress.status === 'completed' &&
      progress.has_more_results &&
      !isGoldSearch(progress)
    ) {
      // After other-city / brand-fallback expand, has_more can flip true once
      // finalMessageAdded is already set — still surface the prompt once.
      const showMoreMessage = buildShowMoreResultsMessage(progress)
      if (showMoreMessage) {
        if (!tracker.showMoreOffsetsShown) {
          tracker.showMoreOffsetsShown = new Set()
        }
        if (!tracker.showMoreOffsetsShown.has(showMoreMessage.message_id)) {
          tracker.showMoreOffsetsShown.add(showMoreMessage.message_id)
          nextMessages.push(showMoreMessage)
        }
      }
    } else if (progress.status === 'failed' && !tracker.finalMessageAdded) {
      tracker.finalMessageAdded = true
      trackAmplitudeEvent('Search Failed', {
        searchId: progress.search_id,
        searchQuery: progress.query?.product || '',
        searchCategory: progress.query?.category || '',
        searchLocation: progress.query?.location || '',
        hasError: Boolean(progress.error),
      })
      nextMessages.push({
        message_id: `failed-${progress.search_id}`,
        role: 'assistant',
        kind: 'status',
        content: progress.error || 'The search stopped before I could finish it.',
        created_at: new Date().toISOString(),
      })
    }

    if (
      !tracker.brandFallbackPromptShown &&
      progress.status === 'completed' &&
      progress.brand_fallback_prompt?.status === 'awaiting'
    ) {
      const brandFallbackMessage = buildBrandFallbackPromptMessage(progress)
      if (brandFallbackMessage) {
        tracker.brandFallbackPromptShown = true
        nextMessages.push(brandFallbackMessage)
      }
    }

    if (
      !tracker.otherCityPromptShown &&
      progress.status === 'completed' &&
      progress.other_city_prompt?.status === 'awaiting' &&
      progress.brand_fallback_prompt?.status !== 'awaiting'
    ) {
      const otherCityMessage = buildOtherCityPromptMessage(progress)
      if (otherCityMessage) {
        tracker.otherCityPromptShown = true
        nextMessages.push(otherCityMessage)
      }
    }

    nextMessages.forEach((message) => {
      appendSyntheticMessage(message)
    })
  }, [state.searchProgress, state.sessionId])

  // Quiet-gap nudge + long-poll timeout while a search is still running (WhatsApp parity).
  useEffect(() => {
    if (demoMode || !searchActive || isGoldSearch(state.searchProgress)) {
      return undefined
    }
    const intervalId = window.setInterval(() => {
      const tracker = progressTrackerRef.current
      if (
        !tracker.waitNudgeShown &&
        tracker.lastActivityAt &&
        Date.now() - tracker.lastActivityAt >= SEARCH_WAIT_SILENCE_MS
      ) {
        tracker.waitNudgeShown = true
        appendSyntheticMessage({
          message_id: `search-wait-nudge-${tracker.searchId}`,
          role: 'assistant',
          kind: 'status',
          content: SEARCH_WAIT_NUDGE,
          created_at: new Date().toISOString(),
        })
      }
      if (
        !tracker.waitTimeoutShown &&
        tracker.searchStartedAt &&
        Date.now() - tracker.searchStartedAt >= SEARCH_WAIT_TIMEOUT_MS
      ) {
        tracker.waitTimeoutShown = true
        appendSyntheticMessage({
          message_id: `search-wait-timeout-${tracker.searchId}`,
          role: 'assistant',
          kind: 'status',
          content: SEARCH_WAIT_TIMEOUT,
          created_at: new Date().toISOString(),
        })
      }
    }, 5000)
    return () => window.clearInterval(intervalId)
  }, [demoMode, searchActive, state.searchProgress?.search_id])

  const rememberLocation = (value) => {
    setLocation(value)
    setIsLocationPromptOpen(false)
    setLocationError('')
    window.localStorage.setItem(LOCATION_STORAGE_KEY, value)
  }

  const handleConfirmManualLocation = (value) => {
    trackAmplitudeEvent('Location Confirmed', { method: 'manual' })
    rememberLocation(value)
  }

  const handleUseCurrentLocation = async () => {
    trackAmplitudeEvent('Location Detection Requested')

    if (!navigator.geolocation) {
      trackAmplitudeEvent('Location Detection Failed', { reason: 'unsupported' })
      setLocationError('This browser does not support location access. Enter your city or area manually.')
      return
    }

    setIsResolvingLocation(true)
    setLocationError('')

    navigator.geolocation.getCurrentPosition(
      async (position) => {
        try {
          const response = await fetch(`${apiBaseUrl}/api/location/resolve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              latitude: position.coords.latitude,
              longitude: position.coords.longitude,
            }),
          })

          if (!response.ok) {
            throw new Error('Could not resolve your current location.')
          }

          const payload = await response.json()
          trackAmplitudeEvent('Location Confirmed', { method: 'geolocation' })
          rememberLocation(payload.location)
        } catch {
          trackAmplitudeEvent('Location Detection Failed', { reason: 'resolve_failed' })
          setLocationError('I could not convert your current position into a search area. Try entering it manually.')
        } finally {
          setIsResolvingLocation(false)
        }
      },
      () => {
        trackAmplitudeEvent('Location Detection Failed', { reason: 'permission_or_timeout' })
        setIsResolvingLocation(false)
        setLocationError('Location permission was blocked. Enter your city or area manually.')
      },
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 60000 },
    )
  }

  const clearDemoProgressTimers = () => {
    demoProgressTimersRef.current.forEach((timerId) => window.clearTimeout(timerId))
    demoProgressTimersRef.current = []
  }

  const appendDemoStatus = (messageId, content) => {
    dispatch({
      type: 'APPEND_MESSAGE',
      payload: {
        message_id: messageId,
        role: 'assistant',
        kind: 'status',
        content,
        created_at: new Date().toISOString(),
      },
    })
  }

  const scheduleDemoStatus = (messageId, content, delay) => {
    const timerId = window.setTimeout(() => {
      appendDemoStatus(messageId, content)
    }, delay)
    demoProgressTimersRef.current.push(timerId)
  }

  const runDemoSearch = async (trimmed, source, isStartingConversation) => {
    const sessionId =
      state.sessionId ||
      window.crypto?.randomUUID?.() ||
      `demo-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`

    trackAmplitudeEvent('Demo Premium Search Started', {
      searchQuery: trimmed,
      source,
      hasLocation: Boolean(location),
    })

    const localSearchId = `demo-progress-${Date.now()}`
    clearDemoProgressTimers()
    appendDemoStatus(
      `${localSearchId}-started`,
      `I’ve started the search for ${trimmed}. I’m checking market listings and callable vendors in parallel now.`,
    )
    scheduleDemoStatus(
      `${localSearchId}-online`,
      'I’m checking market listings for live prices and direct product links.',
      1400,
    )
    scheduleDemoStatus(
      `${localSearchId}-offline`,
      'I’m also checking nearby offline options for a better local price.',
      4200,
    )

    const controller = new AbortController()
    const timeoutId = window.setTimeout(() => controller.abort(), 120000)

    try {
      const response = await fetch(`${apiBaseUrl}/api/demo-premium/search`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Device-ID': getOrCreateDeviceId() },
        body: JSON.stringify({
          query: trimmed,
          location: location || undefined,
        }),
        signal: controller.signal,
      })

      const payload = await response.json().catch(() => null)
      if (!response.ok) {
        throw new Error(payload?.detail || `Demo search failed with status ${response.status}`)
      }

      setDemoSearch(payload)

      if (isStartingConversation) {
        trackAmplitudeEvent('Conversation Started', {
          source,
          hasLocation: Boolean(location),
          hasSearchProgress: false,
          flow: 'demo-premium',
        })
      }
      trackAmplitudeEvent('Demo Premium Search Completed', {
        searchId: payload.search_id,
        onlineResultCount: payload.online_results?.length || 0,
        savingsPercent: payload.savings?.savings_percent,
      })
      clearDemoProgressTimers()

      const onlineCount = payload.online_results?.length || 0
      const previewCount = payload.locked_preview?.length || 0
      const savingsPercent = payload.savings?.savings_percent
      const amount = formatPrice((payload.amount_paise || 900) / 100)
      const messages = []

      if (onlineCount > 0) {
        messages.push({
          message_id: `demo-online-${payload.search_id}`,
          role: 'assistant',
          kind: 'results',
          content:
            onlineCount === 1
              ? `I found an online price on ${payload.online_results[0].name}. Here’s the direct product link.`
              : `I found ${onlineCount} online price matches with direct product links.`,
          payload: { results: payload.online_results },
          created_at: new Date().toISOString(),
        })
      }

      if (previewCount > 0) {
        messages.push({
          message_id: `demo-offline-preview-${payload.search_id}`,
          role: 'assistant',
          kind: 'results',
          content:
            previewCount === 1
              ? 'I found a local vendor quote below the online price. Store details are locked until payment.'
              : `I found ${previewCount} local vendor quotes below the online price. Store details are locked until payment.`,
          payload: { results: payload.locked_preview },
          created_at: new Date().toISOString(),
        })
      }

      messages.push({
        message_id: `demo-paywall-${payload.search_id}`,
        role: 'assistant',
        kind: 'status',
        content: `Best local quote is ${savingsPercent}% lower than the best online price. Unlock the vendor names, phone numbers, and full quote notes for ${amount}.`,
        payload: {
          action: {
            type: 'demo_unlock',
            label: `Unlock for ${amount}`,
            loadingLabel: 'Opening payment...',
          },
        },
        created_at: new Date().toISOString(),
      })

      dispatch({
        type: 'DEMO_SEARCH_SUCCESS',
        payload: {
          sessionId,
          conversationState: {
            product: payload.product,
            category: payload.category,
            location: payload.location,
          },
          messages,
        },
      })
    } catch (error) {
      clearDemoProgressTimers()
      trackAmplitudeEvent('Demo Premium Search Failed', {
        source,
        reason: error.name === 'AbortError' ? 'timeout' : 'request_failed',
      })
      dispatch({
        type: 'SEND_ERROR',
        payload:
          error.name === 'AbortError'
            ? 'This step took longer than expected. Please try again or narrow the request.'
            : error.message || 'I hit a problem while continuing the conversation. Please try that message again.',
      })
    } finally {
      window.clearTimeout(timeoutId)
    }
  }

  const verifyDemoUnlock = async (paymentPayload) => {
    if (!demoSearch) {
      return
    }

    const response = await fetch(`${apiBaseUrl}/api/demo-premium/verify-payment`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Device-ID': getOrCreateDeviceId() },
      body: JSON.stringify({
        search_id: demoSearch.search_id,
        query: demoSearch.query,
        location: demoSearch.location,
        category: demoSearch.category,
        best_online_price: demoSearch.savings?.best_online_price,
        savings_percent: demoSearch.savings?.savings_percent,
        ...paymentPayload,
      }),
    })

    if (!response.ok) {
      throw new Error('Payment verification failed.')
    }

    const payload = await response.json()
    dispatch({
      type: 'DEMO_UNLOCK_SUCCESS',
      payload: {
        messages: [
          {
            message_id: `demo-unlocked-${demoSearch.search_id}`,
            role: 'assistant',
            kind: 'results',
            content: 'Unlocked. Here are the local vendor details and full quote notes.',
            payload: { results: payload.full_results || [] },
            created_at: new Date().toISOString(),
          },
        ],
      },
    })
    trackAmplitudeEvent('Demo Premium Results Unlocked', {
      searchId: demoSearch.search_id,
      resultCount: payload.full_results?.length || 0,
      demoMode: Boolean(paymentPayload.demo_mode),
    })
  }

  const handleDemoUnlock = async () => {
    if (!demoSearch || demoUnlocking) {
      return
    }

    setDemoUnlocking(true)
    trackAmplitudeEvent('Demo Payment Started', { searchId: demoSearch.search_id })
    let checkoutOpened = false

    try {
      const response = await fetch(`${apiBaseUrl}/api/demo-premium/create-order`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Device-ID': getOrCreateDeviceId() },
        body: JSON.stringify({
          search_id: demoSearch.search_id,
          query: demoSearch.query,
        }),
      })

      if (!response.ok) {
        throw new Error('Could not create payment order.')
      }

      const order = await response.json()

      if (order.demo_mode) {
        await new Promise((resolve) => window.setTimeout(resolve, 550))
        await verifyDemoUnlock({
          demo_mode: true,
          razorpay_order_id: order.order_id,
          razorpay_payment_id: `demo_pay_${Date.now()}`,
          razorpay_signature: 'demo',
        })
        return
      }

      await loadRazorpayCheckout()

      const checkout = new window.Razorpay({
        key: order.razorpay_key_id,
        amount: order.amount,
        currency: order.currency,
        name: 'Zwig',
        description: 'Unlock offline vendor quotes',
        order_id: order.order_id,
        notes: {
          search_id: demoSearch.search_id,
          flow: 'demo-premium',
        },
        theme: {
          color: '#0f172a',
        },
        handler: async (paymentResponse) => {
          try {
            await verifyDemoUnlock(paymentResponse)
          } catch (error) {
            console.error(error)
            dispatch({ type: 'SEND_ERROR', payload: 'Payment was received but verification failed. Please retry the unlock.' })
            trackAmplitudeEvent('Demo Payment Failed', { reason: 'verification_failed' })
          } finally {
            setDemoUnlocking(false)
          }
        },
        modal: {
          ondismiss: () => {
            setDemoUnlocking(false)
            trackAmplitudeEvent('Demo Payment Dismissed', { searchId: demoSearch.search_id })
          },
        },
      })

      checkout.on('payment.failed', (paymentError) => {
        console.error(paymentError)
        setDemoUnlocking(false)
        dispatch({ type: 'SEND_ERROR', payload: 'Payment failed. Please try again.' })
        trackAmplitudeEvent('Demo Payment Failed', { reason: 'checkout_failed' })
      })

      checkout.open()
      checkoutOpened = true
    } catch (error) {
      console.error(error)
      dispatch({ type: 'SEND_ERROR', payload: 'Could not open the unlock flow. Please try again.' })
      trackAmplitudeEvent('Demo Payment Failed', { reason: 'order_or_checkout_failed' })
    } finally {
      if (!checkoutOpened) {
        setDemoUnlocking(false)
      }
    }
  }

  const handleMessageAction = async (action) => {
    if (action?.type === 'demo_unlock') {
      handleDemoUnlock()
      return
    }
    if (action?.type === 'dismiss_more_results') {
      appendSyntheticMessage({
        message_id: `show-more-dismissed-${Date.now()}`,
        role: 'assistant',
        kind: 'status',
        content: DISMISS_MORE_RESULTS_MESSAGE,
        created_at: new Date().toISOString(),
      })
      return
    }

    const expandKey =
      action?.searchId && action?.type
        ? `${action.type}:${action.searchId}:${action.accept === false ? 'no' : 'yes'}:${action.offset ?? ''}`
        : null
    if (
      expandKey &&
      (action.type === 'expand_brand_fallback' ||
        action.type === 'expand_other_cities' ||
        action.type === 'show_more_results')
    ) {
      if (handledExpandKeysRef.current.has(expandKey) || messageActionLoading) {
        return
      }
      handledExpandKeysRef.current.add(expandKey)
    }

    if (action?.type === 'expand_brand_fallback' && action.searchId) {
      setMessageActionLoading(true)
      try {
        const response = await fetch(
          `${apiBaseUrl}/api/chat/search/${action.searchId}/expand-brand-fallback?accept=${action.accept ? 'true' : 'false'}`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              ...displayCurrencyHeaders,
            },
          },
        )
        if (!response.ok) {
          const err = new Error(`Expand brand fallback failed with status ${response.status}`)
          err.status = response.status
          throw err
        }
        const payload = await response.json()
        if (!action.accept) {
          appendSyntheticMessage({
            message_id: `brand-fallback-declined-${action.searchId}`,
            role: 'assistant',
            kind: 'status',
            content: 'Okay — I’ll stick with the online brand options for now.',
            created_at: new Date().toISOString(),
          })
          dispatch({
            type: 'SEARCH_PROGRESS_UPDATE',
            payload: slimSearchProgressForState(payload),
          })
          return
        }
        const tracker = progressTrackerRef.current
        const newResults = (payload.partial_results || []).filter((result) => !tracker.resultIds.has(result.id))
        newResults.forEach((result) => tracker.resultIds.add(result.id))
        const brandFallbackIntro =
          newResults.length > 0
            ? `Here are ${newResults.length} other-brand supplier quote${newResults.length === 1 ? '' : 's'} in your city.`
            : 'Checking other-brand suppliers.'
        if (newResults.length > 0) {
          appendSyntheticMessage({
            message_id: `brand-fallback-results-${action.searchId}`,
            role: 'assistant',
            kind: 'results',
            content: brandFallbackIntro,
            payload: { results: newResults },
            created_at: new Date().toISOString(),
          })
        } else {
          appendSyntheticMessage({
            message_id: `brand-fallback-accepted-${action.searchId}`,
            role: 'assistant',
            kind: 'status',
            content: brandFallbackIntro,
            created_at: new Date().toISOString(),
          })
        }
        maybeAppendShowMorePrompt(payload, progressTrackerRef.current, appendSyntheticMessage)
        if (payload.other_city_prompt?.status === 'awaiting') {
          const otherCityMessage = buildOtherCityPromptMessage(payload)
          if (otherCityMessage) {
            progressTrackerRef.current.otherCityPromptShown = true
            appendSyntheticMessage(otherCityMessage)
          }
        }
        dispatch({
          type: 'SEARCH_PROGRESS_UPDATE',
          payload: slimSearchProgressForState(payload),
        })
      } catch (error) {
        console.error(error)
        if (expandKey) {
          handledExpandKeysRef.current.delete(expandKey)
        }
        appendSyntheticMessage({
          message_id: `brand-fallback-error-${Date.now()}`,
          role: 'assistant',
          kind: 'status',
          content: expandErrorMessage(error),
          created_at: new Date().toISOString(),
        })
      } finally {
        setMessageActionLoading(false)
      }
      return
    }
    if (action?.type === 'expand_other_cities' && action.searchId) {
      setMessageActionLoading(true)
      try {
        const response = await fetch(
          `${apiBaseUrl}/api/chat/search/${action.searchId}/expand-other-cities?accept=${action.accept ? 'true' : 'false'}`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              ...displayCurrencyHeaders,
            },
          },
        )
        if (!response.ok) {
          const err = new Error(`Expand other cities failed with status ${response.status}`)
          err.status = response.status
          throw err
        }
        const payload = await response.json()
        if (!action.accept) {
          appendSyntheticMessage({
            message_id: `other-city-declined-${action.searchId}`,
            role: 'assistant',
            kind: 'status',
            content: 'Okay — I’ll stick with suppliers in your city.',
            created_at: new Date().toISOString(),
          })
          dispatch({
            type: 'SEARCH_PROGRESS_UPDATE',
            payload: slimSearchProgressForState(payload),
          })
          return
        }
        const tracker = progressTrackerRef.current
        const newResults = (payload.partial_results || []).filter((result) => !tracker.resultIds.has(result.id))
        newResults.forEach((result) => tracker.resultIds.add(result.id))
        const otherCityIntro =
          newResults.length > 0
            ? `Got it — here are ${newResults.length} supplier quote${newResults.length === 1 ? '' : 's'} from other cities.`
            : 'Got it — checking suppliers from other cities.'
        if (newResults.length > 0) {
          appendSyntheticMessage({
            message_id: `other-city-results-${action.searchId}`,
            role: 'assistant',
            kind: 'results',
            content: otherCityIntro,
            payload: { results: newResults },
            created_at: new Date().toISOString(),
          })
        } else {
          appendSyntheticMessage({
            message_id: `other-city-accepted-${action.searchId}`,
            role: 'assistant',
            kind: 'status',
            content: otherCityIntro,
            created_at: new Date().toISOString(),
          })
        }
        maybeAppendShowMorePrompt(payload, progressTrackerRef.current, appendSyntheticMessage)
        dispatch({
          type: 'SEARCH_PROGRESS_UPDATE',
          payload: slimSearchProgressForState(payload),
        })
      } catch (error) {
        console.error(error)
        if (expandKey) {
          handledExpandKeysRef.current.delete(expandKey)
        }
        appendSyntheticMessage({
          message_id: `other-city-error-${Date.now()}`,
          role: 'assistant',
          kind: 'status',
          content: expandErrorMessage(error),
          created_at: new Date().toISOString(),
        })
      } finally {
        setMessageActionLoading(false)
      }
      return
    }
    if (action?.type === 'show_more_results' && action.searchId) {
      setMessageActionLoading(true)
      try {
        const params = action.offset !== undefined && action.offset !== null ? `?offset=${action.offset}` : ''
        const response = await fetch(`${apiBaseUrl}/api/chat/search/${action.searchId}/more-results${params}`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...displayCurrencyHeaders,
          },
        })
        if (!response.ok) {
          const err = new Error(`More results failed with status ${response.status}`)
          err.status = response.status
          throw err
        }
        const payload = await response.json()
        if (payload.results?.length) {
          payload.results.forEach((result) => progressTrackerRef.current.resultIds.add(result.id))
          appendSyntheticMessage({
            message_id: `more-results-${payload.search_id}-${action.offset || 0}`,
            role: 'assistant',
            kind: 'results',
            content: `Here are ${payload.results.length} more vendor price${payload.results.length === 1 ? '' : 's'}.`,
            payload: { results: payload.results },
            created_at: new Date().toISOString(),
          })
        } else {
          appendSyntheticMessage({
            message_id: `more-results-empty-${payload.search_id || action.searchId}`,
            role: 'assistant',
            kind: 'status',
            content: MORE_RESULTS_EMPTY_MESSAGE,
            created_at: new Date().toISOString(),
          })
        }
        if (payload.has_more) {
          const progressForPrompt = {
            ...(state.searchProgress || {}),
            search_id: payload.search_id,
            next_result_offset: payload.next_offset,
            total_ranked_results: payload.total_results,
            final_results: {
              ...(state.searchProgress?.final_results || {}),
              results: new Array(payload.total_results).fill(null),
            },
          }
          const prompt = buildShowMoreResultsMessage(progressForPrompt, payload.next_offset)
          if (prompt) {
            appendSyntheticMessage(prompt)
          }
        }
      } catch (error) {
        console.error(error)
        if (expandKey) {
          handledExpandKeysRef.current.delete(expandKey)
        }
        appendSyntheticMessage({
          message_id: `more-results-error-${Date.now()}`,
          role: 'assistant',
          kind: 'status',
          content: error?.status === 404 || /\b404\b/.test(String(error?.message || ''))
            ? MORE_RESULTS_GONE_MESSAGE
            : EXPAND_GENERIC_ERROR_MESSAGE,
          created_at: new Date().toISOString(),
        })
      } finally {
        setMessageActionLoading(false)
      }
    }
  }

  const handleSend = async (message, source = 'composer', locationOverride = null) => {
    const trimmed = message.trim()
    const imageToSend = attachedImage
    const hasImage = Boolean(imageToSend)
    if ((!trimmed && !hasImage) || state.isLoading || demoUnlocking) {
      return
    }
    // Carried location from the gold-desk handoff arrives before setLocation has
    // committed, so prefer an explicit override over the (stale) state value.
    const effectiveLocation = locationOverride || location
    const isStartingConversation = !state.sessionId
    const userDisplayMessage = hasImage
      ? `${trimmed || 'Attached a product image'}\n\n[Product image attached]`
      : trimmed

    trackAmplitudeEvent('Chat Message Sent', {
      source,
      messageText: trimmed,
      messageLength: trimmed.length,
      hasExistingSession: !isStartingConversation,
      hasLocation: Boolean(location),
      hasImage,
    })

    dispatch({ type: 'SEND_START', payload: userDisplayMessage })
    setInput('')
    scrollChatToBottom()

    if (demoMode) {
      await runDemoSearch(trimmed, source, isStartingConversation)
      return
    }

    const controller = new AbortController()
    const timeoutId = window.setTimeout(() => controller.abort(), 120000)

    try {
      let response
      if (hasImage) {
        const formData = new FormData()
        formData.append('image', imageToSend)
        formData.append('message', trimmed)
        if (state.sessionId) {
          formData.append('session_id', state.sessionId)
        }
        if (effectiveLocation) {
          formData.append('location', effectiveLocation)
        }
        response = await fetch(`${apiBaseUrl}/api/chat/image-intake`, {
          method: 'POST',
          headers: {
            'X-Device-Id': deviceId,
            ...displayCurrencyHeaders,
          },
          body: formData,
          signal: controller.signal,
        })
      } else {
        response = await fetch(`${apiBaseUrl}/api/chat/message`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Device-Id': deviceId,
            ...displayCurrencyHeaders,
          },
          body: JSON.stringify({
            message: trimmed,
            session_id: state.sessionId || undefined,
            location: effectiveLocation || undefined,
            category: goldLocked ? 'gold' : undefined,
          }),
          signal: controller.signal,
        })
      }

      if (!response.ok) {
        const errorPayload = await response.json().catch(() => null)
        throw new Error(errorPayload?.detail || `Chat failed with status ${response.status}`)
      }

      const payload = await response.json()

      if (isStartingConversation) {
        trackAmplitudeEvent('Conversation Started', {
          source,
          hasLocation: Boolean(location),
          hasSearchProgress: Boolean(payload.search_progress),
        })
      }
      trackAmplitudeEvent('Chat Message Completed', {
        source,
        userMessage: trimmed,
        assistantMessage: payload.assistant_message,
        hasSearchProgress: Boolean(payload.search_progress),
        suggestedReplyCount: payload.suggested_replies?.length || 0,
      })
      dispatch({
        type: 'SEND_SUCCESS',
        payload: {
          sessionId: payload.session_id,
          assistantMessage: payload.assistant_message,
          suggestedReplies: payload.suggested_replies || [],
          conversationState: payload.state,
          results: payload.results || null,
          searchProgress: payload.search_progress || null,
        },
      })

      // Once a search launches (gold OR normal), offer notifications so the user
      // gets pinged when ops posts a supplier quote to their fulfilment request.
      if (payload.session_id && payload.ready_to_search) {
        ensurePushSubscribed(apiBaseUrl, payload.session_id, { promptIfDefault: true })
      }
      clearAttachedImage()
      setLocationAnnouncementShown(true)
      fetchSessions()
    } catch (error) {
      trackAmplitudeEvent('Chat Message Failed', {
        source,
        reason: error.name === 'AbortError' ? 'timeout' : 'request_failed',
      })
      dispatch({
        type: 'SEND_ERROR',
        payload:
          error.name === 'AbortError'
            ? 'This step took longer than 120 seconds. Please try again or narrow the request.'
            : error.message || 'I hit a problem while continuing the conversation. Please try that message again.',
      })
    } finally {
      window.clearTimeout(timeoutId)
    }
  }

  const handleSubmit = (event) => {
    event.preventDefault()
    handleSend(input)
  }

  // gold.zwig.in can still be opened directly with a carried query
  // (?q=...&loc=... + #/app); auto-send it as the first message.
  const goldAutoSentRef = useRef(false)
  useEffect(() => {
    if (!goldLocked || goldAutoSentRef.current) {
      return
    }
    const params = new URLSearchParams(window.location.search)
    const carriedQuery = (params.get('q') || '').trim()
    if (!carriedQuery) {
      return
    }
    goldAutoSentRef.current = true
    const carriedLocation = (params.get('loc') || '').trim()
    if (carriedLocation) {
      rememberLocation(carriedLocation)
    }
    // Strip the carry params but keep the #/app route so a refresh doesn't resend.
    window.history.replaceState(null, '', `/${window.location.hash || '#/app'}`)
    handleSend(carriedQuery, 'gold-redirect', carriedLocation || null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [goldLocked])

  const landingSearchAutoSentRef = useRef(false)
  useEffect(() => {
    if (goldLocked || landingSearchAutoSentRef.current) {
      return
    }
    const carriedQuery = window.sessionStorage.getItem(LANDING_SEARCH_STORAGE_KEY)?.trim() || ''
    if (!carriedQuery) {
      return
    }
    landingSearchAutoSentRef.current = true
    window.sessionStorage.removeItem(LANDING_SEARCH_STORAGE_KEY)
    handleSend(carriedQuery, 'landing-search')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [goldLocked])

  const handleOpenSession = async (sessionId) => {
    try {
      const response = await fetch(`${apiBaseUrl}/api/chat/sessions/${sessionId}`)
      if (!response.ok) {
        throw new Error('Failed to load chat session.')
      }
      const payload = await response.json()
      trackAmplitudeEvent('Chat Session Opened', {
        hasLatestSearch: Boolean(payload.latest_search?.search_id),
        messageCount: payload.messages?.length || 0,
      })
      dispatch({
        type: 'LOAD_SESSION',
        payload: {
          sessionId: payload.session_id,
          messages: payload.messages,
          conversationState: payload.state,
          activeSearchId: payload.latest_search?.search_id || '',
        },
      })
      if (payload.latest_search?.search_id) {
        fetchSearchSnapshot(payload.latest_search.search_id)
      }
      if (payload.state?.location && payload.state.location !== 'unknown') {
        setLocation(payload.state.location)
      }
      setLocationAnnouncementShown(true)
    } catch (error) {
      console.error(error)
    }
  }

  return (
    <div className="h-screen overflow-hidden bg-[#ecece6] text-slate-900">
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

      {/* Mobile sidebar overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/40 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <div className="grid h-screen grid-cols-[minmax(0,1fr)] overflow-hidden lg:grid-cols-[320px_minmax(0,1fr)]">
        {/* Sidebar: hidden on mobile, slides in as overlay when toggled */}
        <div
          className={`fixed inset-y-0 left-0 z-50 w-[280px] transform transition-transform duration-200 ease-in-out lg:relative lg:z-auto lg:w-auto lg:translate-x-0 ${
            sidebarOpen ? 'translate-x-0' : '-translate-x-full'
          } h-screen overflow-y-auto`}
        >
          <SessionSidebar
            sessions={state.sessions}
            activeSessionId={state.sessionId}
            onNewChat={() => {
              trackAmplitudeEvent('New Chat Started', {
                hadActiveSession: Boolean(state.sessionId),
                flow: demoMode ? 'demo-premium' : 'chat',
              })
              setDemoSearch(null)
              setDemoUnlocking(false)
              dispatch({ type: 'RESET_SESSION' })
              setLocationAnnouncementShown(false)
              setSidebarOpen(false)
            }}
            onSelectSession={(sessionId) => {
              handleOpenSession(sessionId)
              setSidebarOpen(false)
            }}
            location={location}
            onChangeLocation={() => {
              trackAmplitudeEvent('Location Prompt Opened', {
                hasLocation: Boolean(location),
              })
              setIsLocationPromptOpen(true)
            }}
          />
        </div>

        <main className="flex h-screen min-w-0 flex-col bg-[#fcfcf9]">
          <div className="border-b border-slate-200 px-4 py-4 sm:px-6 sm:py-5">
            <div className="flex items-center justify-between gap-3">
              <div className="flex min-w-0 items-center gap-3">
                {/* Mobile menu button */}
                <button
                  type="button"
                  onClick={() => setSidebarOpen(true)}
                  className="rounded-lg border border-slate-200 p-2 text-slate-600 transition hover:bg-slate-100 lg:hidden"
                  aria-label="Open sidebar"
                >
                  <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" />
                  </svg>
                </button>
                <div className="min-w-0">
                  <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-400">Chat</p>
                  <h2 className="mt-1 truncate text-lg font-semibold text-slate-900 sm:mt-2 sm:text-2xl">{headline}</h2>
                </div>
              </div>
              <label className="flex shrink-0 items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-2 shadow-[0_10px_24px_rgba(15,23,42,0.04)]">
                <span className="hidden text-[10px] font-black uppercase tracking-[0.22em] text-slate-400 sm:inline">
                  Currency
                </span>
                <select
                  value={displayCurrency}
                  onChange={(event) => handleDisplayCurrencyChange(event.target.value)}
                  className="bg-transparent text-sm font-black text-slate-900 outline-none"
                  aria-label="Display currency"
                >
                  {DISPLAY_CURRENCIES.map((currency) => (
                    <option key={currency} value={currency}>
                      {currency}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto px-3 py-6 sm:px-6 sm:py-8">
            <div className="mx-auto w-full max-w-4xl space-y-5">
              {location && !locationAnnouncementShown && state.messages.length === 1 ? (
                <ChatBubble
                  message={{
                    message_id: 'location-intro',
                    role: 'assistant',
                    kind: 'status',
                    boldPrefix: `📍 Searching suppliers in ${location}.`,
                    content: 'You can change the location anytime.',
                  }}
                  apiBaseUrl={apiBaseUrl}
                  displayCurrency={displayCurrency}
                />
              ) : null}

              {state.messages.map((message) => (
                <ChatBubble
                  key={message.message_id}
                  message={message}
                  onAction={handleMessageAction}
                  onSuggestedReply={(reply) => handleSend(reply, 'suggested_reply')}
                  actionLoading={demoUnlocking || messageActionLoading}
                  replyLoading={state.isLoading}
                  apiBaseUrl={apiBaseUrl}
                  displayCurrency={displayCurrency}
                />
              ))}

              {state.isLoading ? (
                <ChatBubble
                  message={{
                    message_id: 'loading-message',
                    role: 'assistant',
                    kind: 'status',
                    content: demoMode
                      ? 'I’m still working on the live price check.'
                      : searchActive
                        ? SEARCH_WAIT_START
                        : 'Let me lock that in and line up the next step.',
                  }}
                  apiBaseUrl={apiBaseUrl}
                  displayCurrency={displayCurrency}
                />
              ) : null}

              <div ref={messagesEndRef} />
            </div>
          </div>

          <div className="border-t border-slate-200 bg-[#fcfcf9] px-3 py-4 sm:px-6 sm:py-5">
            <div className="mx-auto w-full max-w-4xl">
              <form onSubmit={handleSubmit} className="rounded-[1.75rem] border border-slate-200 bg-white p-3 shadow-[0_16px_40px_rgba(15,23,42,0.06)]">
                {attachedImage ? (
                  <div className="mb-3 flex items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-slate-50 p-2">
                    <div className="flex min-w-0 items-center gap-3">
                      {attachedImagePreview ? (
                        <img
                          src={attachedImagePreview}
                          alt="Attached product"
                          className="h-14 w-14 rounded-xl object-cover"
                        />
                      ) : null}
                      <div className="min-w-0">
                        <p className="truncate text-sm font-bold text-slate-900">{attachedImage.name}</p>
                        <p className="text-xs text-slate-500">I’ll identify this product before searching.</p>
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={clearAttachedImage}
                      className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-600 transition hover:bg-slate-100"
                    >
                      Remove
                    </button>
                  </div>
                ) : null}
                <div className="flex items-end gap-3">
                  <input
                    ref={imageInputRef}
                    type="file"
                    accept="image/*"
                    className="hidden"
                    onChange={handleImageSelected}
                  />
                  <button
                    type="button"
                    onClick={() => imageInputRef.current?.click()}
                    disabled={composerDisabled}
                    className="mb-1 rounded-2xl border border-slate-200 bg-slate-50 px-3 py-3 text-sm font-black text-slate-600 transition hover:border-slate-300 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60"
                    aria-label="Attach product image"
                    title="Attach product image"
                  >
                    +
                  </button>
                  <textarea
                    value={input}
                    onChange={(event) => setInput(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' && !event.shiftKey) {
                        event.preventDefault()
                        handleSubmit(event)
                      }
                    }}
                    rows={1}
                    placeholder={location ? `Ask anything in ${location}` : 'Ask anything'}
                    disabled={composerDisabled}
                    className="max-h-40 min-h-[48px] w-full min-w-0 flex-1 resize-none border-0 bg-transparent px-3 py-2 text-base text-slate-900 outline-none placeholder:text-slate-400"
                  />
                  <button
                    type="submit"
                    disabled={composerDisabled || (!input.trim() && !attachedImage)}
                    className="rounded-2xl bg-slate-900 px-5 py-3 text-sm font-medium text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    Send
                  </button>
                </div>
              </form>

              {state.error ? (
                <div className="mt-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
                  {state.error}
                </div>
              ) : null}
            </div>
          </div>
        </main>
      </div>
    </div>
  )
}

function App() {
  const [route, setRoute] = useState(() => getRouteFromLocation())

  useEffect(() => {
    const handleRouteChange = () => {
      setRoute(getRouteFromLocation())
    }

    window.addEventListener('hashchange', handleRouteChange)
    window.addEventListener('popstate', handleRouteChange)
    return () => {
      window.removeEventListener('hashchange', handleRouteChange)
      window.removeEventListener('popstate', handleRouteChange)
    }
  }, [])

  useEffect(() => {
    trackAmplitudeEvent('Page Viewed', { route })
  }, [route])

  if (route === 'about') {
    return <AboutPage />
  }

  if (route === 'contact') {
    return <ContactPage />
  }

  if (route === 'privacy') {
    return <PrivacyPolicyPage />
  }

  if (route === 'terms') {
    return <TermsPage />
  }

  if (route === 'security') {
    return <SecurityPage />
  }

  if (route === 'support') {
    return <SupportPage />
  }

  if (route === 'supplierOnboarding') {
    return <SupplierOnboardingPage />
  }

  if (route === 'supplierOnboardingForm') {
    return <SupplierOnboardingPage mode="form" />
  }

  if (route === 'supplierPilot') {
    return <SupplierPilotPage token={getSupplierPilotTokenFromLocation()} />
  }

  if (route === 'supplierLoi') {
    return <SupplierLoiPage token={getSupplierLoiTokenFromLocation()} />
  }

  if (route === 'supplierCrm') {
    return <SupplierCrmDashboard />
  }

  if (route === 'supplierDetail') {
    return <SupplierDetailPage supplierId={getSupplierDetailIdFromLocation()} />
  }

  if (route === 'supplierPublic') {
    return <SupplierPublicPage slug={getSupplierPublicSlugFromLocation()} />
  }

  if (route === 'app') {
    return <ChatApp />
  }

  if (route === 'demoPremium') {
    return <ChatApp demoMode />
  }

  if (route === 'voiceLab') {
    return <VoiceLabDashboard />
  }

  if (route === 'goldApp') {
    return <ChatApp goldLocked />
  }

  if (route === 'goldLanding') {
    return <LandingPage variant="gold" />
  }

  return <LandingPage />
}

export default App

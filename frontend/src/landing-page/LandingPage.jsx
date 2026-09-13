import { useEffect, useRef, useState } from 'react'
import { trackAmplitudeEvent } from '../lib/amplitude'
import { BRAND_LOGO_192_URL } from '../lib/brandAssets'
import LegalFooter from './LegalFooter'
import { TrendingSearchesSection } from './OfflineEconomySections'
import ProcurementLiveAnimation from './ProcurementLiveAnimation'

const SEARCH_SUGGESTIONS = [
  'Paracetamol 650 mg tablets',
  'Samsung S24 bulk procurement',
  '999 gold bullion in Mumbai',
  'Laryngoscope set with GST invoice',
]

const ROTATING_SEARCH_EXAMPLES = [
  { query: 'Paracetamol 650 mg tablets, 100 strips', location: 'Chandigarh' },
  { query: 'Samsung S24 bulk procurement, 50 units', location: 'Bengaluru' },
  { query: '999 gold bullion, 500 grams', location: 'Mumbai' },
  { query: 'Industrial safety gear wholesale, 200 units', location: 'Surat' },
  { query: 'Catering service for 40 people', location: 'Mumbai' },
  { query: 'Laryngoscope set with GST invoice', location: 'Chandigarh' },
  { query: 'Packaging pouches wholesale pricing', location: 'Ahmedabad' },
  { query: 'Warehouse shelving bulk quotes', location: 'Rajkot' },
]

const GOLD_ROTATING_SEARCH_EXAMPLES = [
  { query: '999 gold bullion, 500 grams, Mumbai', location: 'Mumbai' },
  { query: '995 bullion live rate, Ahmedabad', location: 'Ahmedabad' },
  { query: '22K gold supplier, Rajkot', location: 'Rajkot' },
  { query: 'Silver 999 live rate today', location: 'Delhi' },
  { query: 'Gold coins and bars, wholesale', location: 'Jaipur' },
]

const CATEGORIES = [
  {
    title: 'Pharmaceuticals',
    body: 'Tablets, syrups, injections, API distributors, and medical stockists.',
    tone: 'from-emerald-50 to-white',
  },
  {
    title: 'Electronics',
    body: 'Phones, accessories, components, business devices, and repair parts.',
    tone: 'from-sky-50 to-white',
  },
  {
    title: 'Gold & Bullion',
    body: 'Live rates, city dealers, refiners, 999/995/22K, silver, coins, and bars.',
    tone: 'from-amber-50 to-white',
  },
  {
    title: 'Industrial',
    body: 'Automation, machinery, chemicals, MRO, packaging, and raw materials.',
    tone: 'from-stone-100 to-white',
  },
  {
    title: 'Medical Equipment',
    body: 'Hospital equipment, surgical instruments, diagnostics, and clinic supplies.',
    tone: 'from-cyan-50 to-white',
  },
  {
    title: 'Packaging',
    body: 'Boxes, pouches, films, labels, corrugation, bottles, and bulk suppliers.',
    tone: 'from-orange-50 to-white',
  },
]

const GOLD_SEARCH_SUGGESTIONS = [
  '999 gold rate in Rajkot',
  '995 bullion 500 grams Mumbai',
  '22K gold supplier in Ahmedabad',
  'Silver 999 live rate today',
]

const GOLD_CATEGORIES = [
  {
    title: 'Gold 999 / 24K',
    body: 'Live city rates and supplier quotes for fine gold.',
    tone: 'from-amber-50 to-white',
  },
  {
    title: 'Gold 995',
    body: 'Dealer rates for standard bullion bars and business purchases.',
    tone: 'from-yellow-50 to-white',
  },
  {
    title: 'Gold 22K',
    body: 'Jewellery-grade procurement and city-level supplier discovery.',
    tone: 'from-orange-50 to-white',
  },
  {
    title: 'Silver 999',
    body: 'Live rates, dealer discovery, and bulk silver requirements.',
    tone: 'from-slate-50 to-white',
  },
  {
    title: 'Coins & Bars',
    body: 'Minted coins, cast bars, refiners, and wholesale desks.',
    tone: 'from-stone-100 to-white',
  },
  {
    title: 'Old Gold / Scrap',
    body: 'Local resale, refinery, and exchange-led procurement support.',
    tone: 'from-zinc-100 to-white',
  },
]

const LANDING_SEARCH_STORAGE_KEY = 'zwig-landing-search'
const WHATSAPP_PREFILL_MESSAGE = 'Hi Zwig, I need help with procurement.'
const WHATSAPP_CHAT_URL =
  import.meta.env.VITE_WHATSAPP_URL ||
  `https://wa.me/${import.meta.env.VITE_WHATSAPP_PHONE || '918310314830'}?text=${encodeURIComponent(WHATSAPP_PREFILL_MESSAGE)}`

const PROCUREMENT_ACTIVITY = [
  {
    title: 'Supplier graph searching',
    body: 'Matching product intent, city, category, and historical supplier evidence.',
    metric: '542k+ offerings',
    icon: 'nodes',
  },
  {
    title: 'Vendors being contacted',
    body: 'Callable suppliers are queued while listed prices and catalog data arrive first.',
    metric: '12 vendors',
    icon: 'calls',
  },
  {
    title: 'Quotes ranked into one sheet',
    body: 'Price, GST, availability, lead time, and confidence are normalized for comparison.',
    metric: 'best quote',
    icon: 'rank',
  },
]

const GOLD_ACTIVITY = [
  {
    title: 'Live scripts pulled',
    body: 'City dealer rate boards are fetched and matched to purity and quantity.',
    metric: '999 / 995',
    icon: 'nodes',
  },
  {
    title: 'Local dealers ranked',
    body: 'Same-city live-rate vendors come first, then high-confidence popular dealers.',
    metric: 'Mumbai',
    icon: 'rank',
  },
  {
    title: 'Supplier quotes lined up',
    body: 'When a price is missing or stale, the best reachable dealers are contacted.',
    metric: 'live quote',
    icon: 'calls',
  },
]

function Reveal({ children, className = '', delay = 0 }) {
  const ref = useRef(null)
  const [visible, setVisible] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  )

  useEffect(() => {
    if (visible || !ref.current) {
      return undefined
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          setVisible(true)
          observer.disconnect()
        }
      },
      { threshold: 0.12 },
    )
    observer.observe(ref.current)
    return () => observer.disconnect()
  }, [visible])

  return (
    <div
      ref={ref}
      className={`transition-all duration-700 ease-out ${
        visible ? 'translate-y-0 opacity-100' : 'translate-y-6 opacity-0'
      } ${className}`}
      style={delay ? { transitionDelay: `${delay}ms` } : undefined}
    >
      {children}
    </div>
  )
}

function StartSearchButton({ placement, children = 'Search Products', className = '' }) {
  return (
    <a
      href="#/app"
      onClick={() => trackAmplitudeEvent('Landing CTA Clicked', { placement })}
      className={`inline-flex items-center justify-center rounded-full bg-[#f97316] px-5 py-2.5 text-sm font-semibold text-white shadow-[0_14px_28px_-14px_rgba(249,115,22,0.7)] transition hover:bg-[#ea580c] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#f97316] ${className}`}
    >
      {children}
    </a>
  )
}

function WhatsAppIcon({ className = 'h-4 w-4' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.435 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z" />
    </svg>
  )
}

function HeroChannelButtons() {
  const subtleButtonClass =
    'inline-flex items-center justify-center gap-2 rounded-full border border-[#e5e7eb] bg-white/90 px-5 py-3 text-sm font-semibold text-[#334155] shadow-sm transition hover:border-[#d1d5db] hover:bg-white hover:text-[#111827] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#f97316]'

  return (
    <div className="mt-8 flex flex-col items-stretch justify-center gap-3 sm:flex-row sm:items-center sm:justify-center sm:gap-3">
      <a
        href={WHATSAPP_CHAT_URL}
        target="_blank"
        rel="noopener noreferrer"
        onClick={() => trackAmplitudeEvent('Landing CTA Clicked', { placement: 'hero_whatsapp' })}
        className={subtleButtonClass}
      >
        <WhatsAppIcon className="h-4 w-4 text-[#64748b]" />
        Procure via WhatsApp
      </a>
      <a
        href="#/app"
        onClick={() => trackAmplitudeEvent('Landing CTA Clicked', { placement: 'hero_web_app' })}
        className={subtleButtonClass}
      >
        Open Web App
      </a>
    </div>
  )
}

function SectionHeading({ eyebrow, title, body, align = 'left' }) {
  return (
    <div className={align === 'center' ? 'mx-auto max-w-3xl text-center' : 'max-w-3xl'}>
      {eyebrow ? (
        <p className="text-xs font-bold uppercase tracking-[0.22em] text-[#f97316]">{eyebrow}</p>
      ) : null}
      <h2 className="mt-3 text-[30px] font-black leading-[1.06] tracking-[-0.04em] text-[#111827] sm:text-4xl md:text-[48px]">
        {title}
      </h2>
      {body ? <p className="mt-4 text-base leading-7 text-[#5b6472] sm:text-lg">{body}</p> : null}
    </div>
  )
}

function ActivityIcon({ type }) {
  if (type === 'calls') {
    return (
      <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
        <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 6.75h.008v.008H8.25zM12 6.75h.008v.008H12zM15.75 6.75h.008v.008h-.008z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M5.25 4.5h13.5A2.25 2.25 0 0 1 21 6.75v6a2.25 2.25 0 0 1-2.25 2.25h-4.5L9 19.5V15H5.25A2.25 2.25 0 0 1 3 12.75v-6A2.25 2.25 0 0 1 5.25 4.5Z" />
      </svg>
    )
  }
  if (type === 'rank') {
    return (
      <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
        <path strokeLinecap="round" strokeLinejoin="round" d="M5 19V9m7 10V5m7 14v-7" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.5 19h17" />
      </svg>
    )
  }
  return (
    <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.75a2.25 2.25 0 1 0 0-4.5 2.25 2.25 0 0 0 0 4.5ZM5.25 21.75a2.25 2.25 0 1 0 0-4.5 2.25 2.25 0 0 0 0 4.5ZM18.75 21.75a2.25 2.25 0 1 0 0-4.5 2.25 2.25 0 0 0 0 4.5Z" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M10.65 6.05 6.6 17.45m6.75-11.4 4.05 11.4M7.5 19.5h9" />
    </svg>
  )
}

function LocationPinIcon({ className = 'h-4 w-4' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 21s7-4.35 7-10a7 7 0 1 0-14 0c0 5.65 7 10 7 10Z" />
      <circle cx="12" cy="11" r="2.5" />
    </svg>
  )
}

function SearchDemo({ isGold }) {
  const suggestions = isGold ? GOLD_SEARCH_SUGGESTIONS : SEARCH_SUGGESTIONS
  const rotatingExamples = isGold ? GOLD_ROTATING_SEARCH_EXAMPLES : ROTATING_SEARCH_EXAMPLES
  const activity = isGold ? GOLD_ACTIVITY : PROCUREMENT_ACTIVITY
  const [exampleIndex, setExampleIndex] = useState(0)
  const [query, setQuery] = useState(rotatingExamples[0].query)
  const [isFocused, setIsFocused] = useState(false)
  const [userEdited, setUserEdited] = useState(false)
  const activeLocation = rotatingExamples[exampleIndex]?.location || rotatingExamples[0].location

  useEffect(() => {
    if (isFocused || userEdited) {
      return undefined
    }
    const timer = window.setInterval(() => {
      setExampleIndex((current) => {
        const next = (current + 1) % rotatingExamples.length
        setQuery(rotatingExamples[next].query)
        return next
      })
    }, 3500)
    return () => window.clearInterval(timer)
  }, [isFocused, userEdited, rotatingExamples])

  const submitSearch = (event) => {
    event.preventDefault()
    const trimmed = query.trim()
    if (!trimmed) {
      return
    }
    trackAmplitudeEvent('Landing Search Submitted', {
      query: trimmed,
      location: activeLocation,
      variant: isGold ? 'gold' : 'main',
    })
    if (typeof window !== 'undefined') {
      window.sessionStorage.setItem(LANDING_SEARCH_STORAGE_KEY, trimmed)
      window.location.href = '#/app'
    }
  }

  const handleSuggestionClick = (suggestion) => {
    setQuery(suggestion)
    setUserEdited(true)
    trackAmplitudeEvent('Landing Search Suggestion Clicked', {
      suggestion,
      variant: isGold ? 'gold' : 'main',
    })
  }

  const handleQueryChange = (event) => {
    setQuery(event.target.value)
    setUserEdited(true)
  }

  const handleQueryFocus = () => {
    setIsFocused(true)
  }

  const handleQueryBlur = (event) => {
    setIsFocused(false)
    if (!event.target.value.trim()) {
      setUserEdited(false)
      setQuery(rotatingExamples[exampleIndex].query)
    }
  }

  return (
    <div className="mx-auto max-w-5xl">
      <form
        onSubmit={submitSearch}
        className="relative z-20 rounded-[28px] border border-black/10 bg-white p-3 shadow-[0_28px_90px_-44px_rgba(15,23,42,0.55)]"
      >
        <div className="flex flex-col gap-3 rounded-[22px] border border-[#e5e7eb] bg-[#fbfaf8] p-3 sm:flex-row sm:items-center sm:p-4">
          <div
            aria-live="polite"
            className="flex h-12 shrink-0 items-center gap-2 rounded-2xl border border-[#e5e7eb] bg-white px-4 sm:min-w-[156px]"
          >
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-[#fff7ed] text-[#f97316]">
              <LocationPinIcon />
            </span>
            <span
              key={userEdited ? 'edited-location' : exampleIndex}
              className={`truncate text-sm font-black text-[#111827] ${userEdited ? '' : 'zwig-query-fade'}`}
            >
              {activeLocation}
            </span>
          </div>

          <label className="flex min-h-12 flex-1 items-center gap-3 rounded-2xl border border-[#e5e7eb] bg-white px-4 shadow-sm transition focus-within:border-[#f97316] focus-within:ring-4 focus-within:ring-orange-100">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-[#fff7ed] text-[#f97316]">
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-4.35-4.35" />
                <circle cx="10.5" cy="10.5" r="6.5" />
              </svg>
            </span>
            <input
              key={userEdited ? 'edited' : exampleIndex}
              value={query}
              onChange={handleQueryChange}
              onFocus={handleQueryFocus}
              onBlur={handleQueryBlur}
              placeholder={isGold ? 'Search 999 gold, 995 bullion, silver...' : 'Search medicines, electronics, packaging suppliers...'}
              className={`w-full bg-transparent text-base font-bold text-[#111827] outline-none placeholder:text-[#a8b1bf] sm:text-lg ${
                userEdited ? '' : 'zwig-query-fade'
              }`}
            />
          </label>

          <button
            type="submit"
            className="group inline-flex h-12 items-center justify-center gap-2 rounded-2xl bg-[#f97316] px-5 text-sm font-black text-white shadow-[0_18px_34px_-20px_rgba(249,115,22,0.85)] transition hover:bg-[#ea580c] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#f97316]"
          >
            Search
            <svg className="h-4 w-4 transition group-hover:-translate-y-0.5 group-hover:translate-x-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M7 17 17 7M8 7h9v9" />
            </svg>
          </button>
        </div>
      </form>

      <div className="relative z-10 mt-5 overflow-hidden">
        <div
          className="flex gap-3 overflow-x-auto px-2 pb-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          aria-label="Popular procurement searches"
        >
          {suggestions.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              onClick={() => handleSuggestionClick(suggestion)}
              className="shrink-0 rounded-full border border-[#e5e7eb] bg-white/86 px-4 py-2 text-sm font-bold text-[#475569] shadow-sm backdrop-blur transition hover:-translate-y-0.5 hover:border-[#fdba74] hover:text-[#c2410c]"
            >
              {suggestion}
            </button>
          ))}
        </div>
      </div>

      <div className="relative mt-8">
        <div className="absolute left-1/2 top-4 hidden h-[calc(100%-2rem)] w-px -translate-x-1/2 bg-gradient-to-b from-transparent via-[#fed7aa] to-transparent md:block" />
        <div className="grid gap-4 md:grid-cols-3">
          {activity.map((item, index) => (
            <article
              key={item.title}
              className="zwig-task-float relative rounded-[26px] border border-[#e5e7eb] bg-white/92 p-5 text-left shadow-[0_18px_60px_-36px_rgba(15,23,42,0.55)] backdrop-blur"
              style={{ animationDelay: `${index * 220}ms` }}
            >
              <div className="mb-5 flex items-start justify-between gap-4">
                <span className="grid h-12 w-12 place-items-center rounded-2xl bg-[#fff7ed] text-[#f97316]">
                  <ActivityIcon type={item.icon} />
                </span>
                <span className="rounded-full bg-[#111827] px-3 py-1 text-xs font-black uppercase tracking-[0.12em] text-white">
                  {item.metric}
                </span>
              </div>
              <h3 className="text-lg font-black tracking-[-0.03em] text-[#111827]">{item.title}</h3>
              <p className="mt-2 text-sm leading-6 text-[#64748b]">{item.body}</p>
              <div className="mt-5 h-1.5 overflow-hidden rounded-full bg-[#f1f5f9]">
                <div className="zwig-progress-scan h-full rounded-full bg-gradient-to-r from-[#f97316] via-[#fdba74] to-[#f97316]" />
              </div>
            </article>
          ))}
        </div>
      </div>


      <div className="mt-10 grid gap-3 lg:grid-cols-[1fr_0.9fr]">
        <div className="rounded-2xl border border-[#e5e7eb] bg-white p-4">
          <div className="mb-4 flex items-center justify-between">
            <p className="text-sm font-bold text-[#111827]">Supplier results</p>
            <span className="rounded-full bg-[#ecfdf5] px-2.5 py-1 text-xs font-semibold text-[#047857]">
              Ranking live
            </span>
          </div>
          {(isGold
            ? [
                ['Shree Mandev Bullion LLP', 'Live script · Mumbai · 999/995', '₹1,55,543'],
                ['Safari Bullions', 'Live board · Mumbai · GST', '₹1,55,571'],
                ['RSBL', 'Popular desk · Mumbai', '₹1,56,209'],
              ]
            : [
                ['Acichem Laboratories', '650 mg tablets · verified phone', '₹400 / box'],
                ['Zenacts Pharma', 'Tablets · Chandigarh · GST', '₹430 / box'],
                ['Pransa Healthcare', 'Dolo/Crocin alternatives', '₹455 / box'],
              ]).map(([name, meta, price], index) => (
            <div
              key={name}
              className={`flex items-start justify-between gap-4 py-3 ${index ? 'border-t border-[#edf0f3]' : ''}`}
            >
              <div>
                <p className="text-sm font-bold text-[#111827]">{name}</p>
                <p className="mt-1 text-xs leading-5 text-[#64748b]">{meta}</p>
              </div>
              <p className="whitespace-nowrap text-sm font-black tabular-nums text-[#111827]">{price}</p>
            </div>
          ))}
        </div>

        <div className="relative overflow-hidden rounded-2xl border border-[#e5e7eb] bg-[#111827] p-4 text-white">
          <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-[#f97316] to-transparent" />
          <p className="text-sm font-bold">{isGold ? 'Live rate feed' : 'Negotiation in progress'}</p>
          <div className="mt-4 space-y-3 text-sm">
            <div className="rounded-2xl bg-white/10 p-3">
              {isGold ? 'Pulling vendor scripts and city rates...' : 'Can you improve pricing for 100 strips with GST invoice?'}
            </div>
            <div className="ml-8 rounded-2xl bg-[#f97316] p-3 text-white">
              {isGold ? 'Updated: 3 Mumbai live boards found.' : 'We can do ₹400 per box if confirmed today.'}
            </div>
            <div className="rounded-2xl bg-white/10 p-3">
              {isGold ? 'Ranking by city, freshness, and supplier confidence.' : 'Another supplier is at ₹430. Marking this as best quote.'}
            </div>
          </div>
          <div className="mt-5 grid grid-cols-3 gap-2">
            {['Price', 'GST', 'Lead time'].map((label) => (
              <div key={label} className="rounded-xl border border-white/10 bg-white/5 p-3">
                <p className="text-[11px] uppercase tracking-[0.14em] text-white/50">{label}</p>
                <p className="mt-1 text-sm font-bold text-white">Checked</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function LandingPage({ variant } = {}) {
  const isGold = variant === 'gold'
  const categories = isGold ? GOLD_CATEGORIES : CATEGORIES

  useEffect(() => {
    const scrollToSectionHash = () => {
      const hash = window.location.hash.replace(/^#/, '')
      if (!hash || hash.startsWith('/')) {
        return
      }
      const target = document.getElementById(hash)
      if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }
    }

    scrollToSectionHash()
    window.addEventListener('hashchange', scrollToSectionHash)
    return () => window.removeEventListener('hashchange', scrollToSectionHash)
  }, [])

  return (
    <div className="min-h-screen overflow-x-hidden bg-[#fbfaf8] font-display text-[#111827]">
      <nav className="sticky top-0 z-50 border-b border-black/5 bg-white/82 backdrop-blur-xl">
        <div className="mx-auto flex max-w-[1180px] items-center justify-between gap-4 px-4 py-3.5 sm:px-6">
          <a href="#/" className="flex items-center gap-3" aria-label="Zwig home">
            <img src={BRAND_LOGO_192_URL} alt="Zwig" className="h-9 w-9 rounded-xl" />
            <span className="text-lg font-black tracking-[-0.03em]">ZWIG</span>
          </a>

          <StartSearchButton placement="navbar">Search Products</StartSearchButton>
        </div>
      </nav>

      <main>
        <section className="relative overflow-hidden px-4 pb-16 pt-16 sm:px-6 sm:pb-20 sm:pt-24">
          <div className="absolute left-1/2 top-0 h-[560px] w-[980px] -translate-x-1/2 rounded-full bg-[radial-gradient(circle,rgba(249,115,22,0.16),transparent_68%)]" />
          <div className="absolute inset-x-0 top-0 h-[520px] bg-[linear-gradient(180deg,#fff7ed_0%,rgba(255,247,237,0)_72%)]" />
          <div className="relative mx-auto max-w-[1180px]">
            <div className="mx-auto max-w-5xl text-center">
              <Reveal>
                <div className="mx-auto inline-flex items-center gap-2 rounded-full border border-[#fed7aa] bg-white px-3 py-1.5 text-xs font-bold text-[#9a3412] shadow-sm">
                  <span className="h-2 w-2 rounded-full bg-[#f97316]" />
                  {isGold ? 'Live bullion procurement desk' : "India's AI-native procurement platform"}
                </div>
              </Reveal>

              <Reveal delay={80}>
                <h1 className="mt-7 text-[42px] font-black leading-[0.96] tracking-[-0.065em] text-[#0f172a] sm:text-6xl lg:text-[86px]">
                  {isGold ? 'Live gold rates from verified supplier channels.' : 'Source products from any industry in seconds.'}
                </h1>
              </Reveal>

              <Reveal delay={150}>
                <p className="mx-auto mt-7 max-w-3xl text-lg leading-8 text-[#4b5563] sm:text-xl">
                  {isGold
                    ? 'Search gold and silver requirements, compare city live rates, and line up supplier quotes with timestamped evidence.'
                    : 'Tell us what you need. Zwig finds suppliers, compares quotes, negotiates prices, and completes your procurement.'}
                </p>
              </Reveal>

              <Reveal delay={220}>
                <HeroChannelButtons />
              </Reveal>

            </div>

            <Reveal delay={300} className="mx-auto mt-12 max-w-5xl">
              <SearchDemo isGold={isGold} />
            </Reveal>
          </div>
        </section>

        <TrendingSearchesSection />

        <section id="features" className="mx-auto max-w-[1180px] scroll-mt-24 px-4 py-20 sm:px-6 sm:py-28">
          <Reveal>
            <SectionHeading
              eyebrow="How Zwig works"
              title="One search becomes a live procurement workflow."
              body="Watch Zwig move from plain-language search to verified supplier quotes — automatically, in minutes."
            />
          </Reveal>
          <Reveal delay={120} className="mt-12">
            <ProcurementLiveAnimation />
          </Reveal>
        </section>

        <section id="industries" className="mx-auto max-w-[1180px] scroll-mt-24 px-4 py-20 sm:px-6 sm:py-28">
          <Reveal>
            <SectionHeading
              eyebrow="Industries"
              title="Built for the categories Indian businesses actually buy."
            />
          </Reveal>

          <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {categories.map((category, index) => (
              <Reveal key={category.title} delay={index * 50}>
                <article className={`group min-h-[230px] rounded-[28px] border border-[#e5e7eb] bg-gradient-to-br ${category.tone} p-6 shadow-sm transition hover:-translate-y-1 hover:shadow-xl`}>
                  <div className="mb-12 flex items-center justify-between">
                    <span className="rounded-full bg-white/80 px-3 py-1 text-xs font-black uppercase tracking-[0.14em] text-[#64748b]">
                      Category
                    </span>
                    <span className="grid h-10 w-10 place-items-center rounded-full bg-white text-[#f97316] shadow-sm transition group-hover:rotate-[-10deg]">
                      <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M7 17 17 7M8 7h9v9" />
                      </svg>
                    </span>
                  </div>
                  <h3 className="text-2xl font-black tracking-[-0.04em] text-[#111827]">{category.title}</h3>
                  <p className="mt-3 text-sm leading-6 text-[#64748b]">{category.body}</p>
                </article>
              </Reveal>
            ))}
          </div>
        </section>


      </main>

      <LegalFooter />
    </div>
  )
}

export default LandingPage

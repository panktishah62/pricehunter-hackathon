import { useEffect, useState } from 'react'
import { BRAND_LOGO_192_URL } from '../lib/brandAssets'

const STAGE_DURATION_MS = 3200

const STAGES = [
  {
    id: 'ask',
    pill: 'Just search',
    caption: 'Pharma, gold, packaging — plain English',
  },
  {
    id: 'clarify',
    pill: 'Intent check',
    caption: 'Quantity, city, GST, and lead time locked in',
  },
  {
    id: 'discover',
    pill: 'Find suppliers',
    caption: 'Graph + directories mapped to your ask',
  },
  {
    id: 'call',
    pill: 'Zwig calls',
    caption: 'Suppliers dialed in parallel — no hold music',
  },
  {
    id: 'deliver',
    pill: 'Best quotes',
    caption: 'Ranked, verified, with call proof',
  },
]

const SCENARIOS = [
  {
    emoji: '💊',
    label: 'Pharma',
    query: 'Paracetamol 650 mg, 100 strips, GST invoice — Chandigarh',
    clarify: ['100 strips MOQ', 'GST invoice required', 'Same-week dispatch'],
    discoverCount: 18,
    discoverNames: ['Acichem Laboratories', 'Zenacts Pharma', 'Pransa Healthcare', 'Medline Distributors'],
    calls: [
      { name: 'Acichem Laboratories', detail: '₹400 / box · in stock', active: true },
      { name: 'Zenacts Pharma', detail: '₹430 / box · GST ready', active: true },
      { name: 'Pransa Healthcare', detail: 'Ringing...', active: false },
    ],
    results: [
      { rank: 1, name: 'Acichem Laboratories', meta: 'Called · GST included', price: '₹400 / box' },
      { rank: 2, name: 'Medline Distributors', meta: 'Called · 2-day dispatch', price: '₹415 / box' },
      { rank: 3, name: 'Zenacts Pharma', meta: 'Called · same week', price: '₹430 / box' },
    ],
    stat: { called: 12, minutes: 4, best: '₹400' },
  },
  {
    emoji: '🥇',
    label: 'Gold',
    query: '999 gold bullion, 500 grams, live rate — Mumbai with GST docs',
    clarify: ['500 grams', '999 purity', 'Mumbai dealer preferred'],
    discoverCount: 24,
    discoverNames: ['Shree Mandev Bullion', 'Safari Bullions', 'RSBL', 'MMTC-PAMP desk'],
    calls: [
      { name: 'Shree Mandev Bullion', detail: '₹1,55,543 · live script', active: true },
      { name: 'Safari Bullions', detail: '₹1,55,571 · board updated', active: true },
      { name: 'RSBL', detail: 'Confirming rate...', active: false },
    ],
    results: [
      { rank: 1, name: 'Shree Mandev Bullion', meta: 'Live script · Mumbai', price: '₹1,55,543' },
      { rank: 2, name: 'Safari Bullions', meta: 'Live board · GST', price: '₹1,55,571' },
      { rank: 3, name: 'RSBL', meta: 'Popular desk', price: '₹1,56,209' },
    ],
    stat: { called: 9, minutes: 3, best: '₹1,55,543' },
  },
  {
    emoji: '📱',
    label: 'Electronics',
    query: 'Samsung Galaxy S24 bulk order, 50 units — best distributor with warranty',
    clarify: ['50 units', 'Warranty + invoice', 'Lowest landed price'],
    discoverCount: 14,
    discoverNames: ['TechDistro India', 'Mobile Wholesale Hub', 'Samsung B2B desk', 'GadgetMart'],
    calls: [
      { name: 'TechDistro India', detail: '₹58,200 / unit · 50 pcs', active: true },
      { name: 'Mobile Wholesale Hub', detail: '₹58,450 / unit', active: true },
      { name: 'GadgetMart', detail: 'Checking stock...', active: false },
    ],
    results: [
      { rank: 1, name: 'TechDistro India', meta: 'Called · warranty', price: '₹58,200' },
      { rank: 2, name: 'Mobile Wholesale Hub', meta: 'Called · 3-day lead', price: '₹58,450' },
      { rank: 3, name: 'Samsung B2B desk', meta: 'Called · official', price: '₹59,100' },
    ],
    stat: { called: 11, minutes: 5, best: '₹58,200' },
  },
  {
    emoji: '🏭',
    label: 'Industrial',
    query: 'Industrial safety gear wholesale, 200 units — Gujarat suppliers with GST',
    clarify: ['200 units', 'Bulk pricing', 'Lead time under 7 days'],
    discoverCount: 16,
    discoverNames: ['SafePro Industrial', 'Gujarat MRO Supply', 'ShieldWork Traders', 'Atlas Safety'],
    calls: [
      { name: 'SafePro Industrial', detail: '₹890 / unit · in stock', active: true },
      { name: 'Gujarat MRO Supply', detail: '₹920 / unit · bulk', active: true },
      { name: 'ShieldWork Traders', detail: 'Voicemail...', active: false },
    ],
    results: [
      { rank: 1, name: 'SafePro Industrial', meta: 'Called · 5-day dispatch', price: '₹890 / unit' },
      { rank: 2, name: 'Gujarat MRO Supply', meta: 'Called · GST invoice', price: '₹920 / unit' },
      { rank: 3, name: 'Atlas Safety', meta: 'Called · 7-day lead', price: '₹945 / unit' },
    ],
    stat: { called: 10, minutes: 4, best: '₹890' },
  },
]

function WaveBars({ active }) {
  return (
    <div className={`flex h-5 items-end gap-0.5 ${active ? 'opacity-100' : 'opacity-30'}`} aria-hidden="true">
      {[0, 1, 2, 3, 4].map((bar) => (
        <span
          key={bar}
          className="wave-bar w-1 rounded-full bg-[#f97316]"
          style={{ height: `${40 + bar * 12}%`, animationDelay: `${bar * 0.12}s` }}
        />
      ))}
    </div>
  )
}

function StagePanel({ stageIndex, scenario }) {
  const stage = STAGES[stageIndex]

  if (stage.id === 'ask') {
    return (
      <div className="zwig-stage-panel space-y-4">
        <div className="rounded-[22px] border border-[#e5e7eb] bg-white p-4 shadow-sm">
          <p className="text-xs font-black uppercase tracking-[0.16em] text-[#94a3b8]">Your search</p>
          <p className="mt-3 text-lg font-black leading-snug tracking-[-0.03em] text-[#111827] sm:text-xl">
            {scenario.query}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {['Products', 'Suppliers', scenario.label].map((chip) => (
            <span key={chip} className="rounded-full bg-[#fff7ed] px-3 py-1.5 text-xs font-bold text-[#c2410c]">
              {chip}
            </span>
          ))}
        </div>
      </div>
    )
  }

  if (stage.id === 'clarify') {
    return (
      <div className="zwig-stage-panel space-y-3">
        <p className="text-sm font-bold text-[#111827]">Zwig follow-ups</p>
        {scenario.clarify.map((item, index) => (
          <div
            key={item}
            className="zwig-stage-item flex items-center gap-3 rounded-2xl border border-[#e5e7eb] bg-white px-4 py-3 shadow-sm"
            style={{ animationDelay: `${index * 120}ms` }}
          >
            <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-[#111827] text-xs font-black text-white">
              {index + 1}
            </span>
            <p className="text-sm font-semibold text-[#334155]">{item}</p>
          </div>
        ))}
      </div>
    )
  }

  if (stage.id === 'discover') {
    return (
      <div className="zwig-stage-panel space-y-4">
        <div className="flex items-center justify-between rounded-2xl border border-[#e5e7eb] bg-[#fbfaf8] px-4 py-3">
          <p className="text-sm font-bold text-[#111827]">Supplier graph searching</p>
          <span className="rounded-full bg-[#111827] px-3 py-1 text-xs font-black tabular-nums text-white">
            {scenario.discoverCount} found
          </span>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          {scenario.discoverNames.map((name, index) => (
            <div
              key={name}
              className="zwig-stage-item flex items-center gap-3 rounded-2xl border border-[#e5e7eb] bg-white px-3 py-2.5"
              style={{ animationDelay: `${index * 100}ms` }}
            >
              <span className="h-2 w-2 shrink-0 rounded-full bg-[#f97316] zwig-pulse-dot" />
              <p className="truncate text-sm font-semibold text-[#334155]">{name}</p>
            </div>
          ))}
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-[#f1f5f9]">
          <div className="zwig-stage-progress h-full rounded-full bg-gradient-to-r from-[#f97316] via-[#fdba74] to-[#f97316]" />
        </div>
      </div>
    )
  }

  if (stage.id === 'call') {
    return (
      <div className="zwig-stage-panel space-y-3">
        <div className="flex items-center justify-between">
          <p className="text-sm font-bold text-[#111827]">Parallel supplier calls</p>
          <span className="text-xs font-bold tabular-nums text-[#64748b]">{scenario.stat.called} queued</span>
        </div>
        {scenario.calls.map((call, index) => (
          <div
            key={call.name}
            className={`zwig-stage-item flex items-center justify-between gap-3 rounded-2xl border px-4 py-3 ${
              call.active ? 'border-[#fed7aa] bg-[#fff7ed]' : 'border-[#e5e7eb] bg-white'
            }`}
            style={{ animationDelay: `${index * 140}ms` }}
          >
            <div className="min-w-0">
              <p className="truncate text-sm font-bold text-[#111827]">{call.name}</p>
              <p className="mt-0.5 truncate text-xs text-[#64748b]">{call.detail}</p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <WaveBars active={call.active} />
              <span
                className={`rounded-full px-2 py-0.5 text-[10px] font-black uppercase tracking-[0.1em] ${
                  call.active ? 'bg-[#111827] text-white' : 'bg-[#f1f5f9] text-[#94a3b8]'
                }`}
              >
                {call.active ? 'Live' : 'Ring'}
              </span>
            </div>
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="zwig-stage-panel space-y-3">
      <div className="grid grid-cols-3 gap-2">
        {[
          [scenario.stat.called, 'suppliers called'],
          [`${scenario.stat.minutes} min`, 'verified results'],
          [scenario.stat.best, 'best quote'],
        ].map(([value, label]) => (
          <div key={label} className="rounded-2xl border border-[#e5e7eb] bg-[#fbfaf8] p-3 text-center">
            <p className="text-base font-black tabular-nums text-[#111827] sm:text-lg">{value}</p>
            <p className="mt-1 text-[10px] font-bold uppercase tracking-[0.1em] text-[#94a3b8]">{label}</p>
          </div>
        ))}
      </div>
      {scenario.results.map((result, index) => (
        <div
          key={result.name}
          className="zwig-stage-item flex items-center justify-between gap-3 rounded-2xl border border-[#e5e7eb] bg-white px-4 py-3 shadow-sm"
          style={{ animationDelay: `${index * 120}ms` }}
        >
          <div className="flex min-w-0 items-center gap-3">
            <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-[#fff7ed] text-xs font-black text-[#f97316]">
              {result.rank}
            </span>
            <div className="min-w-0">
              <p className="truncate text-sm font-bold text-[#111827]">{result.name}</p>
              <p className="truncate text-xs text-[#64748b]">{result.meta}</p>
            </div>
          </div>
          <p className="shrink-0 text-sm font-black tabular-nums text-[#111827]">{result.price}</p>
        </div>
      ))}
    </div>
  )
}

function ProcurementLiveAnimation({ className = '' }) {
  const [stageIndex, setStageIndex] = useState(0)
  const [scenarioIndex, setScenarioIndex] = useState(0)
  const [cycleKey, setCycleKey] = useState(0)
  const [reducedMotion, setReducedMotion] = useState(false)

  const scenario = SCENARIOS[scenarioIndex]

  useEffect(() => {
    setReducedMotion(window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  }, [])

  useEffect(() => {
    if (reducedMotion) {
      return undefined
    }
    const timer = window.setInterval(() => {
      setStageIndex((current) => {
        if (current >= STAGES.length - 1) {
          setScenarioIndex((prev) => (prev + 1) % SCENARIOS.length)
          setCycleKey((prev) => prev + 1)
          return 0
        }
        return current + 1
      })
    }, STAGE_DURATION_MS)
    return () => window.clearInterval(timer)
  }, [reducedMotion])

  const selectScenario = (index) => {
    setScenarioIndex(index)
    setStageIndex(0)
    setCycleKey((prev) => prev + 1)
  }

  const selectStage = (index) => {
    setStageIndex(index)
    setCycleKey((prev) => prev + 1)
  }

  return (
    <div className={className}>
      <div className="mb-6 flex flex-wrap justify-center gap-2">
        {SCENARIOS.map((item, index) => (
          <button
            key={item.label}
            type="button"
            onClick={() => selectScenario(index)}
            className={`inline-flex items-center gap-2 rounded-full border px-4 py-2 text-sm font-bold transition ${
              scenarioIndex === index
                ? 'border-[#f97316] bg-[#fff7ed] text-[#c2410c] shadow-sm'
                : 'border-[#e5e7eb] bg-white text-[#64748b] hover:border-[#fdba74]'
            }`}
          >
            <span>{item.emoji}</span>
            {item.label}
          </button>
        ))}
      </div>

      <div className="relative overflow-hidden rounded-[32px] border border-[#e5e7eb] bg-white p-4 shadow-[0_28px_90px_-44px_rgba(15,23,42,0.45)] sm:p-6">
        <div className="mb-6 grid gap-2 sm:grid-cols-5">
          {STAGES.map((stage, index) => {
            const isActive = stageIndex === index
            const isComplete = stageIndex > index
            return (
              <button
                key={stage.id}
                type="button"
                onClick={() => selectStage(index)}
                className={`relative overflow-hidden rounded-[20px] border px-3 py-3 text-left transition-all duration-500 sm:px-4 sm:py-4 ${
                  isActive
                    ? 'zwig-stage-active border-[#f97316] bg-[#fff7ed] shadow-[0_12px_40px_-24px_rgba(249,115,22,0.8)]'
                    : isComplete
                      ? 'border-[#fed7aa] bg-white'
                      : 'border-[#e5e7eb] bg-[#fbfaf8] opacity-80'
                }`}
              >
                {isActive ? (
                  <span className="absolute inset-x-0 bottom-0 h-0.5 bg-[#f97316]">
                    <span className="zwig-stage-timer block h-full origin-left bg-[#ea580c]" key={`${cycleKey}-${index}`} />
                  </span>
                ) : null}
                <p className={`text-xs font-black uppercase tracking-[0.14em] ${isActive ? 'text-[#f97316]' : 'text-[#94a3b8]'}`}>
                  {stage.pill}
                </p>
                <p className={`mt-1.5 text-[11px] leading-4 sm:text-xs sm:leading-5 ${isActive ? 'font-semibold text-[#334155]' : 'text-[#64748b]'}`}>
                  {stage.caption}
                </p>
              </button>
            )
          })}
        </div>

        <div className="grid gap-6 lg:grid-cols-[1fr_0.95fr] lg:items-start">
          <div key={`panel-${cycleKey}-${stageIndex}`} className="min-h-[280px] rounded-[24px] border border-[#e5e7eb] bg-[#fbfaf8] p-4 sm:p-5">
            <StagePanel stageIndex={stageIndex} scenario={scenario} />
          </div>

          <div className="rounded-[24px] border border-[#111827] bg-[#111827] p-4 text-white sm:p-5">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <img src={BRAND_LOGO_192_URL} alt="" className="h-7 w-7 rounded-lg" />
                <p className="text-sm font-black">Zwig agent</p>
              </div>
              <span className="rounded-full bg-[#f97316] px-2.5 py-1 text-[10px] font-black uppercase tracking-[0.12em]">
                {STAGES[stageIndex].pill}
              </span>
            </div>

            <div className="space-y-3 text-sm" key={`feed-${cycleKey}-${stageIndex}`}>
              {stageIndex === 0 ? (
                <>
                  <div className="zwig-stage-item rounded-2xl bg-white/10 p-3">Parsing procurement intent from your search...</div>
                  <div className="zwig-stage-item ml-6 rounded-2xl bg-[#f97316] p-3">Matched to {scenario.label.toLowerCase()} suppliers in India.</div>
                </>
              ) : null}
              {stageIndex === 1 ? (
                <>
                  <div className="zwig-stage-item rounded-2xl bg-white/10 p-3">Need GST invoice and city-level dispatch?</div>
                  <div className="zwig-stage-item ml-6 rounded-2xl bg-[#f97316] p-3">Confirmed. Locking constraints before supplier outreach.</div>
                </>
              ) : null}
              {stageIndex === 2 ? (
                <>
                  <div className="zwig-stage-item rounded-2xl bg-white/10 p-3">Scanning supplier graph and callable vendors...</div>
                  <div className="zwig-stage-item ml-6 rounded-2xl bg-[#f97316] p-3">{scenario.discoverCount} candidates ranked by fit and reachability.</div>
                </>
              ) : null}
              {stageIndex === 3 ? (
                <>
                  <div className="zwig-stage-item rounded-2xl bg-white/10 p-3">Dialing {scenario.stat.called} suppliers in parallel...</div>
                  <div className="zwig-stage-item ml-6 rounded-2xl bg-[#f97316] p-3">Recording quotes, GST terms, and lead times live.</div>
                </>
              ) : null}
              {stageIndex >= 4 ? (
                <>
                  <div className="zwig-stage-item rounded-2xl bg-white/10 p-3">Normalizing price, GST, availability, and confidence...</div>
                  <div className="zwig-stage-item ml-6 rounded-2xl bg-[#f97316] p-3">Best quote: {scenario.stat.best} — proof attached.</div>
                </>
              ) : null}
            </div>

            <div className="mt-5 grid grid-cols-3 gap-2">
              {['Price', 'GST', 'Lead time'].map((label, index) => (
                <div
                  key={label}
                  className={`rounded-xl border p-3 transition ${
                    stageIndex >= 4 - index ? 'border-[#f97316]/40 bg-[#f97316]/10' : 'border-white/10 bg-white/5'
                  }`}
                >
                  <p className="text-[10px] uppercase tracking-[0.14em] text-white/50">{label}</p>
                  <p className="mt-1 text-xs font-bold">{stageIndex >= 4 - index ? 'Verified' : 'Pending'}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default ProcurementLiveAnimation

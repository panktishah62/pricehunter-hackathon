import { useState } from 'react'
import { trackAmplitudeEvent } from '../lib/amplitude'
import { convertPrice, formatPrice, formatResultPrice, formatResultPriceWithUnit } from '../lib/format'
import { getProductPreview, getResultDisplayName, getResultNotes, isLiveDealerRate } from '../lib/resultPresentation'

function DetailRow({ label, value }) {
  if (!label || !value) {
    return null
  }
  return (
    <div className="grid grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] gap-3 rounded-xl bg-white px-3 py-2 text-xs">
      <span className="text-slate-500">{label}</span>
      <span className="font-semibold text-slate-900">{value}</span>
    </div>
  )
}

function formatRate(value, result, rate, displayCurrency = 'INR') {
  const selectedCurrency = displayCurrency || rate?.display_currency || result?.display_currency || result?.currency || 'INR'
  const numericValue = Number(value)
  const displayValue =
    Number.isFinite(numericValue) ? convertPrice(numericValue, result?.currency || 'INR', selectedCurrency) : null
  return formatPrice(displayValue, selectedCurrency) || '—'
}

function isGoogleSearchUrl(value) {
  if (!value) {
    return false
  }
  try {
    const parsed = new URL(value)
    const hostname = parsed.hostname.replace(/^www\./, '').toLowerCase()
    if (!hostname.endsWith('google.com')) {
      return false
    }
    return parsed.pathname === '/search' || parsed.searchParams.has('tbm') || parsed.searchParams.has('udm')
  } catch {
    return false
  }
}

function formatUpdatedAt(value) {
  if (!value) {
    return 'Updated recently'
  }
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return 'Updated recently'
  }
  return `Updated ${parsed.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })}`
}

function formatQuantity(rate) {
  const quantity = rate?.quantity_grams
  if (!quantity) {
    return ''
  }
  const numericQuantity = Number(quantity)
  if (!Number.isFinite(numericQuantity)) {
    return ''
  }
  if (numericQuantity >= 1000 && numericQuantity % 1000 === 0) {
    return `${numericQuantity / 1000} kg`
  }
  return `${numericQuantity} g`
}

function GoldLiveRatesBoard({ result, terms, displayCurrency = 'INR' }) {
  terms = terms || result?.gold_terms || {}
  const rawRates = Array.isArray(terms.rates) && terms.rates.length > 0 ? terms.rates : [terms]
  const seen = new Set()
  const rates = rawRates.filter((rate) => {
    const key = `${rate?.script_name || ''}|${rate?.sell_rate ?? ''}|${rate?.purity || ''}|${rate?.quantity_grams ?? ''}`
    if (seen.has(key)) {
      return false
    }
    seen.add(key)
    return true
  })
  const visibleRates = rates.filter((rate) => rate?.script_name)
  const sourceUrl = terms.source_url || result?.url

  return (
    <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
      <div className="border-b border-slate-200 bg-slate-950 p-3 text-white">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[10px] font-black uppercase tracking-[0.22em] text-amber-200">Live rates</p>
            <p className="mt-1 text-sm font-black">{terms.vendor_name || result.name}</p>
            <p className="mt-1 text-xs text-slate-300">
              {visibleRates.length} product rate{visibleRates.length === 1 ? '' : 's'} from this dealer
            </p>
          </div>
          {terms.updated_at ? <p className="text-xs font-semibold text-slate-300">{formatUpdatedAt(terms.updated_at)}</p> : null}
        </div>
      </div>

      {visibleRates.length > 0 ? (
        <div className="max-h-[420px] overflow-auto">
          <div className="grid grid-cols-[minmax(0,1fr)_120px] gap-3 border-b border-slate-200 bg-slate-50 px-3 py-2 text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">
            <span>Product</span>
            <span className="text-right">Sell</span>
          </div>
          {visibleRates.map((rate, index) => {
            const quantity = formatQuantity(rate)
            const meta = [rate.purity, quantity || rate.unit].filter(Boolean).join(' · ')
            const key = `${rate.script_name || 'Live Script'}-${rate.updated_at || index}`
            return (
              <div
                key={key}
                className="grid grid-cols-[minmax(0,1fr)_120px] gap-3 border-b border-slate-100 px-3 py-3 last:border-b-0"
              >
                <div className="min-w-0">
                  <p className="text-sm font-black leading-5 text-slate-950">{rate.script_name || 'Live Script'}</p>
                  {meta ? <p className="mt-1 text-xs font-semibold text-slate-500">{meta}</p> : null}
                </div>
                <div className="text-right">
                  <p className="font-display text-lg font-black text-slate-950">{formatRate(rate.sell_rate, result, rate, displayCurrency)}</p>
                </div>
              </div>
            )
          })}
        </div>
      ) : (
        <p className="p-3 text-sm text-slate-600">No sell rates are available for this dealer right now.</p>
      )}

      {sourceUrl ? (
        <div className="border-t border-slate-200 px-3 py-3">
          <a
            href={sourceUrl}
            target="_blank"
            rel="noreferrer"
            className="inline-flex rounded-full bg-slate-950 px-3 py-1.5 text-xs font-bold text-white transition hover:bg-slate-800"
          >
            Open dealer site
          </a>
        </div>
      ) : null}
    </div>
  )
}

const PRICE_ON_REQUEST_LABEL = 'Price on request'

function RichProductPreview({ preview, result, displayCurrency = 'INR', notes = '', showHeader = false }) {
  const title = preview.title || getResultDisplayName(result)
  const pricedLabel =
    formatResultPriceWithUnit(result, displayCurrency, preview.unit) ||
    formatResultPrice(result, displayCurrency)
  const hasPrice = Boolean(pricedLabel)
  const priceLabel = pricedLabel || PRICE_ON_REQUEST_LABEL

  return (
    <div className="overflow-hidden bg-slate-100">
      {showHeader ? (
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 bg-white px-4 py-3">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-slate-900">{title}</p>
            {notes ? <p className="mt-2 text-sm text-slate-600">{notes}</p> : null}
          </div>
          <div className="text-right">
            <p className="text-sm font-semibold text-slate-900">{priceLabel}</p>
            {result.delivery_time ? <p className="mt-1 text-xs text-slate-500">{result.delivery_time}</p> : null}
          </div>
        </div>
      ) : null}

      <div className="grid gap-3 p-3 sm:grid-cols-[120px_minmax(0,1fr)]">
        {preview.image_url ? (
          <img
            src={preview.image_url}
            alt={title || 'Product image'}
            className="h-28 w-full rounded-xl bg-white object-contain sm:h-full"
            loading="lazy"
          />
        ) : null}
        <div>
          {!showHeader ? <p className="text-sm font-bold text-slate-950">{title}</p> : null}
          <div className={`flex flex-wrap gap-2 text-xs ${showHeader ? '' : 'mt-2'}`}>
            {!showHeader ? (
              <span
                className={`rounded-full px-2.5 py-1 font-bold ${
                  hasPrice ? 'bg-slate-950 text-white' : 'bg-slate-200 text-slate-900'
                }`}
              >
                {priceLabel}
              </span>
            ) : null}
            {preview.available ? (
              <span className="rounded-full bg-emerald-100 px-2.5 py-1 font-semibold text-emerald-800">
                {preview.available}
              </span>
            ) : null}
            {preview.supplier_city ? (
              <span className="rounded-full bg-white px-2.5 py-1 font-semibold text-slate-700">
                {preview.supplier_city}
              </span>
            ) : null}
          </div>
          {preview.description ? (
            <p className="mt-3 max-h-24 overflow-hidden text-xs leading-5 text-slate-700">{preview.description}</p>
          ) : null}
        </div>
      </div>

      {preview.specs?.length ? (
        <div className="grid gap-1.5 border-t border-slate-200 p-3">
          <p className="mb-1 text-[10px] font-bold uppercase tracking-[0.2em] text-slate-500">Product specs</p>
          {preview.specs.slice(0, 10).map((item) => (
            <DetailRow key={`${item.label}-${item.value}`} label={item.label} value={item.value} />
          ))}
        </div>
      ) : null}

      <div className="grid gap-2 border-t border-slate-200 p-3 text-xs text-slate-700">
        <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-slate-500">Supplier details</p>
        {preview.supplier_name ? <p className="font-bold text-slate-950">{preview.supplier_name}</p> : null}
        {preview.supplier_phone ? (
          <p>
            Vendor number: <span className="font-bold text-slate-950">{preview.supplier_phone}</span>
          </p>
        ) : null}
        {preview.supplier_address ? <p>{preview.supplier_address}</p> : null}
        <div className="flex flex-wrap gap-1.5">
          {preview.supplier_rating ? (
            <span className="rounded-full bg-white px-2 py-1 font-semibold">
              Rating {preview.supplier_rating}
              {preview.supplier_rating_count ? ` (${preview.supplier_rating_count})` : ''}
            </span>
          ) : null}
          {preview.response_rate ? (
            <span className="rounded-full bg-white px-2 py-1 font-semibold">{preview.response_rate}% response</span>
          ) : null}
          {preview.trust_badges?.slice(0, 3).map((badge) => (
            <span key={badge} className="rounded-full bg-white px-2 py-1 font-semibold">
              {badge}
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}

function ProductPreviewCard({ result, apiBaseUrl, displayCurrency = 'INR', layout = 'inline' }) {
  const initialPreview = getProductPreview(result)
  const hasEmbeddedPreview = Boolean(result?.attributes?.product_preview)
  const notes = getResultNotes(result)
  const isFullLayout = layout === 'full'
  const [preview, setPreview] = useState(initialPreview)
  const [goldTerms, setGoldTerms] = useState(result?.gold_terms || null)
  const [status, setStatus] = useState(initialPreview ? 'loaded' : 'idle')
  const [expanded, setExpanded] = useState(Boolean(initialPreview))
  const isGoldLiveRate = isLiveDealerRate(result)

  if (isGoldLiveRate) {
    const loadGoldRates = async () => {
      if (goldTerms) {
        setExpanded((value) => !value)
        return
      }
      if (!apiBaseUrl || (!result?.vendor_id && !result?.url)) {
        setExpanded((value) => !value)
        return
      }
      setStatus('loading')
      try {
        const response = await fetch(`${apiBaseUrl}/api/gold-live-rate-preview`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            vendor_id: result.vendor_id || null,
            source_url: result.url || null,
            city: result.city || null,
          }),
        })
        if (!response.ok) {
          throw new Error(`Live-rate preview failed with status ${response.status}`)
        }
        const payload = await response.json()
        const nextTerms = {
          vendor_name: payload.vendor_name || result.name,
          source_url: payload.source_url || result.url,
          updated_at: payload.updated_at,
          rates: payload.rates || [],
        }
        setGoldTerms(nextTerms)
        setExpanded(true)
        setStatus('loaded')
        trackAmplitudeEvent('Gold Live Rates Expanded', {
          resultId: result.id,
          rateCount: nextTerms.rates?.length || 0,
        })
      } catch (error) {
        console.error(error)
        setStatus('failed')
      }
    }

    return (
      <div className={isFullLayout ? '' : 'mt-3 space-y-3'}>
        {preview ? (
          <RichProductPreview
            preview={preview}
            result={result}
            displayCurrency={displayCurrency}
            notes={notes}
            showHeader={isFullLayout}
          />
        ) : null}
        <div className={isFullLayout ? 'border-t border-slate-200 px-4 py-3' : ''}>
          <button
            type="button"
            onClick={loadGoldRates}
            disabled={status === 'loading'}
            className="inline-flex items-center rounded-full border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs font-black text-amber-900 transition hover:border-amber-400 hover:bg-amber-100"
          >
            {status === 'loading'
              ? 'Loading live rates...'
              : expanded
                ? 'Hide live rates'
                : 'Show live rates'}
          </button>
        </div>
        {status === 'failed' ? (
          <p className={`text-xs text-rose-600 ${isFullLayout ? 'px-4 pb-3' : ''}`}>
            Could not load the full live-rate board right now.
          </p>
        ) : null}
        {expanded ? (
          <div className={isFullLayout ? 'px-4 pb-4' : ''}>
            <GoldLiveRatesBoard result={result} terms={goldTerms} displayCurrency={displayCurrency} />
          </div>
        ) : null}
      </div>
    )
  }

  const canFetchPreview = Boolean(result?.url && apiBaseUrl && !isGoogleSearchUrl(result.url) && !hasEmbeddedPreview)

  const loadPreview = async () => {
    if (preview) {
      setExpanded((value) => !value)
      return
    }
    if (!canFetchPreview) {
      setExpanded((value) => !value)
      return
    }
    setStatus('loading')
    try {
      const response = await fetch(`${apiBaseUrl}/api/product-preview`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: result.url }),
      })
      if (!response.ok) {
        throw new Error(`Preview failed with status ${response.status}`)
      }
      const payload = await response.json()
      setPreview(getProductPreview({ ...result, attributes: { ...(result.attributes || {}), product_preview: payload } }))
      setExpanded(true)
      setStatus('loaded')
      trackAmplitudeEvent('Product Details Expanded', {
        resultId: result.id,
        hasImage: Boolean(payload.image_url),
        specCount: payload.specs?.length || 0,
      })
    } catch (error) {
      console.error(error)
      setStatus('failed')
    }
  }

  if (!preview && !canFetchPreview) {
    return null
  }

  if (isFullLayout) {
    return (
      <>
        {expanded && preview ? (
          <RichProductPreview
            preview={preview}
            result={result}
            displayCurrency={displayCurrency}
            notes={notes}
            showHeader
          />
        ) : null}
        {canFetchPreview ? (
          <div className="border-t border-slate-200 px-4 py-3">
            <button
              type="button"
              onClick={loadPreview}
              disabled={status === 'loading'}
              className="inline-flex items-center rounded-full border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-800 transition hover:border-slate-400 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {status === 'loading' ? 'Loading details...' : expanded ? 'Hide product details' : 'Show product details'}
            </button>
          </div>
        ) : null}
        {status === 'failed' ? (
          <p className="px-4 pb-3 text-xs text-rose-600">Could not load inline details. The product link is still available.</p>
        ) : null}
      </>
    )
  }

  return (
    <div className="mt-3">
      {canFetchPreview ? (
        <button
          type="button"
          onClick={loadPreview}
          disabled={status === 'loading'}
          className="inline-flex items-center rounded-full border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-800 transition hover:border-slate-400 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {status === 'loading' ? 'Loading details...' : expanded ? 'Hide product details' : 'Show product details'}
        </button>
      ) : null}

      {status === 'failed' ? (
        <p className="mt-2 text-xs text-rose-600">Could not load inline details. The product link is still available.</p>
      ) : null}

      {expanded && preview ? (
        <div className={canFetchPreview ? 'mt-3' : ''}>
          <RichProductPreview preview={preview} result={result} displayCurrency={displayCurrency} />
        </div>
      ) : null}
    </div>
  )
}

export default ProductPreviewCard

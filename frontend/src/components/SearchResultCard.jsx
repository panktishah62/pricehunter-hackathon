import { trackAmplitudeEvent } from '../lib/amplitude'
import { formatResultPrice } from '../lib/format'
import { getResultLinkLabel, isIndiaMartUrl } from '../lib/resultPresentation'
import ProductPreviewCard from './ProductPreviewCard'

function SearchResultCard({ result, apiBaseUrl, displayCurrency = 'INR' }) {
  const showProductLink = result.url && !isIndiaMartUrl(result.url)
  const hasPrice = Boolean(formatResultPrice(result, displayCurrency))

  return (
    <div className="overflow-hidden rounded-2xl border border-slate-200 bg-slate-50">
      <ProductPreviewCard
        result={result}
        apiBaseUrl={apiBaseUrl}
        displayCurrency={displayCurrency}
        layout="full"
      />

      <div className="space-y-2 border-t border-slate-200 px-4 py-3">
        {!hasPrice ? (
          <p className="text-xs font-bold leading-4 text-slate-800">
            Availability checked. Contact the vendor for exact price and product specifications.
          </p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          {showProductLink ? (
            <a
              href={result.url}
              target="_blank"
              rel="noreferrer"
              onClick={() => {
                trackAmplitudeEvent('Product Link Opened', {
                  resultId: result.id,
                  sourceType: result.source_type,
                  hasPrice: result.price !== null && result.price !== undefined,
                  hasDeliveryTime: Boolean(result.delivery_time),
                })
              }}
              className="inline-flex items-center rounded-full bg-slate-900 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-slate-800"
            >
              {getResultLinkLabel(result)}
            </a>
          ) : null}
          {result.phone ? (
            <a
              href={`tel:${result.phone}`}
              onClick={() => {
                trackAmplitudeEvent('Vendor Call Clicked', {
                  resultId: result.id,
                  sourceType: result.source_type,
                  hasPrice: result.price !== null && result.price !== undefined,
                })
              }}
              className="inline-flex items-center rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:border-slate-300 hover:bg-slate-100"
            >
              Call vendor
            </a>
          ) : null}
        </div>
      </div>
    </div>
  )
}

export default SearchResultCard

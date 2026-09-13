function includesAny(value, needles) {
  const normalized = String(value || '').toLowerCase()
  return needles.some((needle) => normalized.includes(needle))
}

function hasLiveRateQuoteFields(value) {
  return /(^|\|)\s*(buy|sell)=/i.test(String(value || ''))
}

export function sanitizeMarketplaceText(value) {
  return String(value || '')
    .replace(/\s*\|\s*Source:\s*[^|]+/gi, '')
    .replace(/\bSource:\s*[^|.\n]+[|.\n]?/gi, '')
    .replace(/\b(?:IndiaMART|India Mart|Google Shopping|google_shopping|SerpAPI)\b/gi, 'Market listing')
    .replace(/\bPlatform:\s*[^|.\n]+[|.\n]?/gi, '')
    .replace(/\s{2,}/g, ' ')
    .trim()
}

function splitVendorProductName(name) {
  const parts = String(name || '')
    .split('|')
    .map((part) => sanitizeMarketplaceText(part))
    .filter(Boolean)
  if (parts.length >= 2) {
    return {
      supplier: parts[0],
      product: parts.slice(1).join(' | '),
    }
  }
  return {
    supplier: parts[0] || '',
    product: parts[0] || '',
  }
}

function extractMoqFromNotes(notes) {
  const match = String(notes || '').match(/MOQ:\s*([^|]+)/i)
  return match?.[1]?.trim() || ''
}

function isTechnicalNote(value) {
  const normalized = String(value || '').toLowerCase()
  return (
    normalized.includes('product match score') ||
    normalized.includes('last price:') ||
    normalized.includes('vendor bucket:') ||
    normalized.includes('live-rate script available') ||
    normalized.includes('chirayu_api') ||
    hasLiveRateQuoteFields(value)
  )
}

function compactSpecLabel(label) {
  return String(label || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '')
}

function isUnitSpecLabel(label) {
  const compact = compactSpecLabel(label)
  return compact === 'unit' || compact === 'units' || compact === 'priceunit' || compact === 'packagingunit'
}

export function isInternalSpecLabel(label) {
  const normalized = String(label || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
  if (!normalized) {
    return true
  }
  const compact = normalized.replace(/\s+/g, '')
  if (compact.includes('indiamart')) {
    return true
  }
  if (['id', 'mcat', 'catid', 'mcatid', 'source', 'sourceurl', 'url', 'link'].includes(compact)) {
    return true
  }
  if (compact.endsWith('id') && ['cat', 'mcat', 'listing', 'product', 'supplier', 'vendor'].some((token) => compact.includes(token))) {
    return true
  }
  return false
}

function isNonProductSpecLabel(label) {
  const compact = compactSpecLabel(label)
  return compact === 'availability' || compact === 'suppliertype'
}

function isHiddenTrustBadge(badge) {
  return /^trustseal\b/i.test(String(badge || '').trim())
}

function preparePreviewSpecs(specs, explicitUnit = null) {
  let unit = String(explicitUnit || '').trim()
  const visible = []

  for (const item of specs || []) {
    const label = item?.label
    const value = item?.value
    if (!label || value === null || value === undefined || value === '') {
      continue
    }
    if (isUnitSpecLabel(label)) {
      if (!unit) {
        unit = String(value).trim()
      }
      continue
    }
    if (isInternalSpecLabel(label) || isNonProductSpecLabel(label)) {
      continue
    }
    visible.push({ label: String(label), value: String(value) })
  }

  return { specs: visible, unit }
}

function normalizePreview(preview, result) {
  const { supplier, product } = splitVendorProductName(result?.name)
  const moq = preview.moq || extractMoqFromNotes(result?.notes)
  const preparedSpecs = preparePreviewSpecs(
    Array.isArray(preview.specs) ? preview.specs : [],
    preview.unit,
  )
  return {
    title: sanitizeMarketplaceText(preview.title || product || result?.name) || 'Product listing',
    image_url: preview.image_url || null,
    price: preview.price ?? result?.price ?? null,
    currency: preview.currency || result?.currency || 'INR',
    unit: preparedSpecs.unit || null,
    available:
      preview.available ||
      (result?.availability === false ? 'Unavailable' : result?.price ? 'In Stock' : 'Price on request'),
    description: sanitizeMarketplaceText(preview.description || ''),
    specs: preparedSpecs.specs,
    supplier_name: sanitizeMarketplaceText(preview.supplier_name || supplier || result?.name) || '',
    supplier_phone: preview.supplier_phone || result?.phone || '',
    supplier_address: preview.supplier_address || result?.address || '',
    supplier_city: preview.supplier_city || result?.city || '',
    supplier_rating: preview.supplier_rating ?? null,
    supplier_rating_count: preview.supplier_rating_count ?? null,
    response_rate: preview.response_rate ?? null,
    trust_badges: (Array.isArray(preview.trust_badges) ? preview.trust_badges : []).filter(
      (badge) => !isHiddenTrustBadge(badge),
    ),
    moq,
  }
}

export function getProductPreview(result) {
  if (!result) {
    return null
  }

  const embedded = result.attributes?.product_preview
  if (embedded && typeof embedded === 'object') {
    return normalizePreview(embedded, result)
  }

  const { supplier, product } = splitVendorProductName(result.name)
  const noteParts = String(result.notes || '')
    .split('|')
    .map((part) => sanitizeMarketplaceText(part.trim()))
    .filter(Boolean)
  const supplierFromNotes = noteParts.find((part) => part && !isTechnicalNote(part) && !/^MOQ:/i.test(part))

  return normalizePreview(
    {
      title: product || result.name,
      image_url: result.attributes?.image_url || null,
      description: result.attributes?.description || '',
      specs: result.attributes?.specs || [],
      supplier_name: supplierFromNotes || supplier || product,
      supplier_phone: result.phone || '',
      supplier_address: result.address || '',
      supplier_city: result.city || '',
      moq: extractMoqFromNotes(result.notes),
    },
    result,
  )
}

export function getResultDisplayName(result) {
  if (isLiveDealerRate(result)) {
    return sanitizeMarketplaceText(result?.name) || 'Live dealer rate'
  }
  return getProductPreview(result)?.title || sanitizeMarketplaceText(result?.name) || 'Vendor listing'
}

export function isLiveDealerRate(result) {
  if (!result) {
    return false
  }
  return (
    result.result_type === 'live_rate' ||
    includesAny(result.delivery_time, ['live dealer', 'website quote']) ||
    includesAny(result.notes, ['live dealer', 'live-rate', 'chirayu_api']) ||
    hasLiveRateQuoteFields(result.notes)
  )
}

export function getResultSourceLabel(result) {
  if (isLiveDealerRate(result)) {
    return 'Live dealer rate'
  }
  if (result?.source_type === 'offline') {
    return 'Local dealer'
  }
  return 'Online'
}

export function getResultLinkLabel(result) {
  if (isLiveDealerRate(result)) {
    return 'Open dealer site'
  }
  return 'View product'
}

export function isIndiaMartUrl(value) {
  if (!value) {
    return false
  }
  try {
    const parsed = new URL(value)
    const hostname = parsed.hostname.replace(/^www\./, '').toLowerCase()
    return hostname === 'indiamart.com' || hostname.endsWith('.indiamart.com')
  } catch {
    return false
  }
}

function formatShortDate(value) {
  if (!value) {
    return ''
  }
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return ''
  }
  return parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export function getResultNotes(result) {
  if (isLiveDealerRate(result)) {
    const notes = sanitizeMarketplaceText(result?.notes || '')
    if (includesAny(notes, ['chirayu_api', 'live dealer rate from']) || hasLiveRateQuoteFields(notes)) {
      const updatedMatch = notes.match(/updated=([^|]+)/i)
      const shortDate = formatShortDate(updatedMatch?.[1]?.trim())
      return shortDate
        ? `Live dealer sell rate from the vendor site. Last updated ${shortDate}.`
        : 'Live dealer sell rate from the vendor site.'
    }
    if (includesAny(notes, ['vendor bucket:', 'live-rate script available'])) {
      return 'Dealer has a live-rate site, but no matching price snapshot yet.'
    }
    return notes
  }

  const preview = getProductPreview(result)
  const parts = []
  if (preview?.supplier_name) {
    parts.push(preview.supplier_name)
  }
  if (preview?.moq) {
    parts.push(`MOQ: ${preview.moq}`)
  }
  if (parts.length) {
    return parts.join(' | ')
  }

  const notes = sanitizeMarketplaceText(result?.notes || '')
  if (!notes || isTechnicalNote(notes)) {
    return ''
  }
  return notes
}

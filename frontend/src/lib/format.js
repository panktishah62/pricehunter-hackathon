const SUPPORTED_DISPLAY_CURRENCIES = new Set(['INR', 'USD', 'AED'])
const INR_RATES = {
  INR: 1,
  USD: 0.012,
  AED: 0.044,
}

export function formatPrice(value, currency = 'INR') {
  if (value === null || value === undefined) {
    return null
  }

  const resolvedCurrency = currency || 'INR'

  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: resolvedCurrency,
    maximumFractionDigits: resolvedCurrency === 'INR' ? 0 : 2,
  }).format(value)
}

export function normalizeDisplayCurrency(currency) {
  const normalized = String(currency || 'INR').toUpperCase()
  return SUPPORTED_DISPLAY_CURRENCIES.has(normalized) ? normalized : 'INR'
}

export function convertPrice(value, fromCurrency = 'INR', toCurrency = 'INR') {
  if (value === null || value === undefined) {
    return null
  }
  const source = normalizeDisplayCurrency(fromCurrency)
  const target = normalizeDisplayCurrency(toCurrency)
  const numericValue = Number(value)
  if (!Number.isFinite(numericValue)) {
    return null
  }
  if (source === target) {
    return numericValue
  }
  const inrValue = source === 'INR' ? numericValue : numericValue / (INR_RATES[source] || 1)
  return inrValue * (INR_RATES[target] || 1)
}

export function formatResultPrice(result, displayCurrency = null) {
  if (!result) {
    return null
  }
  const requestedCurrency = displayCurrency ? normalizeDisplayCurrency(displayCurrency) : null
  const currency = requestedCurrency || result.display_currency || result.currency || 'INR'
  const value = requestedCurrency
    ? convertPrice(result.price, result.currency || 'INR', requestedCurrency)
    : result.display_price ?? result.price
  return formatPrice(value, currency)
}

export function formatResultPriceWithUnit(result, displayCurrency = null, unit = null) {
  const price = formatResultPrice(result, displayCurrency)
  if (!price) {
    return null
  }
  const cleanUnit = String(unit || '').trim()
  if (!cleanUnit) {
    return price
  }
  return `${price} / ${cleanUnit}`
}

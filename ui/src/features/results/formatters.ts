// Display formatting for result cells. Dates are shown in the business timezone, the same
// calendar the backend uses for "today" and "this month".
export const BUSINESS_TIMEZONE = 'America/New_York'

const dateTimeFormat = new Intl.DateTimeFormat('en-US', {
  timeZone: BUSINESS_TIMEZONE,
  year: 'numeric',
  month: 'short',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
})

// Table cells leave out the year: only the current year is searchable.
const compactDateTimeFormat = new Intl.DateTimeFormat('en-US', {
  timeZone: BUSINESS_TIMEZONE,
  month: 'short',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
})

function formatTimestamp(isoTimestamp: string | null | undefined, format: Intl.DateTimeFormat): string {
  if (!isoTimestamp) return '—'
  const moment = new Date(isoTimestamp)
  return Number.isNaN(moment.getTime()) ? isoTimestamp : format.format(moment)
}

export function formatDateTime(isoTimestamp: string | null | undefined): string {
  return formatTimestamp(isoTimestamp, dateTimeFormat)
}

export function formatCompactDateTime(isoTimestamp: string | null | undefined): string {
  return formatTimestamp(isoTimestamp, compactDateTimeFormat)
}

// Amounts arrive as decimal strings (exact); format without losing that exactness for display.
export function formatMoney(amount: string | null | undefined, currency: string | null | undefined): string {
  if (amount === null || amount === undefined) return '—'
  const value = Number(amount)
  if (!Number.isFinite(value)) return amount
  try {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: currency || 'USD' }).format(value)
  } catch {
    return `${amount} ${currency ?? ''}`.trim() // unknown currency code
  }
}

export function formatCode(value: string | null | undefined): string {
  return value ? value.replaceAll('_', ' ') : '—'
}

const PAYMENT_METHOD_LABELS: Record<string, string> = { CARD: 'Card', WALLET: 'Wallet', ACH: 'ACH' }

// One short "paid with" label: the card brand for card payments, otherwise the method.
export function formatPaidWith(method: string | null | undefined, cardBrand: string | null | undefined): string {
  if (method === 'CARD' && cardBrand) return cardBrand.charAt(0) + cardBrand.slice(1).toLowerCase()
  if (!method) return '—'
  return PAYMENT_METHOD_LABELS[method] ?? formatCode(method)
}

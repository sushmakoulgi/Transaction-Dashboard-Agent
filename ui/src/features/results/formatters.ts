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

export function formatDateTime(isoTimestamp: string | null | undefined): string {
  if (!isoTimestamp) return '—'
  const moment = new Date(isoTimestamp)
  return Number.isNaN(moment.getTime()) ? isoTimestamp : dateTimeFormat.format(moment)
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

export function formatYesNo(value: boolean): string {
  return value ? 'Yes' : 'No'
}

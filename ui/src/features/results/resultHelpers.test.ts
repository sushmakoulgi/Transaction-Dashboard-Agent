import { describe, expect, it } from 'vitest'

import type { QueryPlan } from '../../api/agentApiTypes'
import { formatCode, formatCompactDateTime, formatDateTime, formatMoney, formatPaidWith } from './formatters'
import { nextSort, sortOfPlan, withSort } from './planSorting'

const DETAIL_STEP = { tool_name: 'get_transaction_detail' as const, transaction_id: '400000000000001' }

describe('plan sorting', () => {
  it('replaces the sort of the last search only', () => {
    const plan: QueryPlan = {
      steps: [
        { tool_name: 'search_transactions', filter: null, sort: null },
        { tool_name: 'search_chargebacks', filter: null, sort: { field: 'opened_at', direction: 'desc' } },
        DETAIL_STEP,
      ],
    }
    const sorted = withSort(plan, { field: 'chargeback_amount', direction: 'asc' })
    expect(sorted.steps[0]).toEqual(plan.steps[0])
    expect(sorted.steps[1]).toMatchObject({ sort: { field: 'chargeback_amount', direction: 'asc' } })
    expect(sorted.steps[2]).toEqual(DETAIL_STEP)
    expect(sortOfPlan(plan)).toEqual({ field: 'opened_at', direction: 'desc' })
    expect(plan.steps[1]).toMatchObject({ sort: { field: 'opened_at' } }) // original untouched
  })

  it('leaves a plan without a search unchanged', () => {
    const plan: QueryPlan = { steps: [DETAIL_STEP] }
    expect(withSort(plan, { field: 'amount', direction: 'asc' })).toBe(plan)
    expect(sortOfPlan(plan)).toBeNull()
  })

  it('sorts a new column descending first, then toggles', () => {
    expect(nextSort(null, 'amount')).toEqual({ field: 'amount', direction: 'desc' })
    expect(nextSort({ field: 'amount', direction: 'desc' }, 'amount')).toEqual({ field: 'amount', direction: 'asc' })
    expect(nextSort({ field: 'amount', direction: 'asc' }, 'transaction_date')).toEqual({
      field: 'transaction_date',
      direction: 'desc',
    })
  })
})

describe('formatters', () => {
  it('formats money exactly from decimal strings', () => {
    expect(formatMoney('1234.5', 'USD')).toBe('$1,234.50')
    expect(formatMoney('99.00', 'GBP')).toBe('£99.00')
    expect(formatMoney('10', 'XYZ1')).toBe('10 XYZ1') // not a valid currency code
    expect(formatMoney(null, 'USD')).toBe('—')
  })

  it('shows times in New York business time', () => {
    expect(formatDateTime('2026-12-15T14:00:00Z')).toBe('Dec 15, 2026, 09:00 AM') // EST
    expect(formatDateTime('2026-07-15T13:00:00Z')).toBe('Jul 15, 2026, 09:00 AM') // EDT
    expect(formatDateTime(null)).toBe('—')
    expect(formatDateTime('not a date')).toBe('not a date')
  })

  it('leaves the year out of compact table dates', () => {
    expect(formatCompactDateTime('2026-12-15T14:00:00Z')).toBe('Dec 15, 09:00 AM')
    expect(formatCompactDateTime(null)).toBe('—')
  })

  it('shows the card brand for card payments, otherwise the method', () => {
    expect(formatPaidWith('CARD', 'MASTERCARD')).toBe('Mastercard')
    expect(formatPaidWith('CARD', null)).toBe('Card')
    expect(formatPaidWith('WALLET', 'AMEX')).toBe('Wallet')
    expect(formatPaidWith('ACH', null)).toBe('ACH')
    expect(formatPaidWith(null, null)).toBe('—')
  })

  it('makes codes readable', () => {
    expect(formatCode('UNDER_REVIEW')).toBe('UNDER REVIEW')
    expect(formatCode(null)).toBe('—')
  })
})

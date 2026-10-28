import { describe, expect, it } from 'vitest'

import type { QueryPlan } from '../../api/agentApiTypes'
import { describeFilter, describeSort, filterChips } from './describePlan'

const condition = (field: string, operator: string, value: unknown) =>
  ({ kind: 'condition', field, operator, value }) as Parameters<typeof describeFilter>[0]

const planWith = (filter: unknown): QueryPlan =>
  ({ steps: [{ tool_name: 'search_chargebacks', filter, sort: null }] }) as QueryPlan

describe('describeFilter', () => {
  it.each([
    [condition('customer_id', 'eq', '10033317'), 'customer = 10033317'],
    [condition('amount', 'gt', 500), 'amount > 500'],
    [condition('payment_status', 'in', ['FAILED', 'VOIDED']), 'payment status is one of FAILED, VOIDED'],
    [condition('merchant_name', 'contains', 'roger'), 'merchant name contains roger'],
    [condition('opened_at', 'between', { relative: 'this_month' }), 'opened this month'],
    [condition('transaction_date', 'between', { last_n_days: 7 }), 'date in the last 7 days'],
    [condition('transaction_date', 'between', ['2026-10-01', '2026-10-15']), 'date 2026-10-01 – 2026-10-15'],
    [condition('has_refund', 'exists', true), 'has refund'],
    [condition('has_chargeback', 'exists', false), 'no chargeback'],
    [condition('captured_at', 'is_null', true), 'captured is empty'],
  ])('describes a condition', (node, expected) => {
    expect(describeFilter(node)).toBe(expected)
  })

  it('describes OR, NOT and nested groups', () => {
    const tree = {
      kind: 'group',
      logic: 'and',
      negate: false,
      children: [
        condition('customer_id', 'eq', 'C-1'),
        {
          kind: 'group',
          logic: 'or',
          negate: false,
          children: [condition('has_refund', 'exists', true), condition('amount', 'gt', 500)],
        },
        { kind: 'group', logic: 'and', negate: true, children: [condition('card_brand', 'eq', 'AMEX')] },
      ],
    } as Parameters<typeof describeFilter>[0]
    expect(describeFilter(tree)).toBe('customer = C-1 and (has refund or amount > 500) and not (card brand = AMEX)')
  })
})

describe('filterChips', () => {
  it('gives one chip per top-level AND condition', () => {
    const plan = planWith({
      kind: 'group',
      logic: 'and',
      negate: false,
      children: [condition('customer_id', 'eq', '10033317'), condition('opened_at', 'between', { relative: 'this_month' })],
    })
    expect(filterChips(plan)).toEqual(['customer = 10033317', 'opened this month'])
  })

  it('keeps an OR as one chip and handles no filter', () => {
    const plan = planWith({
      kind: 'group',
      logic: 'or',
      negate: false,
      children: [condition('amount', 'gt', 1), condition('amount', 'lt', 0)],
    })
    expect(filterChips(plan)).toEqual(['amount > 1 or amount < 0'])
    expect(filterChips(planWith(null))).toEqual([])
  })
})

describe('describeSort', () => {
  it('names the field and direction', () => {
    expect(describeSort(null)).toBe('newest first')
    expect(describeSort({ field: 'amount', direction: 'asc' })).toBe('amount ↑')
    expect(describeSort({ field: 'opened_at', direction: 'desc' })).toBe('opened ↓')
  })
})

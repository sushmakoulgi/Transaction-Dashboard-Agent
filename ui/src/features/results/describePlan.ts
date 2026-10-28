// Readable text for a plan's filter and sort, for the summary bar above the results.
import type { QueryPlan } from '../../api/agentApiTypes'
import type { components } from '../../api/agent-api-schema'
import type { SortSpec } from './planSorting'

type Schemas = components['schemas']
type FilterCondition = Schemas['FilterCondition']
type FilterNode = FilterCondition | Schemas['FilterGroup-Output']
type RelativeDateValue = Schemas['RelativeDateValue']
type SearchStep = Schemas['SearchStep-Output']

const FIELD_LABELS: Record<string, string> = {
  transaction_date: 'date',
  chargeback_opened_at: 'chargeback opened',
  opened_at: 'opened',
  refunded_at: 'refunded',
  captured_at: 'captured',
  chargeback_event_date: 'chargeback event',
  customer_id: 'customer',
  payment_id: 'payment',
  transaction_id: 'transaction',
  chargeback_id: 'chargeback',
}

const OPERATOR_SYMBOLS: Record<string, string> = {
  eq: '=',
  neq: '≠',
  gt: '>',
  gte: '≥',
  lt: '<',
  lte: '≤',
  in: 'is one of',
  not_in: 'is not one of',
  contains: 'contains',
}

const RELATIVE_DATE_LABELS: Record<string, string> = {
  today: 'today',
  yesterday: 'yesterday',
  this_month: 'this month',
  last_month: 'last month',
  this_year: 'this year',
}

export function fieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? field.replaceAll('_', ' ')
}

function isRelativeDate(value: unknown): value is RelativeDateValue {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function describeValue(value: FilterCondition['value']): string {
  if (Array.isArray(value)) return value.join(', ')
  if (isRelativeDate(value)) {
    if (value.last_n_days) return `in the last ${value.last_n_days} days`
    return value.relative ? (RELATIVE_DATE_LABELS[value.relative] ?? value.relative) : ''
  }
  return String(value)
}

function describeCondition(condition: FilterCondition): string {
  const field = condition.field
  const label = fieldLabel(field)
  const value = condition.value
  switch (condition.operator) {
    case 'exists': {
      const subject = field.replace(/^has_/, '').replaceAll('_', ' ')
      return value === false ? `no ${subject}` : `has ${subject}`
    }
    case 'is_null':
      return value === false ? `${label} is set` : `${label} is empty`
    case 'between':
      if (isRelativeDate(value)) return `${label} ${describeValue(value)}`
      if (Array.isArray(value) && value.length === 2) return `${label} ${value[0]} – ${value[1]}`
      return `${label} between ${describeValue(value)}`
    default:
      return `${label} ${OPERATOR_SYMBOLS[condition.operator] ?? condition.operator} ${describeValue(value)}`
  }
}

export function describeFilter(node: FilterNode): string {
  if (node.kind === 'condition') return describeCondition(node)
  const parts = node.children.map((child) =>
    child.kind === 'group' && child.children.length > 1 ? `(${describeFilter(child)})` : describeFilter(child),
  )
  const joined = parts.join(node.logic === 'or' ? ' or ' : ' and ')
  return node.negate ? `not (${joined})` : joined
}

// One chip per top-level AND condition; anything else is a single chip.
export function filterChips(plan: QueryPlan): string[] {
  const search = plan.steps.findLast((step): step is SearchStep => step.tool_name !== 'get_transaction_detail')
  const filter = search?.filter
  if (!filter) return []
  if (filter.kind === 'group' && filter.logic === 'and' && !filter.negate) {
    return filter.children.map((child) => describeFilter(child))
  }
  return [describeFilter(filter)]
}

export function describeSort(sort: SortSpec | null): string {
  if (!sort) return 'newest first'
  return `${fieldLabel(sort.field)} ${sort.direction === 'asc' ? '↑' : '↓'}`
}

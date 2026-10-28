// The columns shown for each entity. `sortField` names the backend field a header sorts by
// (only fields the backend marks sortable).
import { createColumnHelper, tableFeatures } from '@tanstack/react-table'

import type { ChargebackSummary, SearchableEntity, TransactionSummary } from '../../api/agentApiTypes'
import { formatCode, formatDateTime, formatMoney } from './formatters'
import { StatusBadge, YesNoBadge } from './StatusBadge'

export const resultTableFeatures = tableFeatures({})

const transactionColumn = createColumnHelper<typeof resultTableFeatures, TransactionSummary>()
const chargebackColumn = createColumnHelper<typeof resultTableFeatures, ChargebackSummary>()

export const transactionColumns = transactionColumn.columns([
  transactionColumn.accessor('transaction_date', {
    header: 'Date',
    cell: (info) => formatDateTime(info.getValue()),
  }),
  transactionColumn.accessor('customer_id', { header: 'Customer' }),
  transactionColumn.accessor('amount', {
    header: 'Amount',
    cell: (info) => formatMoney(info.getValue(), info.row.original.currency),
  }),
  transactionColumn.accessor('merchant_name', { header: 'Merchant', cell: (info) => info.getValue() ?? '—' }),
  transactionColumn.accessor('payment_status', { header: 'Payment', cell: (info) => <StatusBadge status={info.getValue()} /> }),
  transactionColumn.accessor('payment_method', { header: 'Method', cell: (info) => formatCode(info.getValue()) }),
  transactionColumn.accessor('card_brand', { header: 'Card', cell: (info) => formatCode(info.getValue()) }),
  transactionColumn.accessor('has_refund', { header: 'Refund', cell: (info) => <YesNoBadge value={info.getValue()} /> }),
  transactionColumn.accessor('chargeback_status', {
    header: 'Chargeback',
    cell: (info) => <StatusBadge status={info.getValue()} />,
  }),
])

export const chargebackColumns = chargebackColumn.columns([
  chargebackColumn.accessor('opened_at', { header: 'Opened', cell: (info) => formatDateTime(info.getValue()) }),
  chargebackColumn.accessor('customer_id', { header: 'Customer' }),
  chargebackColumn.accessor('chargeback_amount', {
    header: 'Amount',
    cell: (info) => formatMoney(info.getValue(), info.row.original.currency),
  }),
  chargebackColumn.accessor('chargeback_status', { header: 'Status', cell: (info) => <StatusBadge status={info.getValue()} /> }),
  chargebackColumn.accessor('reason_code', { header: 'Reason' }),
  chargebackColumn.accessor('network', { header: 'Network', cell: (info) => formatCode(info.getValue()) }),
  chargebackColumn.accessor('latest_event_type', {
    header: 'Latest event',
    cell: (info) => <StatusBadge status={info.getValue()} />,
  }),
  chargebackColumn.accessor('transaction_date', {
    header: 'Transaction date',
    cell: (info) => formatDateTime(info.getValue()),
  }),
])

// Column id -> backend sort field, for the columns the backend can sort on.
export const SORT_FIELD_BY_COLUMN: Record<SearchableEntity, Record<string, string>> = {
  transactions: { transaction_date: 'transaction_date', amount: 'amount' },
  chargebacks: { opened_at: 'opened_at', chargeback_amount: 'chargeback_amount' },
}

// Clicking a row opens this transaction's details (a chargeback opens its disputed transaction).
export const DETAIL_TRANSACTION_ID_FIELD = 'transaction_id' as const

export const ROW_ID_FIELD: Record<SearchableEntity, 'transaction_id' | 'chargeback_id'> = {
  transactions: 'transaction_id',
  chargebacks: 'chargeback_id',
}

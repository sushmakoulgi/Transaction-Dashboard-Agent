// A popup with everything about one transaction: payment, refunds and chargeback timeline.
import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, type ReactNode } from 'react'

import type { AgentApiClient } from '../../api/agentApiClient'
import type { TransactionDetail } from '../../api/agentApiTypes'
import { describeAgentError } from '../chat/describeAgentError'
import { formatCode, formatDateTime, formatMoney } from '../results/formatters'
import { StatusBadge } from '../results/StatusBadge'
import './TransactionDetailDialog.css'

interface TransactionDetailDialogProps {
  api: AgentApiClient
  transactionId: string
  onClose: () => void
}

export function TransactionDetailDialog({ api, transactionId, onClose }: TransactionDetailDialogProps) {
  const closeButton = useRef<HTMLButtonElement>(null)
  const detail = useQuery({
    queryKey: ['transaction-detail', transactionId],
    queryFn: () => api.getTransactionDetail(transactionId),
    staleTime: 60_000,
  })

  useEffect(() => {
    closeButton.current?.focus()
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [onClose])

  return (
    <div className="dialog-backdrop" onClick={onClose} data-testid="dialog-backdrop">
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="transaction-detail-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="dialog__header">
          <div>
            <h2 id="transaction-detail-title">Transaction details</h2>
            <code className="dialog__id">{transactionId}</code>
          </div>
          <button ref={closeButton} type="button" className="dialog__close" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </header>
        <div className="dialog__body">
          {detail.isPending && <p className="muted">Loading details…</p>}
          {detail.isError && (
            <p className="dialog__error" role="alert">
              {describeAgentError(detail.error)}
            </p>
          )}
          {detail.data && <DetailContent detail={detail.data} />}
        </div>
      </div>
    </div>
  )
}

function DetailContent({ detail }: { detail: TransactionDetail }) {
  const { payment, refunds, chargeback } = detail
  const merchant = detail.merchant_details
  const card = payment.payment_details
  return (
    <>
      <section className="detail-hero">
        <span className="detail-hero__amount">{formatMoney(detail.amount, detail.currency)}</span>
        <StatusBadge status={payment.payment_status} />
        {chargeback && <StatusBadge status={chargeback.chargeback_status} />}
      </section>

      <DetailSection title="Overview">
        <div className="detail-grid">
        <Field label="Date" value={formatDateTime(detail.transaction_date)} />
        <Field label="Customer" value={detail.customer_id} />
        <Field label="Type" value={formatCode(detail.transaction_type)} />
        <Field label="Merchant" value={text(merchant.merchant_name)} />
        <Field label="Category" value={formatCode(text(merchant.merchant_category))} />
        <Field label="Country" value={text(merchant.country)} />
        <Field label="Channel" value={formatCode(text(detail.metadata.channel))} />
        </div>
      </DetailSection>

      <DetailSection title="Payment">
        <div className="detail-grid">
        <Field label="Payment ID" value={<code>{payment.payment_id}</code>} />
        <Field label="Method" value={formatCode(payment.payment_method)} />
        <Field label="Status" value={<StatusBadge status={payment.payment_status} />} />
        <Field
          label="Card"
          value={card.card_brand ? `${formatCode(text(card.card_brand))} •••• ${text(card.card_last4)}` : '—'}
        />
        <Field label="Authorized" value={amountAt(payment.authorized_amount, detail.currency, payment.authorized_at)} />
        <Field label="Captured" value={amountAt(payment.captured_amount, detail.currency, payment.captured_at)} />
        </div>
      </DetailSection>

      <DetailSection title={`Refunds (${refunds.length})`}>
        {refunds.length === 0 ? (
          <p className="muted">No refunds.</p>
        ) : (
          <table className="detail-table">
            <thead>
              <tr>
                <th scope="col">Date</th>
                <th scope="col">Amount</th>
                <th scope="col">Status</th>
                <th scope="col">Reason</th>
              </tr>
            </thead>
            <tbody>
              {refunds.map((refund) => (
                <tr key={refund.refund_id}>
                  <td>{formatDateTime(refund.refunded_at)}</td>
                  <td>{formatMoney(refund.refund_amount, detail.currency)}</td>
                  <td>
                    <StatusBadge status={refund.refund_status} />
                  </td>
                  <td>{formatCode(text(refund.refund_details.reason))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </DetailSection>

      <DetailSection title="Chargeback">
        {!chargeback ? (
          <p className="muted">No chargeback.</p>
        ) : (
          <>
            <div className="detail-grid">
              <Field label="Status" value={<StatusBadge status={chargeback.chargeback_status} />} />
              <Field label="Reason" value={`${chargeback.reason_code} · ${text(chargeback.dispute_details.reason_description)}`} />
              <Field label="Amount" value={formatMoney(chargeback.chargeback_amount, detail.currency)} />
              <Field label="Opened" value={formatDateTime(chargeback.opened_at)} />
              <Field label="Network" value={formatCode(text(chargeback.dispute_details.network))} />
              <Field label="Case" value={text(chargeback.dispute_details.network_case_id)} />
            </div>
            <ol className="timeline" aria-label="Chargeback timeline">
              {chargeback.events.map((event) => (
                <li key={event.chargeback_event_id} className="timeline__event">
                  <span className="timeline__dot" aria-hidden="true" />
                  <div>
                    <StatusBadge status={event.event_type} />{' '}
                    <span className="muted">{formatDateTime(event.event_date)}</span>
                    <p className="timeline__note">
                      {text(event.event_payload.note)} ({formatCode(text(event.event_payload.actor))})
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          </>
        )}
      </DetailSection>
    </>
  )
}

function DetailSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="detail-section">
      <h3>{title}</h3>
      {children}
    </section>
  )
}

function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="detail-field">
      <span className="detail-field__label">{label}</span>
      <span className="detail-field__value">{value}</span>
    </div>
  )
}

function text(value: unknown): string {
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

function amountAt(amount: string | null, currency: string, at: string | null): string {
  return amount ? `${formatMoney(amount, currency)} · ${formatDateTime(at)}` : '—'
}

import { formatCode } from './formatters'

type Tone = 'success' | 'warning' | 'danger' | 'neutral' | 'info'

const TONE_BY_STATUS: Record<string, Tone> = {
  CAPTURED: 'success',
  COMPLETED: 'success',
  WON: 'success',
  AUTHORIZED: 'warning',
  PENDING: 'warning',
  OPEN: 'warning',
  OPENED: 'warning',
  UNDER_REVIEW: 'warning',
  EVIDENCE_REQUESTED: 'warning',
  EVIDENCE_SUBMITTED: 'info',
  REPRESENTMENT: 'info',
  PRE_ARBITRATION: 'danger',
  ARBITRATION: 'danger',
  FAILED: 'danger',
  LOST: 'danger',
  VOIDED: 'neutral',
  ACCEPTED: 'neutral',
}

export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <span className="muted">—</span>
  const tone = TONE_BY_STATUS[status] ?? 'neutral'
  return <span className={`badge badge--${tone}`}>{formatCode(status)}</span>
}

export function YesNoBadge({ value }: { value: boolean }) {
  return value ? <span className="badge badge--info">Yes</span> : <span className="muted">No</span>
}

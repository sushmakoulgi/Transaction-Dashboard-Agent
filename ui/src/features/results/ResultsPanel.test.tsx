// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AgentApiError, type AgentApiClient } from '../../api/agentApiClient'
import type {
  ChargebackSummary,
  ChatResponse,
  PlanPageResponse,
  QueryPlan,
  TransactionSummary,
} from '../../api/agentApiTypes'
import { ResultsPanel } from './ResultsPanel'

const PLAN: QueryPlan = {
  steps: [
    {
      tool_name: 'search_transactions',
      filter: { kind: 'condition', field: 'has_chargeback', operator: 'exists', value: true },
      sort: null,
    },
  ],
}

function transaction(number: number): TransactionSummary {
  return {
    transaction_id: String(400000000000000 + number),
    customer_id: String(10001640 + number),
    amount: '1234.50',
    currency: 'USD',
    transaction_type: 'PURCHASE',
    transaction_date: '2026-10-15T13:30:00Z',
    merchant_name: 'Rogers',
    merchant_category: 'WIRELESS',
    payment_id: `PAY${String(number).padStart(9, '0')}`,
    payment_status: 'CAPTURED',
    payment_method: 'CARD',
    card_brand: 'VISA',
    has_refund: number % 2 === 0,
    has_chargeback: true,
    chargeback_status: 'UNDER_REVIEW',
  }
}

function transactionPage(page: number, pageSize: number, totalCount: number): PlanPageResponse {
  const first = (page - 1) * pageSize
  const count = Math.max(0, Math.min(pageSize, totalCount - first))
  return {
    result_entity: 'transactions',
    result_page: {
      items: Array.from({ length: count }, (_, index) => transaction(first + index + 1)),
      page,
      page_size: pageSize,
      total_count: totalCount,
      total_pages: Math.ceil(totalCount / pageSize),
    },
  }
}

function chatAnswer(page: PlanPageResponse | null, plan: QueryPlan | null = PLAN): ChatResponse {
  return {
    session_id: 's1',
    reply: 'ok',
    query_plan: plan,
    result_entity: page?.result_entity ?? null,
    result_page: page?.result_page ?? null,
    reached_round_limit: false,
  }
}

function fakeApi(executePlan: AgentApiClient['executePlan']): AgentApiClient {
  return {
    checkHealth: vi.fn(),
    chat: vi.fn(),
    executePlan: vi.fn(executePlan),
    getTransactionDetail: vi.fn(),
    getSessionEntries: vi.fn(),
    deleteSession: vi.fn(),
  }
}

const servesTransactionPages = (total: number) =>
  fakeApi(async (_plan, page, pageSize) => transactionPage(page, pageSize, total))

function renderPanel(api: AgentApiClient, answer: ChatResponse | null) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <ResultsPanel api={api} answer={answer} />
    </QueryClientProvider>,
  )
}

const bodyRows = () => within(screen.getByRole('table')).getAllByRole('row').slice(1)
const cellsOf = (row: HTMLElement) => within(row).getAllByRole('cell').map((cell) => cell.textContent)

afterEach(cleanup)

describe('results panel', () => {
  it("shows the answer's own first page without another request", () => {
    const api = servesTransactionPages(40)
    renderPanel(api, chatAnswer(transactionPage(1, 25, 40)))

    expect(screen.getAllByRole('columnheader').map((header) => header.textContent?.replace(/[▲▼↕]/g, '').trim())).toEqual([
      'Date', 'Customer', 'Amount', 'Merchant', 'Payment', 'Method', 'Card', 'Refund', 'Chargeback',
    ])
    expect(bodyRows()).toHaveLength(25)
    expect(cellsOf(bodyRows()[0])).toEqual([
      'Oct 15, 2026, 09:30 AM', // 13:30 UTC shown in New York time
      '10001641',
      '$1,234.50',
      'Rogers',
      'CAPTURED',
      'CARD',
      'VISA',
      'No',
      'UNDER REVIEW',
    ])
    expect(screen.getByText('Page 1 of 2 · 40 matches')).toBeTruthy()
    expect(api.executePlan).not.toHaveBeenCalled()
  })

  it('pages through the plan without calling Claude', async () => {
    const api = servesTransactionPages(40)
    renderPanel(api, chatAnswer(transactionPage(1, 25, 40)))
    expect(screen.getByRole('button', { name: '‹ Previous' })).toHaveProperty('disabled', true)

    await userEvent.click(screen.getByRole('button', { name: 'Next ›' }))

    await waitFor(() => expect(screen.getByText('Page 2 of 2 · 40 matches')).toBeTruthy())
    expect(api.executePlan).toHaveBeenCalledWith(PLAN, 2, 25)
    await waitFor(() => expect(bodyRows()).toHaveLength(15))
    expect(cellsOf(bodyRows()[0])[1]).toBe('10001666')
    expect(screen.getByRole('button', { name: 'Next ›' })).toHaveProperty('disabled', true)
    expect(api.chat).not.toHaveBeenCalled()
  })

  it('changes the page size and starts again at page 1', async () => {
    const api = servesTransactionPages(40)
    renderPanel(api, chatAnswer(transactionPage(1, 25, 40)))

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Rows per page' }), '50')

    await waitFor(() => expect(api.executePlan).toHaveBeenCalledWith(PLAN, 1, 50))
    await waitFor(() => expect(bodyRows()).toHaveLength(40))
    expect(screen.getByText('Page 1 of 1 · 40 matches')).toBeTruthy()
  })

  it('sorts on the server by sortable columns only', async () => {
    const api = servesTransactionPages(40)
    renderPanel(api, chatAnswer(transactionPage(1, 25, 40)))
    expect(screen.queryByRole('button', { name: /Customer/ })).toBeNull() // not sortable

    await userEvent.click(screen.getByRole('button', { name: /Amount/ }))
    const sortedPlan = (direction: 'asc' | 'desc') => ({
      steps: [{ ...PLAN.steps[0], sort: { field: 'amount', direction } }],
    })
    await waitFor(() => expect(api.executePlan).toHaveBeenLastCalledWith(sortedPlan('desc'), 1, 25))
    await waitFor(() =>
      expect(screen.getByRole('columnheader', { name: /Amount/ }).getAttribute('aria-sort')).toBe('descending'),
    )

    await userEvent.click(screen.getByRole('button', { name: /Amount/ }))
    await waitFor(() => expect(api.executePlan).toHaveBeenLastCalledWith(sortedPlan('asc'), 1, 25))
  })

  it('shows chargeback columns for chargeback results', () => {
    const chargeback: ChargebackSummary = {
      chargeback_id: '20000000-0000-4000-8000-000000000001',
      transaction_id: '400000000000001',
      customer_id: '10033317',
      reason_code: '10.4',
      chargeback_status: 'OPEN',
      chargeback_amount: '99.00',
      currency: 'EUR',
      opened_at: '2026-10-20T16:00:00Z',
      network: 'VISA',
      latest_event_type: 'EVIDENCE_REQUESTED',
      transaction_date: '2026-10-01T16:00:00Z',
    }
    const page: PlanPageResponse = {
      result_entity: 'chargebacks',
      result_page: { items: [chargeback], page: 1, page_size: 25, total_count: 1, total_pages: 1 },
    }
    renderPanel(servesTransactionPages(0), chatAnswer(page))

    expect(cellsOf(bodyRows()[0])).toEqual([
      'Oct 20, 2026, 12:00 PM',
      '10033317',
      '€99.00',
      'OPEN',
      '10.4',
      'VISA',
      'EVIDENCE REQUESTED',
      'Oct 01, 2026, 12:00 PM',
    ])
    expect(screen.getByText('Page 1 of 1 · 1 match')).toBeTruthy()
  })

  it('explains a failed page and can try again', async () => {
    let attempts = 0
    const api = fakeApi(async (_plan, page, pageSize) => {
      attempts += 1
      if (attempts === 1) throw new AgentApiError(503, 'TRANSACTION_SERVICE_UNAVAILABLE', 'down')
      return transactionPage(page, pageSize, 40)
    })
    renderPanel(api, chatAnswer(transactionPage(1, 25, 40)))

    await userEvent.click(screen.getByRole('button', { name: 'Next ›' }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/transaction data is unavailable/)

    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(screen.getByText('Page 2 of 2 · 40 matches')).toBeTruthy())
  })

  it('handles answers without results', () => {
    const { rerender } = renderPanel(servesTransactionPages(0), null)
    expect(screen.getByText('Results from your questions will appear here.')).toBeTruthy()

    const queryClient = new QueryClient()
    rerender(
      <QueryClientProvider client={queryClient}>
        <ResultsPanel api={servesTransactionPages(0)} answer={chatAnswer(null, null)} />
      </QueryClientProvider>,
    )
    expect(screen.getByText('This answer has no table of results.')).toBeTruthy()

    rerender(
      <QueryClientProvider client={queryClient}>
        <ResultsPanel api={servesTransactionPages(0)} answer={chatAnswer(transactionPage(1, 25, 0))} />
      </QueryClientProvider>,
    )
    expect(screen.getByText('No transactions matched.')).toBeTruthy()
  })
})

describe('summary bar and detail popup', () => {
  const detail = {
    transaction_id: '400000000000001',
    customer_id: '10001641',
    amount: '1234.50',
    currency: 'USD',
    transaction_type: 'PURCHASE',
    transaction_date: '2026-10-15T13:30:00Z',
    merchant_details: { merchant_name: 'Rogers', merchant_category: 'WIRELESS', country: 'US' },
    metadata: { channel: 'WEB' },
    payment: {
      payment_id: 'PAY000000001',
      payment_method: 'CARD',
      payment_status: 'CAPTURED',
      authorized_amount: '1234.50',
      captured_amount: '1234.50',
      authorized_at: '2026-10-15T13:30:05Z',
      captured_at: '2026-10-15T14:00:00Z',
      payment_details: { card_brand: 'VISA', card_last4: '4242' },
    },
    refunds: [
      {
        refund_id: 'r1',
        refund_amount: '100.00',
        refund_status: 'COMPLETED',
        refunded_at: '2026-10-16T13:00:00Z',
        refund_details: { reason: 'ITEM_RETURNED' },
      },
    ],
    chargeback: {
      chargeback_id: 'c1',
      reason_code: '13.1',
      chargeback_amount: '1234.50',
      chargeback_status: 'WON',
      opened_at: '2026-10-20T16:00:00Z',
      dispute_details: { network: 'VISA', network_case_id: 'CASE-1', reason_description: 'Not received' },
      events: [
        { chargeback_event_id: 'e1', event_type: 'OPENED', event_sequence: 1, event_date: '2026-10-20T16:00:00Z', event_payload: { actor: 'ISSUER', note: 'Opened recorded' } },
        { chargeback_event_id: 'e2', event_type: 'WON', event_sequence: 2, event_date: '2026-10-25T16:00:00Z', event_payload: { actor: 'NETWORK', note: 'Won recorded' } },
      ],
    },
  }

  function apiWithDetail() {
    const api = servesTransactionPages(40)
    api.getTransactionDetail = vi.fn(async () => detail) as AgentApiClient['getTransactionDetail']
    return api
  }

  it('shows the summary below the grid: entity, count, reply and filter chips', () => {
    renderPanel(servesTransactionPages(40), { ...chatAnswer(transactionPage(1, 25, 40)), reply: '40 transactions have chargebacks.' })
    const summary = screen.getByLabelText('Results summary')
    expect(within(summary).getByText('Transactions')).toBeTruthy()
    expect(within(summary).getByTestId('results-count').textContent).toBe('40')
    expect(within(summary).getByText('40 transactions have chargebacks.')).toBeTruthy()
    expect(within(summary).getByText('has chargeback')).toBeTruthy()
    expect(within(summary).getByText(/sorted by newest first/)).toBeTruthy()
    // the summary comes after the grid and its pager
    expect(summary.compareDocumentPosition(screen.getByRole('table')) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy()
    expect(
      summary.compareDocumentPosition(screen.getByRole('button', { name: 'Next ›' })) & Node.DOCUMENT_POSITION_PRECEDING,
    ).toBeTruthy()
  })

  it('colours statuses with badges', () => {
    renderPanel(servesTransactionPages(40), chatAnswer(transactionPage(1, 25, 40)))
    const firstRow = bodyRows()[0]
    expect(within(firstRow).getByText('CAPTURED').className).toBe('badge badge--success')
    expect(within(firstRow).getByText('UNDER REVIEW').className).toBe('badge badge--warning')
  })

  it('opens the clicked transaction in a popup and closes it with Escape', async () => {
    const api = apiWithDetail()
    renderPanel(api, chatAnswer(transactionPage(1, 25, 40)))

    await userEvent.click(bodyRows()[0])

    const dialog = await screen.findByRole('dialog', { name: 'Transaction details' })
    expect(api.getTransactionDetail).toHaveBeenCalledWith('400000000000001')
    await within(dialog).findByText('Refunds (1)') // details loaded
    expect(dialog.querySelector('.detail-hero__amount')?.textContent).toBe('$1,234.50')
    expect(within(dialog).getByText('VISA •••• 4242')).toBeTruthy()
    expect(within(dialog).getByText('Refunds (1)')).toBeTruthy()
    expect(within(dialog).getByText('13.1 · Not received')).toBeTruthy()
    const timeline = within(dialog).getByRole('list', { name: 'Chargeback timeline' })
    expect(within(timeline).getAllByRole('listitem').map((item) => item.querySelector('.badge')?.textContent)).toEqual([
      'OPENED',
      'WON',
    ])
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Close' }))

    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('opens from the keyboard and closes with the button or the backdrop', async () => {
    const api = apiWithDetail()
    renderPanel(api, chatAnswer(transactionPage(1, 25, 40)))

    bodyRows()[1].focus()
    await userEvent.keyboard('{Enter}')
    expect(api.getTransactionDetail).toHaveBeenCalledWith('400000000000002')
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()

    await userEvent.click(bodyRows()[0])
    await screen.findByRole('dialog')
    await userEvent.click(screen.getByTestId('dialog-backdrop'))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('shows a friendly error when the details cannot load', async () => {
    const api = servesTransactionPages(40)
    api.getTransactionDetail = vi.fn(async () => {
      throw new AgentApiError(404, 'TRANSACTION_NOT_FOUND', 'transaction not found')
    }) as AgentApiClient['getTransactionDetail']
    renderPanel(api, chatAnswer(transactionPage(1, 25, 40)))

    await userEvent.click(bodyRows()[0])
    expect((await within(await screen.findByRole('dialog')).findByRole('alert')).textContent).toBe('transaction not found')
  })
})

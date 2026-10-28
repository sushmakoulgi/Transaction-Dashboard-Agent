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
    transaction_id: `00000000-0000-4000-8000-${String(number).padStart(12, '0')}`,
    customer_id: `C-10${number}`,
    amount: '1234.50',
    currency: 'USD',
    transaction_type: 'PURCHASE',
    transaction_date: '2026-10-15T13:30:00Z',
    merchant_name: 'Blue Harbor Travel',
    merchant_category: 'TRAVEL',
    payment_id: `10000000-0000-4000-8000-${String(number).padStart(12, '0')}`,
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
      'C-101',
      '$1,234.50',
      'Blue Harbor Travel',
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
    expect(cellsOf(bodyRows()[0])[1]).toBe('C-1026')
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
      transaction_id: '00000000-0000-4000-8000-000000000001',
      customer_id: 'C-1015',
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
      'C-1015',
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

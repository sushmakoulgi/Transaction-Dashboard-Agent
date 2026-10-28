// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../../App'
import { AgentApiError, type AgentApiClient } from '../../api/agentApiClient'
import type { ChatResponse, FilterValidationIssue } from '../../api/agentApiTypes'
import { EXAMPLE_QUESTIONS } from './exampleQuestions'

function answer(reply: string, overrides: Partial<ChatResponse> = {}): ChatResponse {
  return {
    session_id: 'session-1',
    reply,
    query_plan: null,
    result_entity: null,
    result_page: null,
    reached_round_limit: false,
    ...overrides,
  }
}

function fakeApi(overrides: Partial<AgentApiClient> = {}): AgentApiClient {
  return {
    checkHealth: vi.fn(async () => true),
    chat: vi.fn(async () => answer('Here you go.')),
    executePlan: vi.fn(),
    getTransactionDetail: vi.fn(),
    getSessionEntries: vi.fn(async () => []),
    deleteSession: vi.fn(async () => undefined),
    ...overrides,
  } as AgentApiClient
}

function renderApp(api: AgentApiClient) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <App api={api} />
    </QueryClientProvider>,
  )
}

const questionBox = () => screen.getByRole('textbox', { name: 'Your question' })
const messages = () => [...document.querySelectorAll('.message:not(.message--pending)')].map((node) => [
  node.getAttribute('data-role'),
  node.querySelector('p')?.textContent,
])

beforeEach(() => sessionStorage.clear())
afterEach(cleanup)

describe('chat panel', () => {
  it('offers example questions and answers one when clicked', async () => {
    const api = fakeApi({
      chat: vi.fn(async () =>
        answer('10 transactions have chargebacks opened this month.', {
          query_plan: { steps: [{ tool_name: 'search_transactions', filter: null, sort: null }] },
          result_entity: 'transactions',
          result_page: { items: [], page: 1, page_size: 25, total_count: 10, total_pages: 1 },
        }),
      ),
    })
    renderApp(api)

    await userEvent.click(screen.getByRole('button', { name: EXAMPLE_QUESTIONS[1] }))

    await waitFor(() =>
      expect(messages()).toEqual([
        ['user', EXAMPLE_QUESTIONS[1]],
        ['assistant', '10 transactions have chargebacks opened this month.'],
      ]),
    )
    expect(api.chat).toHaveBeenCalledWith(EXAMPLE_QUESTIONS[1], undefined)
    expect(within(screen.getByRole('region', { name: 'Results' })).getByText(/Page 1 of 1 · 10 matches/)).toBeTruthy()
    expect(api.executePlan).not.toHaveBeenCalled() // the answer's own page is shown as-is
  })

  it('sends on Enter, keeps Shift+Enter for new lines, and continues the session', async () => {
    const api = fakeApi()
    renderApp(api)

    await userEvent.type(questionBox(), 'Transactions for C-1015{Shift>}{Enter}{/Shift}this month')
    expect(api.chat).not.toHaveBeenCalled()
    await userEvent.type(questionBox(), '{Enter}')
    await waitFor(() => expect(api.chat).toHaveBeenCalledWith('Transactions for C-1015\nthis month', undefined))

    await userEvent.type(questionBox(), 'Now their chargebacks{Enter}')
    await waitFor(() => expect(api.chat).toHaveBeenLastCalledWith('Now their chargebacks', 'session-1'))
    expect((questionBox() as HTMLTextAreaElement).value).toBe('')
  })

  it('shows a waiting state and blocks sending until the answer arrives', async () => {
    let finish: (value: ChatResponse) => void = () => undefined
    const api = fakeApi({ chat: vi.fn(() => new Promise<ChatResponse>((resolve) => (finish = resolve))) })
    renderApp(api)

    await userEvent.type(questionBox(), 'Slow question{Enter}')
    expect(await screen.findByRole('status')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Waiting…' })).toHaveProperty('disabled', true)

    finish(answer('Done.'))
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull())
    expect(messages().at(-1)).toEqual(['assistant', 'Done.'])
  })

  it('explains errors and lists validation issues with suggestions', async () => {
    const issue: FilterValidationIssue = {
      path: 'filter.field',
      code: 'UNKNOWN_FIELD',
      message: "unknown field 'cust_id'",
      suggestions: ['customer_id'],
    }
    const api = fakeApi({
      chat: vi
        .fn()
        .mockRejectedValueOnce(
          new AgentApiError(502, 'CLAUDE_CONFIGURATION_ERROR', 'no usable Claude credentials: set ANTHROPIC_API_KEY'),
        )
        .mockRejectedValueOnce(new AgentApiError(422, 'FILTER_VALIDATION_FAILED', '1 problem', [issue]))
        .mockRejectedValueOnce(new AgentApiError(0, 'NETWORK_ERROR', 'failed')),
    })
    renderApp(api)

    for (const question of ['first', 'second', 'third']) {
      await userEvent.type(questionBox(), `${question}{Enter}`)
      await waitFor(() => expect(screen.queryByRole('status')).toBeNull())
    }

    const errors = messages().filter(([role]) => role === 'error').map(([, text]) => text)
    expect(errors).toEqual([
      "The agent can't use Claude: no usable Claude credentials: set ANTHROPIC_API_KEY",
      '1 problem',
      "Can't reach the agent service. Is it running?",
    ])
    expect(screen.getByText(/did you mean: customer_id/)).toBeTruthy()
  })

  it('keeps the conversation across a reload of the page', async () => {
    const api = fakeApi()
    const firstRender = renderApp(api)
    await userEvent.type(questionBox(), 'Transactions for C-1015{Enter}')
    await waitFor(() => expect(messages()).toHaveLength(2))
    firstRender.unmount()

    renderApp(api)
    expect(messages()).toEqual([
      ['user', 'Transactions for C-1015'],
      ['assistant', 'Here you go.'],
    ])
    await userEvent.type(questionBox(), 'And their refunds?{Enter}')
    await waitFor(() => expect(api.chat).toHaveBeenLastCalledWith('And their refunds?', 'session-1'))
  })

  it('starts a new conversation and asks the server to forget the old one', async () => {
    const api = fakeApi()
    renderApp(api)
    await userEvent.type(questionBox(), 'hello{Enter}')
    await waitFor(() => expect(messages()).toHaveLength(2))

    await userEvent.click(screen.getByRole('button', { name: 'New conversation' }))

    expect(api.deleteSession).toHaveBeenCalledWith('session-1')
    expect(messages()).toEqual([])
    expect(screen.getByRole('button', { name: EXAMPLE_QUESTIONS[0] })).toBeTruthy()
    await userEvent.type(questionBox(), 'fresh start{Enter}')
    await waitFor(() => expect(api.chat).toHaveBeenLastCalledWith('fresh start', undefined))
  })

  it('shows whether the agent service is reachable', async () => {
    renderApp(fakeApi({ checkHealth: vi.fn(async () => false) }))
    expect(await screen.findByText(/Agent service unreachable/)).toBeTruthy()
  })
})

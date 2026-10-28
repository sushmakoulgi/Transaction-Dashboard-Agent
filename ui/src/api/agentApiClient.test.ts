import { describe, expect, it } from 'vitest'

import { AgentApiError, createAgentApiClient } from './agentApiClient'
import type { QueryPlan } from './agentApiTypes'

interface RecordedRequest {
  url: string
  method: string
  headers: Record<string, string>
  body: unknown
}

function fakeFetch(...responses: Array<Response | Error>) {
  const requests: RecordedRequest[] = []
  const fetchFunction = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    requests.push({
      url: String(input),
      method: init?.method ?? 'GET',
      headers: (init?.headers ?? {}) as Record<string, string>,
      body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
    })
    const next = responses.length > 1 ? responses.shift()! : responses[0]
    if (next instanceof Error) throw next
    return next.clone()
  }
  return { fetchFunction: fetchFunction as typeof fetch, requests }
}

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const PLAN: QueryPlan = { steps: [{ tool_name: 'search_transactions', filter: null, sort: null }] }

describe('createAgentApiClient', () => {
  it('sends a chat question with the token and returns the response', async () => {
    const { fetchFunction, requests } = fakeFetch(jsonResponse(200, { session_id: 's1', reply: 'Hi' }))
    const client = createAgentApiClient({ baseUrl: 'http://agent:8000/', apiToken: 'secret', fetchFunction })

    const response = await client.chat('Chargebacks this month?')

    expect(response.reply).toBe('Hi')
    expect(requests[0]).toMatchObject({
      url: 'http://agent:8000/api/chat',
      method: 'POST',
      body: { message: 'Chargebacks this month?' },
    })
    expect(requests[0].headers.Authorization).toBe('Bearer secret')
    expect(requests[0].headers['Content-Type']).toBe('application/json')
  })

  it('continues a session and sends no token when none is configured', async () => {
    const { fetchFunction, requests } = fakeFetch(jsonResponse(200, { session_id: 's1' }))
    await createAgentApiClient({ baseUrl: 'http://agent', fetchFunction }).chat('And refunds?', 's1')
    expect(requests[0].body).toEqual({ message: 'And refunds?', session_id: 's1' })
    expect(requests[0].headers.Authorization).toBeUndefined()
  })

  it('pages through a plan and fetches details and sessions on the right paths', async () => {
    const { fetchFunction, requests } = fakeFetch(
      jsonResponse(200, { result_entity: 'transactions', result_page: {} }),
      jsonResponse(200, { transaction_id: 't/1' }),
      jsonResponse(200, []),
      new Response(null, { status: 204 }),
    )
    const client = createAgentApiClient({ baseUrl: 'http://agent', fetchFunction })

    await client.executePlan(PLAN, 2, 10)
    await client.getTransactionDetail('t/1')
    await client.getSessionEntries('s1')
    await expect(client.deleteSession('s1')).resolves.toBeUndefined()

    expect(requests.map((request) => `${request.method} ${request.url}`)).toEqual([
      'POST http://agent/api/plans/execute',
      'GET http://agent/api/transactions/t%2F1',
      'GET http://agent/api/sessions/s1/entries',
      'DELETE http://agent/api/sessions/s1',
    ])
    expect(requests[0].body).toEqual({ query_plan: PLAN, page: 2, page_size: 10 })
  })

  it('raises the service error with its code and issues', async () => {
    const issue = { path: 'filter.field', code: 'UNKNOWN_FIELD', message: 'unknown', suggestions: ['customer_id'] }
    const { fetchFunction } = fakeFetch(
      jsonResponse(422, { error: 'FILTER_VALIDATION_FAILED', message: '1 problem', issues: [issue] }),
    )
    const error = await createAgentApiClient({ baseUrl: 'http://agent', fetchFunction })
      .chat('bad')
      .catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(AgentApiError)
    expect(error).toMatchObject({ status: 422, errorCode: 'FILTER_VALIDATION_FAILED', message: '1 problem' })
    expect((error as AgentApiError).issues[0].suggestions).toEqual(['customer_id'])
  })

  it('raises a generic error for non-JSON failures and network problems', async () => {
    const { fetchFunction } = fakeFetch(new Response('Bad Gateway', { status: 502 }), new TypeError('Failed to fetch'))
    const client = createAgentApiClient({ baseUrl: 'http://agent', fetchFunction })

    await expect(client.chat('x')).rejects.toMatchObject({ status: 502, errorCode: 'HTTP_ERROR' })
    await expect(client.chat('x')).rejects.toMatchObject({ status: 0, errorCode: 'NETWORK_ERROR' })
  })

  it('reports health as true or false without throwing', async () => {
    const healthy = fakeFetch(jsonResponse(200, { status: 'ok' }))
    const down = fakeFetch(new TypeError('Failed to fetch'))
    expect(await createAgentApiClient({ baseUrl: 'http://agent', ...healthy }).checkHealth()).toBe(true)
    expect(await createAgentApiClient({ baseUrl: 'http://agent', ...down }).checkHealth()).toBe(false)
  })
})

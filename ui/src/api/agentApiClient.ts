// The UI's only way to reach the backend: the agent service API.
import type {
  ChatResponse,
  ErrorResponse,
  ExecutePlanRequest,
  FilterValidationIssue,
  PlanPageResponse,
  QueryPlan,
  SessionEntry,
  TransactionDetail,
} from './agentApiTypes'

export interface AgentApiConfig {
  baseUrl: string
  apiToken?: string // sent as `Authorization: Bearer ...`; omit when AUTH_ENABLED=false
  fetchFunction?: typeof fetch // tests pass a fake
}

// Every failed call raises this, carrying the service's ErrorResponse when there is one.
export class AgentApiError extends Error {
  readonly status: number
  readonly errorCode: string
  readonly issues: FilterValidationIssue[]

  constructor(status: number, errorCode: string, message: string, issues: FilterValidationIssue[] = []) {
    super(message)
    this.name = 'AgentApiError'
    this.status = status
    this.errorCode = errorCode
    this.issues = issues
  }
}

export interface AgentApiClient {
  checkHealth(): Promise<boolean>
  chat(message: string, sessionId?: string): Promise<ChatResponse>
  executePlan(queryPlan: QueryPlan, page: number, pageSize: number): Promise<PlanPageResponse>
  getTransactionDetail(transactionId: string): Promise<TransactionDetail>
  getSessionEntries(sessionId: string): Promise<SessionEntry[]>
  deleteSession(sessionId: string): Promise<void>
}

export function createAgentApiClient(config: AgentApiConfig): AgentApiClient {
  const baseUrl = config.baseUrl.replace(/\/+$/, '')
  const fetchFunction = config.fetchFunction ?? globalThis.fetch.bind(globalThis)

  async function send<ResponseT>(method: string, path: string, body?: unknown): Promise<ResponseT> {
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    if (config.apiToken) headers.Authorization = `Bearer ${config.apiToken}`

    let response: Response
    try {
      response = await fetchFunction(`${baseUrl}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      })
    } catch (networkError) {
      throw new AgentApiError(0, 'NETWORK_ERROR', `could not reach the agent service (${String(networkError)})`)
    }
    if (!response.ok) throw await toAgentApiError(response)
    if (response.status === 204) return undefined as ResponseT
    return (await response.json()) as ResponseT
  }

  return {
    async checkHealth() {
      try {
        await send<{ status: string }>('GET', '/health')
        return true
      } catch {
        return false
      }
    },
    chat(message, sessionId) {
      return send<ChatResponse>('POST', '/api/chat', sessionId ? { message, session_id: sessionId } : { message })
    },
    executePlan(queryPlan, page, pageSize) {
      const request: ExecutePlanRequest = { query_plan: queryPlan, page, page_size: pageSize }
      return send<PlanPageResponse>('POST', '/api/plans/execute', request)
    },
    getTransactionDetail(transactionId) {
      return send<TransactionDetail>('GET', `/api/transactions/${encodeURIComponent(transactionId)}`)
    },
    getSessionEntries(sessionId) {
      return send<SessionEntry[]>('GET', `/api/sessions/${encodeURIComponent(sessionId)}/entries`)
    },
    deleteSession(sessionId) {
      return send<void>('DELETE', `/api/sessions/${encodeURIComponent(sessionId)}`)
    },
  }
}

async function toAgentApiError(response: Response): Promise<AgentApiError> {
  try {
    const body = (await response.json()) as Partial<ErrorResponse>
    if (typeof body.error === 'string' && typeof body.message === 'string') {
      return new AgentApiError(response.status, body.error, body.message, body.issues ?? [])
    }
  } catch {
    // not JSON: fall through to a generic error
  }
  return new AgentApiError(response.status, 'HTTP_ERROR', `the agent service answered ${response.status}`)
}

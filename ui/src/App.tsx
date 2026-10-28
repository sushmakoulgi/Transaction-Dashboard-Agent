import { useQuery } from '@tanstack/react-query'

import { agentApi } from './agentApi'
import type { AgentApiClient } from './api/agentApiClient'
import type { ChatResponse } from './api/agentApiTypes'
import { agentApiConfig } from './config'
import { ChatPanel } from './features/chat/ChatPanel'
import { useChatSession } from './features/chat/useChatSession'

const HEALTH_CHECK_INTERVAL_MS = 15_000

interface AppProps {
  api?: AgentApiClient // tests pass a fake
}

function App({ api = agentApi }: AppProps) {
  const health = useQuery({
    queryKey: ['agent-health'],
    queryFn: () => api.checkHealth(),
    refetchInterval: HEALTH_CHECK_INTERVAL_MS,
  })
  const session = useChatSession(api)
  const status = health.isPending ? 'checking' : health.data ? 'connected' : 'unreachable'

  return (
    <main className="app">
      <header className="app-header">
        <h1>Transaction Dashboard</h1>
        <span className={`connection-status connection-status--${status}`}>
          Agent service {status === 'checking' ? '…' : status} ({agentApiConfig.baseUrl})
        </span>
      </header>
      <div className="app-layout">
        <ChatPanel session={session} />
        <ResultsSummary answer={session.latestAnswer} />
      </div>
    </main>
  )
}

// Until the results table arrives (P1d-3): what the latest answer found.
function ResultsSummary({ answer }: { answer: ChatResponse | null }) {
  return (
    <section className="results-panel" aria-label="Results">
      <h2>Results</h2>
      {answer?.result_page && answer.result_entity ? (
        <p>
          {answer.result_page.total_count} {answer.result_entity} matched (page {answer.result_page.page} of{' '}
          {Math.max(answer.result_page.total_pages, 1)}).
        </p>
      ) : (
        <p className="placeholder">Results from your questions will appear here.</p>
      )}
    </section>
  )
}

export default App

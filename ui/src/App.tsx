import { useQuery } from '@tanstack/react-query'

import { agentApi } from './agentApi'
import type { AgentApiClient } from './api/agentApiClient'
import { agentApiConfig } from './config'
import { ChatPanel } from './features/chat/ChatPanel'
import { useChatSession } from './features/chat/useChatSession'
import { ResultsPanel } from './features/results/ResultsPanel'

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
        <ResultsPanel key={session.answerSequence} api={api} answer={session.latestAnswer} />
      </div>
    </main>
  )
}

export default App

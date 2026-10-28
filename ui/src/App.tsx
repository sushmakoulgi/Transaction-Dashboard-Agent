import { useQuery } from '@tanstack/react-query'

import { agentApi } from './agentApi'
import { agentApiConfig } from './config'

const HEALTH_CHECK_INTERVAL_MS = 15_000

function App() {
  const health = useQuery({
    queryKey: ['agent-health'],
    queryFn: () => agentApi.checkHealth(),
    refetchInterval: HEALTH_CHECK_INTERVAL_MS,
  })

  const status = health.isPending ? 'checking' : health.data ? 'connected' : 'unreachable'

  return (
    <main className="app">
      <header className="app-header">
        <h1>Transaction Dashboard</h1>
        <span className={`connection-status connection-status--${status}`}>
          Agent service {status === 'checking' ? '…' : status} ({agentApiConfig.baseUrl})
        </span>
      </header>
      <p className="placeholder">The chat panel and results table arrive in the next steps.</p>
    </main>
  )
}

export default App

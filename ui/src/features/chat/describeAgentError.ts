// Turns any failure from the agent service into a message a user can act on.
import { AgentApiError } from '../../api/agentApiClient'

const FRIENDLY_MESSAGE_BY_ERROR_CODE: Record<string, string> = {
  NETWORK_ERROR: "Can't reach the agent service. Is it running?",
  UNAUTHORIZED: 'The agent service rejected the token. Check VITE_AGENT_API_TOKEN in ui/.env.local.',
  CLAUDE_UNAVAILABLE: 'Claude is busy or unreachable right now. Please try again shortly.',
  TRANSACTION_SERVICE_UNAVAILABLE: 'The transaction data is unavailable right now. Please try again shortly.',
}

export function describeAgentError(error: unknown): string {
  if (!(error instanceof AgentApiError)) {
    return `Something went wrong: ${error instanceof Error ? error.message : String(error)}`
  }
  const friendly = FRIENDLY_MESSAGE_BY_ERROR_CODE[error.errorCode]
  if (friendly) return friendly
  if (error.errorCode === 'CLAUDE_CONFIGURATION_ERROR') {
    return `The agent can't use Claude: ${error.message}`
  }
  return error.message
}

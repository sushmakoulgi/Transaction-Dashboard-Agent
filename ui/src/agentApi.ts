// The one API client instance the app uses, configured from the environment.
import { createAgentApiClient } from './api/agentApiClient'
import { agentApiConfig } from './config'

export const agentApi = createAgentApiClient(agentApiConfig)

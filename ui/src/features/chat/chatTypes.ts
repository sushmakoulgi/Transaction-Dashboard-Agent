import type { FilterValidationIssue } from '../../api/agentApiTypes'

export type ChatMessageRole = 'user' | 'assistant' | 'error'

export interface ChatMessage {
  id: string
  role: ChatMessageRole
  text: string
  issues?: FilterValidationIssue[] // for errors: the problems the backend reported
}

// The conversation: session ID, transcript and the latest answer (for the results table).
import { useMutation } from '@tanstack/react-query'
import { useCallback, useEffect, useState } from 'react'

import { AgentApiError, type AgentApiClient } from '../../api/agentApiClient'
import type { ChatResponse } from '../../api/agentApiTypes'
import { clearConversation, loadConversation, saveConversation } from './chatStorage'
import type { ChatMessage } from './chatTypes'
import { describeAgentError } from './describeAgentError'

export const MAX_QUESTION_LENGTH = 2000

export interface ChatSession {
  messages: ChatMessage[]
  latestAnswer: ChatResponse | null
  answerSequence: number // increases with every answer; a new results view per answer
  isWaitingForAnswer: boolean
  sendQuestion: (question: string) => void
  startNewConversation: () => void
}

let messageCounter = 0
function newMessageId(): string {
  messageCounter += 1
  return `${Date.now()}-${messageCounter}`
}

export function useChatSession(api: AgentApiClient): ChatSession {
  const [initial] = useState(loadConversation)
  const [sessionId, setSessionId] = useState<string | null>(initial.sessionId)
  const [messages, setMessages] = useState<ChatMessage[]>(initial.messages)
  const [latestAnswer, setLatestAnswer] = useState<ChatResponse | null>(null)
  const [answerSequence, setAnswerSequence] = useState(0)

  useEffect(() => {
    saveConversation({ sessionId, messages })
  }, [sessionId, messages])

  const appendMessage = useCallback((message: Omit<ChatMessage, 'id'>) => {
    setMessages((current) => [...current, { ...message, id: newMessageId() }])
  }, [])

  const chat = useMutation({
    // TanStack Query always calls the latest mutationFn, so this sees the current session.
    mutationFn: (question: string) => api.chat(question, sessionId ?? undefined),
    onSuccess: (answer) => {
      setSessionId(answer.session_id)
      setLatestAnswer(answer)
      setAnswerSequence((sequence) => sequence + 1)
      appendMessage({ role: 'assistant', text: answer.reply, fromSavedPlan: answer.plan_source === 'cache' })
    },
    onError: (error) => {
      appendMessage({
        role: 'error',
        text: describeAgentError(error),
        issues: error instanceof AgentApiError && error.issues.length ? error.issues : undefined,
      })
    },
  })

  const sendQuestion = useCallback(
    (question: string) => {
      const trimmed = question.trim()
      if (!trimmed || trimmed.length > MAX_QUESTION_LENGTH || chat.isPending) return
      appendMessage({ role: 'user', text: trimmed })
      chat.mutate(trimmed)
    },
    [appendMessage, chat],
  )

  const startNewConversation = useCallback(() => {
    if (sessionId) void api.deleteSession(sessionId).catch(() => undefined)
    clearConversation()
    setSessionId(null)
    setMessages([])
    setLatestAnswer(null)
    setAnswerSequence((sequence) => sequence + 1)
  }, [api, sessionId])

  return {
    messages,
    latestAnswer,
    answerSequence,
    isWaitingForAnswer: chat.isPending,
    sendQuestion,
    startNewConversation,
  }
}

// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest'

import { AgentApiError } from '../../api/agentApiClient'
import { clearConversation, loadConversation, saveConversation } from './chatStorage'
import { describeAgentError } from './describeAgentError'

beforeEach(() => sessionStorage.clear())

describe('chat storage', () => {
  it('round-trips the conversation and clears it', () => {
    const conversation = { sessionId: 's1', messages: [{ id: '1', role: 'user' as const, text: 'hi' }] }
    saveConversation(conversation)
    expect(loadConversation()).toEqual(conversation)
    clearConversation()
    expect(loadConversation()).toEqual({ sessionId: null, messages: [] })
  })

  it('starts fresh when the stored data is corrupt', () => {
    sessionStorage.setItem('transaction-dashboard.chat', '{not json')
    expect(loadConversation()).toEqual({ sessionId: null, messages: [] })
    sessionStorage.setItem('transaction-dashboard.chat', JSON.stringify({ sessionId: 42, messages: 'x' }))
    expect(loadConversation()).toEqual({ sessionId: null, messages: [] })
  })
})

describe('describeAgentError', () => {
  it.each([
    ['UNAUTHORIZED', /VITE_AGENT_API_TOKEN/],
    ['CLAUDE_UNAVAILABLE', /try again shortly/],
    ['TRANSACTION_SERVICE_UNAVAILABLE', /transaction data is unavailable/],
    ['NETWORK_ERROR', /Is it running\?/],
  ])('gives a friendly message for %s', (errorCode, expected) => {
    expect(describeAgentError(new AgentApiError(500, errorCode, 'raw'))).toMatch(expected)
  })

  it('uses the service message for other codes and handles non-API errors', () => {
    expect(describeAgentError(new AgentApiError(422, 'CLAUDE_DECLINED', 'Claude declined'))).toBe('Claude declined')
    expect(describeAgentError(new Error('boom'))).toBe('Something went wrong: boom')
  })
})

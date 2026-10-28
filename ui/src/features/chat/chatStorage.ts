// Keeps the conversation for the life of the browser tab (sessionStorage), so a reload
// doesn't lose it. This is display state only; the server keeps just questions and plans.
import type { ChatMessage } from './chatTypes'

const STORAGE_KEY = 'transaction-dashboard.chat'

export interface StoredConversation {
  sessionId: string | null
  messages: ChatMessage[]
}

const EMPTY_CONVERSATION: StoredConversation = { sessionId: null, messages: [] }

export function loadConversation(storage: Storage = sessionStorage): StoredConversation {
  try {
    const stored = storage.getItem(STORAGE_KEY)
    if (!stored) return EMPTY_CONVERSATION
    const parsed = JSON.parse(stored) as Partial<StoredConversation>
    return {
      sessionId: typeof parsed.sessionId === 'string' ? parsed.sessionId : null,
      messages: Array.isArray(parsed.messages) ? parsed.messages : [],
    }
  } catch {
    return EMPTY_CONVERSATION // corrupt or unavailable storage: start fresh
  }
}

export function saveConversation(conversation: StoredConversation, storage: Storage = sessionStorage): void {
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(conversation))
  } catch {
    // storage full or blocked: the conversation still works, it just won't survive a reload
  }
}

export function clearConversation(storage: Storage = sessionStorage): void {
  try {
    storage.removeItem(STORAGE_KEY)
  } catch {
    // nothing to clear
  }
}

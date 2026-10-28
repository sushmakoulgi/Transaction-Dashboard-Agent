import { useState, type FormEvent, type KeyboardEvent } from 'react'

import { MAX_QUESTION_LENGTH } from './useChatSession'

interface ChatInputProps {
  disabled: boolean
  onSend: (question: string) => void
}

export function ChatInput({ disabled, onSend }: ChatInputProps) {
  const [draft, setDraft] = useState('')
  const trimmedLength = draft.trim().length
  const canSend = !disabled && trimmedLength > 0 && trimmedLength <= MAX_QUESTION_LENGTH

  function send(event?: FormEvent) {
    event?.preventDefault()
    if (!canSend) return
    onSend(draft)
    setDraft('')
  }

  function sendOnEnter(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault() // Shift+Enter still adds a new line
      send()
    }
  }

  return (
    <form className="chat-input" onSubmit={send}>
      <textarea
        aria-label="Your question"
        placeholder="Ask about transactions… (Enter to send, Shift+Enter for a new line)"
        value={draft}
        maxLength={MAX_QUESTION_LENGTH}
        rows={2}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={sendOnEnter}
      />
      <div className="chat-input__footer">
        <span className="chat-input__counter">
          {draft.length}/{MAX_QUESTION_LENGTH}
        </span>
        <button type="submit" disabled={!canSend}>
          {disabled ? 'Waiting…' : 'Send'}
        </button>
      </div>
    </form>
  )
}

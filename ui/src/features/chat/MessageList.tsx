import { useEffect, useRef } from 'react'

import type { ChatMessage } from './chatTypes'
import { EXAMPLE_QUESTIONS } from './exampleQuestions'

interface MessageListProps {
  messages: ChatMessage[]
  isWaitingForAnswer: boolean
  onPickExample: (question: string) => void
}

export function MessageList({ messages, isWaitingForAnswer, onPickExample }: MessageListProps) {
  const endOfMessages = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endOfMessages.current?.scrollIntoView?.({ behavior: 'smooth', block: 'end' })
  }, [messages.length, isWaitingForAnswer])

  if (messages.length === 0 && !isWaitingForAnswer) {
    return (
      <div className="message-list message-list--empty">
        <p>Ask about transactions, payments, refunds or chargebacks. For example:</p>
        <ul className="example-questions">
          {EXAMPLE_QUESTIONS.map((question) => (
            <li key={question}>
              <button type="button" onClick={() => onPickExample(question)}>
                {question}
              </button>
            </li>
          ))}
        </ul>
      </div>
    )
  }

  return (
    <div className="message-list" aria-live="polite">
      {messages.map((message) => (
        <div key={message.id} className={`message message--${message.role}`} data-role={message.role}>
          <p>{message.text}</p>
          {message.issues && (
            <ul className="message-issues">
              {message.issues.map((issue) => (
                <li key={`${issue.path}-${issue.code}`}>
                  <code>{issue.path}</code>: {issue.message}
                  {issue.suggestions?.length ? ` (did you mean: ${issue.suggestions.join(', ')})` : ''}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
      {isWaitingForAnswer && (
        <div className="message message--assistant message--pending" role="status">
          <p>Looking that up…</p>
        </div>
      )}
      <div ref={endOfMessages} />
    </div>
  )
}

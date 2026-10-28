import { ChatInput } from './ChatInput'
import { MessageList } from './MessageList'
import type { ChatSession } from './useChatSession'
import './ChatPanel.css'

interface ChatPanelProps {
  session: ChatSession
}

export function ChatPanel({ session }: ChatPanelProps) {
  return (
    <section className="chat-panel" aria-label="Chat">
      <header className="chat-panel__header">
        <h2>Ask a question</h2>
        <button
          type="button"
          className="chat-panel__new"
          onClick={session.startNewConversation}
          disabled={session.messages.length === 0 || session.isWaitingForAnswer}
        >
          New conversation
        </button>
      </header>
      <MessageList
        messages={session.messages}
        isWaitingForAnswer={session.isWaitingForAnswer}
        onPickExample={session.sendQuestion}
      />
      <ChatInput disabled={session.isWaitingForAnswer} onSend={session.sendQuestion} />
    </section>
  )
}

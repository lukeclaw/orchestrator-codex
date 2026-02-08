import { useState, useRef, useEffect } from 'react'
import { useChat } from '../hooks/useChat'
import './ChatPage.css'

export default function ChatPage() {
  const { messages, sending, send } = useChat()
  const [input, setInput] = useState('')
  const messagesRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (messagesRef.current) {
      messagesRef.current.scrollTop = messagesRef.current.scrollHeight
    }
  }, [messages])

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const msg = input.trim()
    if (!msg || sending) return
    setInput('')
    send(msg)
  }

  return (
    <div className="chat-page">
      <div className="cp-messages" ref={messagesRef}>
        {messages.length === 0 && (
          <div className="cp-welcome">
            <h2>Chat with the Orchestrator</h2>
            <p>Ask questions, give instructions, or manage your sessions.</p>
          </div>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            className={`cp-msg ${m.role}`}
            data-testid={`chat-msg-${m.role}`}
          >
            <div className="cp-msg-role">{m.role === 'user' ? 'You' : 'Orchestrator'}</div>
            <div className="cp-msg-text">{m.text}</div>
          </div>
        ))}
        {sending && (
          <div className="cp-msg assistant">
            <div className="cp-msg-role">Orchestrator</div>
            <div className="cp-msg-text typing">Thinking...</div>
          </div>
        )}
      </div>

      <form className="cp-form" onSubmit={handleSubmit}>
        <input
          type="text"
          data-testid="chat-input"
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder="Ask the orchestrator..."
          autoComplete="off"
          disabled={sending}
        />
        <button type="submit" className="btn btn-primary" data-testid="chat-send" disabled={sending}>
          Send
        </button>
      </form>
    </div>
  )
}

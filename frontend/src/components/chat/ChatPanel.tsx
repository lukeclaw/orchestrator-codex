import { useState, useRef, useEffect } from 'react'
import { useChat } from '../../hooks/useChat'
import './ChatPanel.css'

export default function ChatPanel() {
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
    <>
      <div className="chat-messages" data-testid="chat-messages" ref={messagesRef}>
        {messages.length === 0 && (
          <p className="empty-state">Ask the orchestrator anything...</p>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            className={`chat-msg ${m.role}`}
            data-testid={`chat-msg-${m.role}`}
          >
            <div className="msg-role">{m.role === 'user' ? 'You' : 'Orchestrator'}</div>
            <div className="msg-text">{m.text}</div>
          </div>
        ))}
        {sending && (
          <div className="chat-msg assistant">
            <div className="msg-role">Orchestrator</div>
            <div className="msg-text typing">Thinking...</div>
          </div>
        )}
      </div>
      <form className="chat-form" onSubmit={handleSubmit}>
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
    </>
  )
}

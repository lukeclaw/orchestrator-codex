import { useState } from 'react'
import { useApp } from '../../context/AppContext'
import './Header.css'

export default function Header() {
  const { connected } = useApp()
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    navigator.clipboard.writeText('tmux attach -t orchestrator')
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <header className="app-header">
      <div className="header-left">
        <button
          className="tmux-hint"
          data-testid="tmux-hint"
          onClick={handleCopy}
          title="Click to copy"
        >
          {copied ? 'Copied!' : 'tmux attach -t orchestrator'}
        </button>
      </div>
      <div className="header-right">
        <span
          className={`connection-dot ${connected ? 'connected' : 'disconnected'}`}
          data-testid="connection-status"
        />
        <span className="connection-label">
          {connected ? 'Live' : 'Disconnected'}
        </span>
      </div>
    </header>
  )
}

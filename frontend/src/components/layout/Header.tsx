import { useApp } from '../../context/AppContext'
import './Header.css'

export default function Header() {
  const { connected } = useApp()

  return (
    <header className="app-header">
      <div className="header-left">
        <code className="tmux-hint" data-testid="tmux-hint">
          tmux attach -t orchestrator
        </code>
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

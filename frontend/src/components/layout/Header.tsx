import { useApp } from '../../context/AppContext'
import { useNavigationHistory } from '../../hooks/useNavigationHistory'
import { IconArrowLeft, IconArrowRight } from '../common/Icons'
import './Header.css'

export default function Header() {
  const { connected } = useApp()
  const { canGoBack, canGoForward, goBack, goForward } = useNavigationHistory()

  return (
    <header className="app-header">
      <div className="header-left">
        <nav className="header-nav-buttons">
          <button disabled={!canGoBack} onClick={goBack} title="Go back">
            <IconArrowLeft size={14} />
          </button>
          <button disabled={!canGoForward} onClick={goForward} title="Go forward">
            <IconArrowRight size={14} />
          </button>
        </nav>
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

import { useState, useEffect } from 'react'
import './GettingStartedModal.css'

interface Props {
  show: boolean
}

export default function GettingStartedModal({ show }: Props) {
  const [dismissed, setDismissed] = useState(false)
  const [visible, setVisible] = useState(false)

  // Animate in after a short delay
  useEffect(() => {
    if (show && !dismissed) {
      const t = setTimeout(() => setVisible(true), 400)
      return () => clearTimeout(t)
    }
  }, [show, dismissed])

  if (!show || dismissed) return null

  function handleDismiss() {
    setVisible(false)
    setTimeout(() => setDismissed(true), 200)
  }

  return (
    <div className={`getting-started-overlay ${visible ? 'visible' : ''}`} onClick={handleDismiss}>
      <div className="getting-started-card" onClick={e => e.stopPropagation()}>
        <div className="getting-started-arrow" />
        <h3 className="getting-started-title">Welcome to Orchestrator</h3>
        <p className="getting-started-desc">
          The brain manages workers that execute tasks across your projects.
        </p>
        <div className="getting-started-hint">
          Type <code>/create</code> in the Brain panel and describe what you want to build.
        </div>
        <button className="getting-started-btn" onClick={handleDismiss}>Got it</button>
      </div>
    </div>
  )
}

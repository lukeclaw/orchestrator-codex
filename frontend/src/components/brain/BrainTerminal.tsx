import TerminalView from '../terminal/TerminalView'
import './BrainTerminal.css'

interface BrainStatus {
  running: boolean
  session_id: string | null
  status: string | null
}

interface BrainTerminalProps {
  brainStatus: BrainStatus | null
  starting: boolean
  stopping: boolean
  onStart: () => void
  onStop: () => void
  onUserInput?: () => void
}

export default function BrainTerminal({
  brainStatus,
  starting,
  stopping,
  onStart,
  onStop,
  onUserInput,
}: BrainTerminalProps) {
  const isRunning = brainStatus?.running && brainStatus?.session_id

  return (
    <div className="brain-terminal">
      {isRunning && brainStatus.session_id ? (
        <div className="brain-terminal-area">
          <TerminalView key={brainStatus.session_id} sessionId={brainStatus.session_id} onUserInput={onUserInput} />
        </div>
      ) : (
        <div className="brain-empty">
          <div className="brain-empty-icon">&#x1F9E0;</div>
          <p>The orchestrator brain manages your workers.</p>
          <p>Start it to coordinate projects and monitor progress.</p>
          <button
            className="btn btn-primary btn-sm"
            onClick={onStart}
            disabled={starting}
            style={{ marginTop: 8 }}
          >
            {starting ? 'Starting...' : 'Start Brain'}
          </button>
        </div>
      )}
    </div>
  )
}

export type { BrainStatus }

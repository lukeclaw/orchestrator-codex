import { useState, useEffect, useCallback } from 'react'
import { api } from '../../api/client'
import TerminalView from '../terminal/TerminalView'
import './BrainTerminal.css'

interface BrainStatus {
  running: boolean
  session_id: string | null
  status: string | null
}

export default function BrainTerminal() {
  const [brainStatus, setBrainStatus] = useState<BrainStatus | null>(null)
  const [starting, setStarting] = useState(false)
  const [stopping, setStopping] = useState(false)

  const fetchStatus = useCallback(async () => {
    try {
      const status = await api<BrainStatus>('/api/brain/status')
      setBrainStatus(status)
    } catch {
      setBrainStatus({ running: false, session_id: null, status: null })
    }
  }, [])

  useEffect(() => {
    fetchStatus()
    const interval = setInterval(fetchStatus, 5000)
    return () => clearInterval(interval)
  }, [fetchStatus])

  async function handleStart() {
    setStarting(true)
    try {
      await api('/api/brain/start', { method: 'POST' })
      await fetchStatus()
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Failed to start brain')
    } finally {
      setStarting(false)
    }
  }

  async function handleStop() {
    setStopping(true)
    try {
      await api('/api/brain/stop', { method: 'POST' })
      await fetchStatus()
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Failed to stop brain')
    } finally {
      setStopping(false)
    }
  }

  const isRunning = brainStatus?.running && brainStatus?.session_id

  return (
    <div className="brain-terminal">
      <div className="brain-header">
        <div className="brain-title">
          <span className={`brain-indicator ${isRunning ? 'active' : 'inactive'}`} />
          <span>Orchestrator Brain</span>
        </div>
        <div className="brain-actions">
          {isRunning ? (
            <button
              className="btn btn-danger btn-sm"
              onClick={handleStop}
              disabled={stopping}
            >
              {stopping ? 'Stopping...' : 'Stop'}
            </button>
          ) : (
            <button
              className="btn btn-primary btn-sm"
              onClick={handleStart}
              disabled={starting}
            >
              {starting ? 'Starting...' : 'Start Brain'}
            </button>
          )}
        </div>
      </div>

      {isRunning && brainStatus.session_id ? (
        <div className="brain-terminal-area">
          <TerminalView sessionId={brainStatus.session_id} />
        </div>
      ) : (
        <div className="brain-empty">
          <div className="brain-empty-icon">&#x1F9E0;</div>
          <p>The orchestrator brain is a Claude Code instance that manages your workers.</p>
          <p>Start it to coordinate projects, assign tasks, and monitor progress.</p>
        </div>
      )}
    </div>
  )
}

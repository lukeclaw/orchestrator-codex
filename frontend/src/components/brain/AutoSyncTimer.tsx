import { useState, useEffect, useRef, useCallback, type MutableRefObject } from 'react'
import { api } from '../../api/client'
import { IconSync } from '../common/Icons'
import './AutoSyncTimer.css'

type TimerState = 'off' | 'counting' | 'syncing' | 'paused'

const SYNC_INTERVAL = 60
const PAUSE_DURATION = 180

interface AutoSyncTimerProps {
  brainSessionId: string | null
  brainStatus: string | null
  brainRunning: boolean
  userInteractionRef: MutableRefObject<(() => void) | null>
}

export default function AutoSyncTimer({
  brainSessionId,
  brainStatus,
  brainRunning,
  userInteractionRef,
}: AutoSyncTimerProps) {
  const [enabled, setEnabled] = useState(false)  // Off by default - feature not fully ready
  const [timerState, setTimerState] = useState<TimerState>('off')
  const [countdown, setCountdown] = useState(SYNC_INTERVAL)
  const [pauseCountdown, setPauseCountdown] = useState(0)

  // Register user interaction handler
  const handleUserInteraction = useCallback(() => {
    if (timerState === 'counting' || timerState === 'paused') {
      setTimerState('paused')
      setPauseCountdown(PAUSE_DURATION)
    }
  }, [timerState])

  useEffect(() => {
    userInteractionRef.current = handleUserInteraction
    return () => { userInteractionRef.current = null }
  }, [handleUserInteraction, userInteractionRef])

  // Sync action
  const triggerSync = useCallback(async () => {
    if (!brainRunning) return
    setTimerState('syncing')
    try {
      await api('/api/brain/sync', { method: 'POST' })
    } catch {
      // sync failed — go back to counting
    }
    // After sending, wait for brain to finish (will detect via brainStatus going back to idle)
  }, [brainRunning])

  // When syncing and brain goes back to idle, resume counting
  useEffect(() => {
    if (timerState === 'syncing' && brainStatus === 'idle') {
      setTimerState('counting')
      setCountdown(SYNC_INTERVAL)
    }
  }, [timerState, brainStatus])

  // Main timer tick
  useEffect(() => {
    if (!enabled || !brainRunning) return

    if (timerState === 'off' || timerState === 'syncing') return

    const id = setInterval(() => {
      if (timerState === 'counting') {
        // Freeze if brain is working
        if (brainStatus === 'working') return
        setCountdown(prev => {
          if (prev <= 1) {
            triggerSync()
            return SYNC_INTERVAL
          }
          return prev - 1
        })
      } else if (timerState === 'paused') {
        setPauseCountdown(prev => {
          if (prev <= 1) {
            setTimerState('counting')
            setCountdown(SYNC_INTERVAL)
            return 0
          }
          return prev - 1
        })
      }
    }, 1000)

    return () => clearInterval(id)
  }, [enabled, brainRunning, timerState, brainStatus, triggerSync])

  // Toggle
  function handleToggle() {
    if (enabled) {
      setEnabled(false)
      setTimerState('off')
    } else {
      setEnabled(true)
      setTimerState('counting')
      setCountdown(SYNC_INTERVAL)
    }
  }

  // Manual sync
  function handleManualSync() {
    if (brainStatus === 'working' || !brainRunning) return
    triggerSync()
  }

  // Format seconds as M:SS
  function formatTime(secs: number) {
    const m = Math.floor(secs / 60)
    const s = secs % 60
    return `${m}:${s.toString().padStart(2, '0')}`
  }

  if (!brainRunning) {
    return (
      <div className="auto-sync-timer" data-testid="auto-sync-timer">
        <span className="ast-label ast-muted">Auto-sync: brain not running</span>
      </div>
    )
  }

  const progress = timerState === 'counting'
    ? ((SYNC_INTERVAL - countdown) / SYNC_INTERVAL) * 100
    : 0

  return (
    <div className="auto-sync-timer" data-testid="auto-sync-timer">
      <div className="ast-controls">
        <button
          className={`ast-toggle ${enabled ? 'on' : 'off'}`}
          onClick={handleToggle}
          data-testid="auto-sync-toggle"
          title={enabled ? 'Disable auto-sync' : 'Enable auto-sync'}
        >
          <span className="ast-toggle-dot" />
          <span className="ast-toggle-label">{enabled ? 'ON' : 'OFF'}</span>
        </button>

        <span className="ast-status">
          {timerState === 'off' && 'Auto-sync off'}
          {timerState === 'counting' && brainStatus === 'working' && 'Brain working...'}
          {timerState === 'counting' && brainStatus !== 'working' && formatTime(countdown)}
          {timerState === 'paused' && `Paused ${formatTime(pauseCountdown)}`}
          {timerState === 'syncing' && 'Syncing...'}
        </span>

        <button
          className="ast-sync-btn"
          onClick={handleManualSync}
          disabled={brainStatus === 'working' || timerState === 'syncing'}
          title="Sync now"
        >
          <IconSync size={13} />
        </button>
      </div>

      {timerState === 'counting' && brainStatus !== 'working' && (
        <div className="ast-progress-bar">
          <div className="ast-progress-fill" style={{ width: `${progress}%` }} />
        </div>
      )}
    </div>
  )
}

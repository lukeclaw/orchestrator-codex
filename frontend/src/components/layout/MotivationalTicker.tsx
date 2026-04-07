import { useState, useEffect, useRef, useCallback } from 'react'
import { useTickerInsights, type InsightMessage } from '../../hooks/useTickerInsights'
import { useSettings } from '../../context/SettingsContext'
import { IconChevronRight } from '../common/Icons'
import './MotivationalTicker.css'

/** Render message text with the stat portion highlighted */
function MessageContent({ message }: { message: InsightMessage }) {
  if (!message.statRange) return <>{message.text}</>

  const [start, end] = message.statRange
  return (
    <>
      {message.text.slice(0, start)}
      <span className="ticker-stat">{message.text.slice(start, end)}</span>
      {message.text.slice(end)}
    </>
  )
}

const SPEED_MS: Record<string, number> = { '10s': 10000, '1m': 60000 }

export default function MotivationalTicker() {
  const { getValue } = useSettings()
  const tickerSpeed = String(getValue('ticker.speed') || '1m')
  const rotateMs = SPEED_MS[tickerSpeed] ?? 60000
  const { messages, loading } = useTickerInsights()
  const [activeSlot, setActiveSlot] = useState<'a' | 'b'>('a')
  const [slotA, setSlotA] = useState<InsightMessage | null>(null)
  const [slotB, setSlotB] = useState<InsightMessage | null>(null)
  const indexRef = useRef(0)
  const initialized = useRef(false)

  // Initialize on first non-empty messages; never reset after that.
  // The advance function always reads the latest messages array,
  // so new messages appear naturally as the ticker cycles.
  if (!initialized.current && messages.length > 0) {
    initialized.current = true
    indexRef.current = 0
    setSlotA(messages[0])
    if (messages.length > 1) setSlotB(messages[1])
    setActiveSlot('a')
  }

  // Use a ref for messages so advance always sees the latest array
  // without needing to be re-created (which would reset the interval)
  const messagesRef = useRef(messages)
  messagesRef.current = messages

  const advance = useCallback(() => {
    const msgs = messagesRef.current
    if (msgs.length < 2) return
    const nextIdx = (indexRef.current + 1) % msgs.length
    indexRef.current = nextIdx
    const nextMessage = msgs[nextIdx]
    // Put the next message into the inactive slot, then swap
    setActiveSlot(current => {
      if (current === 'a') {
        setSlotB(nextMessage)
        return 'b'
      } else {
        setSlotA(nextMessage)
        return 'a'
      }
    })
  }, []) // stable — reads from refs

  // Track interval so skip can reset it
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (messages.length < 2) return
    intervalRef.current = setInterval(advance, rotateMs)
    return () => { if (intervalRef.current) clearInterval(intervalRef.current) }
  }, [advance, rotateMs, messages.length])

  const skip = useCallback(() => {
    advance()
    // Reset the timer so next auto-rotation is a full interval from now
    if (intervalRef.current) clearInterval(intervalRef.current)
    intervalRef.current = setInterval(advance, rotateMs)
  }, [advance, rotateMs])

  if (tickerSpeed === 'off' || loading || messages.length === 0) return null

  const currentMessage = activeSlot === 'a' ? slotA : slotB
  const isRest = currentMessage?.type === 'rest-reminder'

  const skipBtn = messages.length >= 2 ? (
    <button className="ticker-skip" onClick={skip} title="Next message" aria-label="Next message">
      <IconChevronRight size={10} />
    </button>
  ) : null

  // Single message: static display
  if (messages.length === 1) {
    return (
      <div
        className={`ticker${messages[0].type === 'rest-reminder' ? ' ticker--rest' : ''}`}
        aria-live="polite"
        aria-atomic="true"
      >
        <span className="ticker-slot ticker-slot--active">
          <MessageContent message={messages[0]} />
        </span>
      </div>
    )
  }

  // Multiple messages: cross-fade rotation
  return (
    <div
      className={`ticker${isRest ? ' ticker--rest' : ''}`}
      aria-live="polite"
      aria-atomic="true"
    >
      <span className={`ticker-slot ${activeSlot === 'a' ? 'ticker-slot--active' : 'ticker-slot--inactive'}`}>
        {slotA && <><MessageContent message={slotA} />{skipBtn}</>}
      </span>
      <span className={`ticker-slot ${activeSlot === 'b' ? 'ticker-slot--active' : 'ticker-slot--inactive'}`}>
        {slotB && <><MessageContent message={slotB} />{skipBtn}</>}
      </span>
    </div>
  )
}

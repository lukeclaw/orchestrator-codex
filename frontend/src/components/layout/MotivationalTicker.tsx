import { useState, useEffect, useRef, useCallback } from 'react'
import { useTickerInsights, type InsightMessage } from '../../hooks/useTickerInsights'
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

const ROTATE_INTERVAL = 60000 // 1 minute per message

export default function MotivationalTicker() {
  const { messages, loading } = useTickerInsights()
  const [index, setIndex] = useState(0)
  const [activeSlot, setActiveSlot] = useState<'a' | 'b'>('a')
  const [slotA, setSlotA] = useState<InsightMessage | null>(null)
  const [slotB, setSlotB] = useState<InsightMessage | null>(null)
  const prevMessagesKey = useRef('')

  // Reset index when messages content changes
  const messagesKey = messages.map(m => m.id).join(',')
  if (messagesKey !== prevMessagesKey.current) {
    prevMessagesKey.current = messagesKey
    if (messages.length > 0) {
      setIndex(0)
      setSlotA(messages[0])
      setSlotB(messages.length > 1 ? messages[1] : null)
      setActiveSlot('a')
    }
  }

  const advance = useCallback(() => {
    if (messages.length < 2) return
    setIndex(prev => {
      const nextIdx = (prev + 1) % messages.length
      const nextMessage = messages[nextIdx]
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
      return nextIdx
    })
  }, [messages])

  useEffect(() => {
    if (messages.length < 2) return
    const timer = setInterval(advance, ROTATE_INTERVAL)
    return () => clearInterval(timer)
  }, [advance, messages.length])

  if (loading || messages.length === 0) return null

  const currentMessage = activeSlot === 'a' ? slotA : slotB
  const isRest = currentMessage?.type === 'rest-reminder'

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
        {slotA && <MessageContent message={slotA} />}
      </span>
      <span className={`ticker-slot ${activeSlot === 'b' ? 'ticker-slot--active' : 'ticker-slot--inactive'}`}>
        {slotB && <MessageContent message={slotB} />}
      </span>
    </div>
  )
}

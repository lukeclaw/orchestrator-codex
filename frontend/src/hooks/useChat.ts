import { useState, useCallback } from 'react'
import { api } from '../api/client'
import type { ChatResponse } from '../api/types'

export interface ChatMessage {
  role: 'user' | 'assistant'
  text: string
}

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [sending, setSending] = useState(false)

  const send = useCallback(async (text: string) => {
    setMessages(prev => [...prev, { role: 'user', text }])
    setSending(true)

    try {
      const data = await api<ChatResponse>('/api/chat', {
        method: 'POST',
        body: JSON.stringify({ message: text }),
      })
      setMessages(prev => [...prev, { role: 'assistant', text: data.response || 'No response' }])
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Unknown error'
      setMessages(prev => [...prev, { role: 'assistant', text: `Error: ${msg}` }])
    } finally {
      setSending(false)
    }
  }, [])

  return { messages, sending, send }
}

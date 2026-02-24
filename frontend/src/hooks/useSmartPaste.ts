import { useCallback } from 'react'

export type PasteContentType = 'image' | 'url' | 'long_text' | 'short_text'

export interface PasteResult {
  type: PasteContentType
  imageData?: string  // base64 data URL for images
  text?: string       // raw text for text/url
}

/**
 * Classify text content: URL > long_text (>1000 chars) > short_text.
 */
function classifyText(text: string): PasteContentType {
  const trimmed = text.trim()
  // Single-line URL check
  if (!trimmed.includes('\n') && /^https?:\/\/\S+$/i.test(trimmed)) {
    return 'url'
  }
  if (trimmed.length > 1000) {
    return 'long_text'
  }
  return 'short_text'
}

/**
 * Read clipboard via the backend /api/clipboard endpoint (uses pbpaste +
 * osascript natively, no WebKit permission popup).  Falls back to
 * navigator.clipboard for browser-only dev contexts.
 */
async function readClipboardViaBackend(): Promise<PasteResult> {
  const res = await fetch('/api/clipboard')
  if (!res.ok) throw new Error('Backend clipboard read failed')
  const data: { text: string | null; image_base64: string | null } = await res.json()

  // Prefer image when both are present (screenshot takes priority)
  if (data.image_base64) {
    return { type: 'image', imageData: `data:image/png;base64,${data.image_base64}` }
  }
  if (data.text) {
    const type = classifyText(data.text)
    return { type, text: data.text.trim() }
  }
  throw new Error('Clipboard is empty')
}

/**
 * Fallback: read clipboard via the browser Clipboard API.
 * Used when the backend endpoint is unavailable (e.g. running in a browser
 * without the Python server).
 */
async function readClipboardViaBrowser(): Promise<PasteResult> {
  try {
    const text = await navigator.clipboard.readText()
    if (text.trim()) {
      const type = classifyText(text)
      return { type, text: text.trim() }
    }
  } catch {
    // readText() failed — fall through to read()
  }

  const items = await navigator.clipboard.read()
  for (const item of items) {
    const imageType = item.types.find(t => t.startsWith('image/'))
    if (imageType) {
      const blob = await item.getType(imageType)
      const base64 = await blobToBase64(blob)
      return { type: 'image', imageData: base64 }
    }
  }

  throw new Error('Clipboard is empty')
}

/**
 * Hook that reads the clipboard and classifies the content.
 * Priority: backend native read > browser Clipboard API.
 */
export function useSmartPaste() {
  const readClipboard = useCallback(async (): Promise<PasteResult> => {
    try {
      return await readClipboardViaBackend()
    } catch {
      // Backend unavailable — fall back to browser API
      return readClipboardViaBrowser()
    }
  }, [])

  return { readClipboard }
}

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = reject
    reader.readAsDataURL(blob)
  })
}

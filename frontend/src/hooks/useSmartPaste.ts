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
 * Hook that reads the clipboard and classifies the content.
 * Priority: image > URL > long_text > short_text.
 */
export function useSmartPaste() {
  const readClipboard = useCallback(async (): Promise<PasteResult> => {
    // Try reading rich clipboard data (images) first
    try {
      const items = await navigator.clipboard.read()
      for (const item of items) {
        const imageType = item.types.find(t => t.startsWith('image/'))
        if (imageType) {
          const blob = await item.getType(imageType)
          const base64 = await blobToBase64(blob)
          return { type: 'image', imageData: base64 }
        }
      }
    } catch {
      // Clipboard.read() may not be supported or allowed; fall through to text
    }

    // Fall back to text
    const text = await navigator.clipboard.readText()
    if (!text.trim()) {
      throw new Error('Clipboard is empty')
    }
    const type = classifyText(text)
    return { type, text: text.trim() }
  }, [])

  /**
   * Lightweight probe: returns true if the clipboard contains an image or a URL.
   * Used to decide whether to enable/disable a "paste as link" button.
   * Returns true on permission errors so the button stays enabled as a fallback.
   */
  const peekClipboardForLink = useCallback(async (): Promise<boolean> => {
    try {
      const items = await navigator.clipboard.read()
      for (const item of items) {
        if (item.types.some(t => t.startsWith('image/'))) return true
      }
    } catch {
      // clipboard.read() may not be available; fall through to text
    }

    try {
      const text = await navigator.clipboard.readText()
      const trimmed = text.trim()
      if (!trimmed) return false
      return !trimmed.includes('\n') && /^https?:\/\/\S+$/i.test(trimmed)
    } catch {
      // Can't read clipboard (permission denied) — assume valid so we don't
      // permanently disable the button; validation on click will catch it.
      return true
    }
  }, [])

  return { readClipboard, peekClipboardForLink }
}

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = reject
    reader.readAsDataURL(blob)
  })
}

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
    // Try readText() first — it does NOT trigger Chromium's clipboard permission
    // popup, unlike read(). Only fall back to read() for image-only clipboard.
    try {
      const text = await navigator.clipboard.readText()
      if (text.trim()) {
        const type = classifyText(text)
        return { type, text: text.trim() }
      }
    } catch {
      // readText() failed (permission denied, etc.) — fall through to read()
    }

    // No text found — try the rich clipboard API for images
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

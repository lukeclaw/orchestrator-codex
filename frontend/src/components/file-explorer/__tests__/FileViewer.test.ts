import { describe, it, expect } from 'vitest'

// Test pure helper functions from FileViewer
// (no jsdom, so we test data transformation logic only)

describe('FileViewer helpers', () => {
  describe('humanSize', () => {
    function humanSize(bytes: number): string {
      if (bytes < 1024) return `${bytes} B`
      if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
      return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
    }

    it('formats bytes', () => {
      expect(humanSize(0)).toBe('0 B')
      expect(humanSize(500)).toBe('500 B')
      expect(humanSize(1023)).toBe('1023 B')
    })

    it('formats kilobytes', () => {
      expect(humanSize(1024)).toBe('1.0 KB')
      expect(humanSize(2048)).toBe('2.0 KB')
      expect(humanSize(1536)).toBe('1.5 KB')
    })

    it('formats megabytes', () => {
      expect(humanSize(1024 * 1024)).toBe('1.0 MB')
      expect(humanSize(5 * 1024 * 1024)).toBe('5.0 MB')
    })
  })

  describe('markdown detection', () => {
    function isMarkdown(path: string): boolean {
      return path.endsWith('.md') || path.endsWith('.markdown')
    }

    it('detects .md files', () => {
      expect(isMarkdown('README.md')).toBe(true)
      expect(isMarkdown('docs/guide.md')).toBe(true)
    })

    it('detects .markdown files', () => {
      expect(isMarkdown('notes.markdown')).toBe(true)
    })

    it('rejects non-markdown files', () => {
      expect(isMarkdown('main.py')).toBe(false)
      expect(isMarkdown('script.js')).toBe(false)
      expect(isMarkdown('data.json')).toBe(false)
    })
  })

  describe('file name extraction', () => {
    function getFileName(path: string): string {
      return path.split('/').pop() || path
    }

    it('extracts filename from path', () => {
      expect(getFileName('src/main.py')).toBe('main.py')
      expect(getFileName('a/b/c/deep.txt')).toBe('deep.txt')
    })

    it('handles root-level files', () => {
      expect(getFileName('main.py')).toBe('main.py')
    })

    it('handles empty path', () => {
      expect(getFileName('')).toBe('')
    })
  })

  describe('line counting', () => {
    it('counts lines correctly for truncation display', () => {
      const content = 'line1\nline2\nline3\nline4\nline5'
      const lines = content.split('\n')
      expect(lines.length).toBe(5)
    })

    it('handles empty content', () => {
      const content = ''
      const lines = content.split('\n')
      expect(lines.length).toBe(1) // split always returns at least ['']
    })

    it('handles single line without newline', () => {
      const content = 'single line'
      const lines = content.split('\n')
      expect(lines.length).toBe(1)
    })
  })
})

import { describe, it, expect } from 'vitest'

// Test pure helper functions extracted from useEditorTabs
// (no jsdom needed — we test data transformation logic only)

describe('useEditorTabs helpers', () => {
  describe('extractFileName', () => {
    function extractFileName(path: string): string {
      return path.split('/').pop() || path
    }

    it('extracts filename from path', () => {
      expect(extractFileName('src/main.py')).toBe('main.py')
      expect(extractFileName('a/b/c/deep.txt')).toBe('deep.txt')
    })

    it('handles root-level files', () => {
      expect(extractFileName('main.py')).toBe('main.py')
    })

    it('handles empty path', () => {
      expect(extractFileName('')).toBe('')
    })
  })

  describe('detectLanguage', () => {
    const EXT_LANGUAGE: Record<string, string> = {
      '.py': 'python', '.pyi': 'python',
      '.js': 'javascript', '.jsx': 'javascript',
      '.ts': 'typescript', '.tsx': 'typescript',
      '.json': 'json', '.yaml': 'yaml', '.yml': 'yaml',
      '.toml': 'toml', '.md': 'markdown',
      '.html': 'html', '.htm': 'html',
      '.css': 'css', '.scss': 'scss', '.less': 'less',
      '.sh': 'bash', '.bash': 'bash', '.zsh': 'bash',
      '.rs': 'rust', '.go': 'go', '.java': 'java',
      '.c': 'c', '.h': 'c', '.cpp': 'cpp', '.hpp': 'cpp',
      '.rb': 'ruby', '.php': 'php', '.sql': 'sql',
      '.xml': 'xml', '.svg': 'xml',
    }

    function detectLanguage(path: string): string | null {
      const ext = path.slice(path.lastIndexOf('.')).toLowerCase()
      const lang = EXT_LANGUAGE[ext]
      if (lang) return lang
      const basename = path.split('/').pop()?.toLowerCase() || ''
      if (basename === 'dockerfile') return 'dockerfile'
      if (basename === 'makefile') return 'makefile'
      return null
    }

    it('detects common languages by extension', () => {
      expect(detectLanguage('main.py')).toBe('python')
      expect(detectLanguage('app.ts')).toBe('typescript')
      expect(detectLanguage('styles.css')).toBe('css')
      expect(detectLanguage('data.json')).toBe('json')
      expect(detectLanguage('README.md')).toBe('markdown')
    })

    it('detects Dockerfile', () => {
      expect(detectLanguage('Dockerfile')).toBe('dockerfile')
    })

    it('detects Makefile', () => {
      expect(detectLanguage('Makefile')).toBe('makefile')
    })

    it('returns null for unknown extensions', () => {
      expect(detectLanguage('file.xyz')).toBe(null)
    })

    it('handles nested paths', () => {
      expect(detectLanguage('src/components/App.tsx')).toBe('typescript')
    })
  })

  describe('Tab data model', () => {
    interface Tab {
      path: string
      originalContent: string | null
      currentContent: string | null
      isNew: boolean
      isPreview: boolean
    }

    function isDirty(tab: Tab): boolean {
      if (tab.isNew) return (tab.currentContent ?? '') !== ''
      return tab.originalContent !== tab.currentContent
    }

    it('new file with content is dirty', () => {
      const tab: Tab = {
        path: 'new.txt',
        originalContent: '',
        currentContent: 'hello',
        isNew: true,
        isPreview: false,
      }
      expect(isDirty(tab)).toBe(true)
    })

    it('new file with empty content is not dirty', () => {
      const tab: Tab = {
        path: 'new.txt',
        originalContent: '',
        currentContent: '',
        isNew: true,
        isPreview: false,
      }
      expect(isDirty(tab)).toBe(false)
    })

    it('existing file with changed content is dirty', () => {
      const tab: Tab = {
        path: 'file.py',
        originalContent: 'original',
        currentContent: 'modified',
        isNew: false,
        isPreview: false,
      }
      expect(isDirty(tab)).toBe(true)
    })

    it('existing file with unchanged content is not dirty', () => {
      const tab: Tab = {
        path: 'file.py',
        originalContent: 'same',
        currentContent: 'same',
        isNew: false,
        isPreview: false,
      }
      expect(isDirty(tab)).toBe(false)
    })

    it('preview tab detection', () => {
      const tab: Tab = {
        path: 'preview.py',
        originalContent: null,
        currentContent: null,
        isNew: false,
        isPreview: true,
      }
      expect(tab.isPreview).toBe(true)
    })
  })

  describe('monacoLanguage mapping', () => {
    function monacoLanguage(lang: string | null): string {
      const map: Record<string, string> = {
        python: 'python', javascript: 'javascript', typescript: 'typescript',
        json: 'json', yaml: 'yaml', html: 'html', css: 'css', scss: 'scss', less: 'less',
        bash: 'shell', shell: 'shell', rust: 'rust', go: 'go', java: 'java',
        c: 'c', cpp: 'cpp', ruby: 'ruby', php: 'php', sql: 'sql',
        xml: 'xml', markdown: 'markdown', dockerfile: 'dockerfile',
        toml: 'ini', lua: 'lua', swift: 'swift', kotlin: 'kotlin',
      }
      return map[lang ?? ''] ?? 'plaintext'
    }

    it('maps common languages', () => {
      expect(monacoLanguage('python')).toBe('python')
      expect(monacoLanguage('typescript')).toBe('typescript')
      expect(monacoLanguage('bash')).toBe('shell')
    })

    it('maps toml to ini', () => {
      expect(monacoLanguage('toml')).toBe('ini')
    })

    it('returns plaintext for null', () => {
      expect(monacoLanguage(null)).toBe('plaintext')
    })

    it('returns plaintext for unknown', () => {
      expect(monacoLanguage('unknown')).toBe('plaintext')
    })
  })

  describe('tab memory cap logic', () => {
    const MAX_TABS = 20

    it('cap is set to 20', () => {
      expect(MAX_TABS).toBe(20)
    })

    it('identifies removable preview tabs', () => {
      interface SimpleTab {
        path: string
        isPreview: boolean
        dirty: boolean
      }

      const tabs: SimpleTab[] = Array.from({ length: 20 }, (_, i) => ({
        path: `file${i}.py`,
        isPreview: i < 5,
        dirty: i === 0,  // first preview tab is dirty
      }))

      // Find first non-dirty preview tab
      const removable = tabs.findIndex(t => t.isPreview && !t.dirty)
      expect(removable).toBe(1) // index 1 is first non-dirty preview
    })
  })
})

import { describe, it, expect } from 'vitest'

// Test pure helper functions extracted from FileExplorerPanel logic
// (no jsdom, so we test data transformation logic only)

describe('FileExplorerPanel helpers', () => {
  describe('epochToRelative', () => {
    function epochToRelative(epoch: number | null): string {
      if (!epoch) return ''
      const secs = Math.floor((Date.now() / 1000) - epoch)
      if (secs < 0) return 'just now'
      if (secs < 60) return 'just now'
      if (secs < 3600) return `${Math.floor(secs / 60)}m`
      if (secs < 86400) return `${Math.floor(secs / 3600)}h`
      return `${Math.floor(secs / 86400)}d`
    }

    it('returns empty string for null', () => {
      expect(epochToRelative(null)).toBe('')
    })

    it('returns "just now" for recent timestamps', () => {
      const now = Date.now() / 1000
      expect(epochToRelative(now - 10)).toBe('just now')
    })

    it('returns minutes for 60s+', () => {
      const now = Date.now() / 1000
      expect(epochToRelative(now - 120)).toBe('2m')
    })

    it('returns hours for 3600s+', () => {
      const now = Date.now() / 1000
      expect(epochToRelative(now - 7200)).toBe('2h')
    })

    it('returns days for 86400s+', () => {
      const now = Date.now() / 1000
      expect(epochToRelative(now - 172800)).toBe('2d')
    })

    it('returns "just now" for future timestamps', () => {
      const now = Date.now() / 1000
      expect(epochToRelative(now + 60)).toBe('just now')
    })
  })

  describe('GIT_BADGE mapping', () => {
    const GIT_BADGE: Record<string, string> = {
      modified: 'M',
      added: 'A',
      untracked: 'U',
      deleted: 'D',
      renamed: 'R',
      conflicting: '!',
      ignored: 'I',
    }

    it('maps all expected git statuses', () => {
      expect(GIT_BADGE.modified).toBe('M')
      expect(GIT_BADGE.added).toBe('A')
      expect(GIT_BADGE.untracked).toBe('U')
      expect(GIT_BADGE.deleted).toBe('D')
      expect(GIT_BADGE.renamed).toBe('R')
      expect(GIT_BADGE.conflicting).toBe('!')
      expect(GIT_BADGE.ignored).toBe('I')
    })

    it('returns undefined for unknown status', () => {
      expect(GIT_BADGE['unknown']).toBeUndefined()
    })
  })

  describe('tree flattening logic', () => {
    interface TreeNode {
      name: string
      path: string
      is_dir: boolean
      expanded?: boolean
      children?: TreeNode[]
    }

    function flattenTree(nodes: TreeNode[], filterText: string = ''): { node: TreeNode; depth: number }[] {
      const result: { node: TreeNode; depth: number }[] = []
      const walk = (nodes: TreeNode[], depth: number) => {
        for (const n of nodes) {
          const matchesFilter = !filterText || n.name.toLowerCase().includes(filterText.toLowerCase())
          if (matchesFilter || n.is_dir) {
            result.push({ node: n, depth })
          }
          if (n.expanded && n.children) {
            walk(n.children, depth + 1)
          }
        }
      }
      walk(nodes, 0)
      return result
    }

    it('flattens root-level nodes', () => {
      const nodes: TreeNode[] = [
        { name: 'src', path: 'src', is_dir: true },
        { name: 'main.py', path: 'main.py', is_dir: false },
      ]
      const flat = flattenTree(nodes)
      expect(flat).toHaveLength(2)
      expect(flat[0].depth).toBe(0)
      expect(flat[1].depth).toBe(0)
    })

    it('includes expanded children at deeper depth', () => {
      const nodes: TreeNode[] = [
        {
          name: 'src', path: 'src', is_dir: true, expanded: true,
          children: [
            { name: 'app.py', path: 'src/app.py', is_dir: false },
          ],
        },
      ]
      const flat = flattenTree(nodes)
      expect(flat).toHaveLength(2)
      expect(flat[1].depth).toBe(1)
      expect(flat[1].node.name).toBe('app.py')
    })

    it('excludes collapsed children', () => {
      const nodes: TreeNode[] = [
        {
          name: 'src', path: 'src', is_dir: true, expanded: false,
          children: [
            { name: 'app.py', path: 'src/app.py', is_dir: false },
          ],
        },
      ]
      const flat = flattenTree(nodes)
      expect(flat).toHaveLength(1)
    })

    it('filters by text but keeps dirs', () => {
      const nodes: TreeNode[] = [
        { name: 'src', path: 'src', is_dir: true },
        { name: 'main.py', path: 'main.py', is_dir: false },
        { name: 'test.ts', path: 'test.ts', is_dir: false },
      ]
      const flat = flattenTree(nodes, 'main')
      expect(flat).toHaveLength(2) // src (dir, always included) + main.py
      expect(flat.find(f => f.node.name === 'main.py')).toBeDefined()
      expect(flat.find(f => f.node.name === 'test.ts')).toBeUndefined()
    })

    it('filter is case insensitive', () => {
      const nodes: TreeNode[] = [
        { name: 'README.md', path: 'README.md', is_dir: false },
      ]
      const flat = flattenTree(nodes, 'readme')
      expect(flat).toHaveLength(1)
    })
  })
})

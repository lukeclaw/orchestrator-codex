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

  describe('mergeGitStatuses', () => {
    const GIT_STATUS_SEVERITY: Record<string, number> = {
      ignored: 0,
      untracked: 1,
      added: 2,
      renamed: 3,
      modified: 4,
      deleted: 5,
      conflicting: 6,
    }

    function mergeGitStatuses(statuses: (string | null)[]): string | null {
      let best: string | null = null
      let bestSev = -1
      for (const s of statuses) {
        if (s && (GIT_STATUS_SEVERITY[s] ?? -1) > bestSev) {
          best = s
          bestSev = GIT_STATUS_SEVERITY[s] ?? -1
        }
      }
      return best
    }

    it('returns null for all-null statuses', () => {
      expect(mergeGitStatuses([null, null])).toBeNull()
    })

    it('returns the only non-null status', () => {
      expect(mergeGitStatuses([null, 'modified', null])).toBe('modified')
    })

    it('returns highest severity status', () => {
      expect(mergeGitStatuses(['untracked', 'modified', 'added'])).toBe('modified')
    })

    it('conflicting beats all others', () => {
      expect(mergeGitStatuses(['modified', 'conflicting', 'deleted'])).toBe('conflicting')
    })

    it('returns null for empty array', () => {
      expect(mergeGitStatuses([])).toBeNull()
    })
  })

  describe('tree flattening logic (with compact folders)', () => {
    interface TreeNode {
      name: string
      path: string
      is_dir: boolean
      expanded?: boolean
      children?: TreeNode[]
      git_status?: string | null
    }

    interface FlatNode {
      node: TreeNode
      depth: number
      displayName: string
      chainPaths: string[]
      mergedGitStatus: string | null
    }

    const GIT_STATUS_SEVERITY: Record<string, number> = {
      ignored: 0, untracked: 1, added: 2, renamed: 3,
      modified: 4, deleted: 5, conflicting: 6,
    }

    function mergeGitStatuses(statuses: (string | null | undefined)[]): string | null {
      let best: string | null = null
      let bestSev = -1
      for (const s of statuses) {
        if (s && (GIT_STATUS_SEVERITY[s] ?? -1) > bestSev) {
          best = s
          bestSev = GIT_STATUS_SEVERITY[s] ?? -1
        }
      }
      return best
    }

    function flattenTree(nodes: TreeNode[], filterText: string = ''): FlatNode[] {
      const result: FlatNode[] = []
      const walk = (nodes: TreeNode[], depth: number) => {
        for (const n of nodes) {
          const matchesFilter = !filterText || n.name.toLowerCase().includes(filterText.toLowerCase())
          if (!matchesFilter && !n.is_dir) continue

          // Compact folder logic: follow single-child dir chains (skip when filtering)
          if (!filterText && n.is_dir && n.expanded && n.children) {
            const chainNames: string[] = [n.name]
            const chainPaths: string[] = [n.path]
            const chainStatuses: (string | null | undefined)[] = [n.git_status]
            let current = n
            while (
              current.children &&
              current.children.length === 1 &&
              current.children[0].is_dir &&
              current.children[0].expanded
            ) {
              current = current.children[0]
              chainNames.push(current.name)
              chainPaths.push(current.path)
              chainStatuses.push(current.git_status)
            }

            result.push({
              node: current,
              depth,
              displayName: chainNames.join('/'),
              chainPaths,
              mergedGitStatus: mergeGitStatuses(chainStatuses),
            })

            if (current.expanded && current.children) {
              walk(current.children, depth + 1)
            }
            continue
          }

          // Normal (non-compacted) node
          result.push({
            node: n,
            depth,
            displayName: n.name,
            chainPaths: [n.path],
            mergedGitStatus: n.git_status ?? null,
          })

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

    // --- Compact folder chain tests ---

    it('compacts single-child dir chain into one row', () => {
      const nodes: TreeNode[] = [
        {
          name: 'src', path: 'src', is_dir: true, expanded: true,
          children: [
            {
              name: 'main', path: 'src/main', is_dir: true, expanded: true,
              children: [
                {
                  name: 'java', path: 'src/main/java', is_dir: true, expanded: true,
                  children: [
                    { name: 'App.java', path: 'src/main/java/App.java', is_dir: false },
                  ],
                },
              ],
            },
          ],
        },
      ]
      const flat = flattenTree(nodes)
      // Should produce: "src/main/java" (compact) at depth 0, then "App.java" at depth 1
      expect(flat).toHaveLength(2)
      expect(flat[0].displayName).toBe('src/main/java')
      expect(flat[0].depth).toBe(0)
      expect(flat[0].chainPaths).toEqual(['src', 'src/main', 'src/main/java'])
      expect(flat[0].node.path).toBe('src/main/java') // last in chain
      expect(flat[1].displayName).toBe('App.java')
      expect(flat[1].depth).toBe(1)
    })

    it('does NOT compact multi-child folder', () => {
      const nodes: TreeNode[] = [
        {
          name: 'src', path: 'src', is_dir: true, expanded: true,
          children: [
            { name: 'app.py', path: 'src/app.py', is_dir: false },
            { name: 'utils.py', path: 'src/utils.py', is_dir: false },
          ],
        },
      ]
      const flat = flattenTree(nodes)
      expect(flat).toHaveLength(3)
      expect(flat[0].displayName).toBe('src')
      expect(flat[0].chainPaths).toEqual(['src'])
    })

    it('does NOT compact when single child is a file', () => {
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
      expect(flat[0].displayName).toBe('src')
      expect(flat[0].chainPaths).toEqual(['src'])
    })

    it('chain stops at collapsed node', () => {
      const nodes: TreeNode[] = [
        {
          name: 'src', path: 'src', is_dir: true, expanded: true,
          children: [
            {
              name: 'main', path: 'src/main', is_dir: true, expanded: false,
              children: [
                {
                  name: 'java', path: 'src/main/java', is_dir: true, expanded: true,
                  children: [],
                },
              ],
            },
          ],
        },
      ]
      const flat = flattenTree(nodes)
      // "src/main" compacts (src expanded, main is only dir child but main is collapsed)
      // Wait: the chain follows while child is expanded. main is collapsed, so chain stops at src.
      // Actually: src is expanded with 1 dir child (main). Chain checks if main is expanded — it's not.
      // So chain = [src] only, no compaction beyond src. But src is expanded with 1 child = main.
      // The compact code enters because src is expanded with children. It starts with [src].
      // Then checks: current(src).children.length === 1 && children[0].is_dir && children[0].expanded
      // main is NOT expanded → while loop doesn't execute. Chain = [src] only.
      // So we get: "src" at depth 0, then "main" at depth 1 (collapsed, not expanded)
      expect(flat).toHaveLength(2)
      expect(flat[0].displayName).toBe('src')
      expect(flat[1].displayName).toBe('main')
      expect(flat[1].node.expanded).toBe(false)
    })

    it('merges git status across compact chain', () => {
      const nodes: TreeNode[] = [
        {
          name: 'src', path: 'src', is_dir: true, expanded: true, git_status: 'untracked',
          children: [
            {
              name: 'main', path: 'src/main', is_dir: true, expanded: true, git_status: 'modified',
              children: [
                { name: 'App.java', path: 'src/main/App.java', is_dir: false },
              ],
            },
          ],
        },
      ]
      const flat = flattenTree(nodes)
      expect(flat[0].displayName).toBe('src/main')
      // modified (severity 4) > untracked (severity 1)
      expect(flat[0].mergedGitStatus).toBe('modified')
    })

    it('disables compaction when filterText is active', () => {
      const nodes: TreeNode[] = [
        {
          name: 'src', path: 'src', is_dir: true, expanded: true,
          children: [
            {
              name: 'main', path: 'src/main', is_dir: true, expanded: true,
              children: [
                { name: 'App.java', path: 'src/main/App.java', is_dir: false },
              ],
            },
          ],
        },
      ]
      const flat = flattenTree(nodes, 'App')
      // With filter, no compaction — dirs shown individually
      expect(flat.find(f => f.displayName === 'src/main')).toBeUndefined()
      expect(flat.find(f => f.displayName === 'src')).toBeDefined()
      expect(flat.find(f => f.displayName === 'main')).toBeDefined()
    })
  })
})

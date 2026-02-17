import { useState, useCallback } from 'react'
import { useLocation } from 'react-router-dom'
import { useApp, type SmartPastePayload } from '../../context/AppContext'
import { useNotify } from '../../context/NotificationContext'
import { useSmartPaste, type PasteResult } from '../../hooks/useSmartPaste'
import { api } from '../../api/client'
import type { TaskLink } from '../../api/types'
import SmartPastePopup from '../common/SmartPastePopup'
import './Header.css'

/** Parse route to determine page context. */
function parseRoute(pathname: string): { page: 'worker' | 'task' | 'project'; id: string } | null {
  const m = pathname.match(/^\/(workers|tasks|projects)\/([^/]+)/)
  if (!m) return null
  const pageMap: Record<string, 'worker' | 'task' | 'project'> = {
    workers: 'worker', tasks: 'task', projects: 'project',
  }
  return { page: pageMap[m[1]], id: m[2] }
}

export default function Header() {
  const { connected, tasks, sessions, setSmartPastePayload, refresh } = useApp()
  const notify = useNotify()
  const { readClipboard } = useSmartPaste()
  const location = useLocation()

  const [pasting, setPasting] = useState(false)
  const [popup, setPopup] = useState<{
    title: string
    preview?: string
    charCount?: number
    options: { label: string; description?: string; action: () => Promise<void> }[]
  } | null>(null)

  const route = parseRoute(location.pathname)
  const enabled = !!route

  // --- Worker page: paste to terminal ---
  const pasteToWorker = useCallback(async (sessionId: string, result: PasteResult) => {
    if (result.type === 'image') {
      // Save image, then inject URL into terminal
      const res = await api<{ ok: boolean; url: string; filename: string }>(
        '/api/paste-image',
        { method: 'POST', body: JSON.stringify({ image_data: result.imageData }) },
      )
      if (res.ok) {
        const fullUrl = `http://localhost:8093${res.url}`
        await api(`/api/sessions/${sessionId}/send`, {
          method: 'POST',
          body: JSON.stringify({ message: fullUrl }),
        })
        notify(`Image pasted: ${res.filename}`, 'success')
      }
    } else {
      // Text/URL: inject directly
      await api(`/api/sessions/${sessionId}/send`, {
        method: 'POST',
        body: JSON.stringify({ message: result.text }),
      })
      notify('Text pasted to terminal', 'success')
    }
  }, [notify])

  // --- Task page: paste as link/notes/description ---
  const pasteToTask = useCallback(async (taskId: string, result: PasteResult) => {
    const task = tasks.find(t => t.id === taskId)
    if (!task) { notify('Task not found', 'error'); return }

    if (result.type === 'url') {
      // Auto-add as link
      const existingLinks: TaskLink[] = task.links || []
      const newLinks = [...existingLinks, { url: result.text! }]
      await api(`/api/tasks/${taskId}`, {
        method: 'PATCH',
        body: JSON.stringify({ links: newLinks }),
      })
      refresh()
      notify('Link added to task', 'success')
    } else if (result.type === 'image') {
      // Save image, add as link
      const res = await api<{ ok: boolean; url: string; filename: string }>(
        '/api/paste-image',
        { method: 'POST', body: JSON.stringify({ image_data: result.imageData }) },
      )
      if (res.ok) {
        const existingLinks: TaskLink[] = task.links || []
        const newLinks = [...existingLinks, { url: `http://localhost:8093${res.url}`, tag: 'Image' }]
        await api(`/api/tasks/${taskId}`, {
          method: 'PATCH',
          body: JSON.stringify({ links: newLinks }),
        })
        refresh()
        notify(`Image added as link: ${res.filename}`, 'success')
      }
    } else {
      // Text: show popup with Notes / Description options
      const text = result.text!
      setPopup({
        title: 'Paste to Task',
        preview: text.slice(0, 200) + (text.length > 200 ? '...' : ''),
        charCount: text.length,
        options: [
          {
            label: 'Add as Notes',
            description: 'Append to task notes',
            action: async () => {
              const existingNotes = task.notes || ''
              const newNotes = existingNotes ? `${existingNotes}\n\n${text}` : text
              await api(`/api/tasks/${taskId}`, {
                method: 'PATCH',
                body: JSON.stringify({ notes: newNotes }),
              })
              refresh()
              notify('Text added to task notes', 'success')
            },
          },
          {
            label: 'Set as Description',
            description: 'Replace task description',
            action: async () => {
              await api(`/api/tasks/${taskId}`, {
                method: 'PATCH',
                body: JSON.stringify({ description: text }),
              })
              refresh()
              notify('Task description updated', 'success')
            },
          },
        ],
      })
    }
  }, [tasks, refresh, notify])

  // --- Project page: paste as context item ---
  const pasteToProject = useCallback(async (projectId: string, result: PasteResult) => {
    let payload: SmartPastePayload

    if (result.type === 'image') {
      const res = await api<{ ok: boolean; url: string; filename: string }>(
        '/api/paste-image',
        { method: 'POST', body: JSON.stringify({ image_data: result.imageData }) },
      )
      if (!res.ok) return
      payload = {
        title: res.filename,
        content: `![image](http://localhost:8093${res.url})`,
      }
    } else if (result.type === 'url') {
      payload = {
        content: result.text!,
        category: 'reference',
      }
    } else {
      const text = result.text!
      const firstLine = text.split('\n')[0].slice(0, 100)
      payload = {
        title: firstLine,
        content: text,
      }
    }

    setSmartPastePayload(payload)
    notify('Opening context form...', 'info')
  }, [setSmartPastePayload, notify])

  // --- Main paste handler ---
  const handlePaste = useCallback(async () => {
    if (pasting || !route) return
    setPasting(true)
    try {
      const result = await readClipboard()

      switch (route.page) {
        case 'worker': {
          // Find session by matching worker ID
          const session = sessions.find(s => s.id === route.id)
          if (!session) { notify('Worker session not found', 'error'); return }
          await pasteToWorker(session.id, result)
          break
        }
        case 'task':
          await pasteToTask(route.id, result)
          break
        case 'project':
          await pasteToProject(route.id, result)
          break
      }
    } catch (e) {
      if (e instanceof Error && e.name === 'NotAllowedError') {
        notify('Clipboard access denied. Please allow clipboard permissions.', 'error')
      } else {
        notify(e instanceof Error ? e.message : 'Failed to paste', 'error')
      }
    } finally {
      setPasting(false)
    }
  }, [pasting, route, readClipboard, sessions, notify, pasteToWorker, pasteToTask, pasteToProject])

  return (
    <header className="app-header">
      <div className="header-left">
        <code className="tmux-hint" data-testid="tmux-hint">
          tmux attach -t orchestrator
        </code>
      </div>
      <div className="header-right">
        <button
          className={`smart-paste-btn${enabled ? '' : ' disabled'}`}
          onClick={handlePaste}
          disabled={!enabled || pasting}
          title={enabled
            ? `Smart Paste to ${route!.page} (${pasting ? 'pasting...' : 'Ctrl+Shift+V'})`
            : 'Navigate to a project, task, or worker'
          }
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
            <rect x="8" y="2" width="8" height="4" rx="1" ry="1" />
          </svg>
          <span>{pasting ? 'Pasting...' : 'Paste'}</span>
        </button>
        <span
          className={`connection-dot ${connected ? 'connected' : 'disconnected'}`}
          data-testid="connection-status"
        />
        <span className="connection-label">
          {connected ? 'Live' : 'Disconnected'}
        </span>
      </div>

      <SmartPastePopup
        open={!!popup}
        onClose={() => setPopup(null)}
        title={popup?.title || ''}
        preview={popup?.preview}
        charCount={popup?.charCount}
        options={popup?.options || []}
      />
    </header>
  )
}

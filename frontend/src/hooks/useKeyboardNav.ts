import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'

/**
 * Keyboard shortcuts using G+key pattern (like GitHub).
 * G then D = Dashboard, G then P = Projects, G then S = Sessions,
 * G then T = Tasks, G then C = Chat, G then A = Activity
 */
export function useKeyboardNav() {
  const navigate = useNavigate()
  const gPressed = useRef(false)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)

  useEffect(() => {
    function handler(e: KeyboardEvent) {
      // Ignore when typing in inputs
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return

      if (e.key === 'g' || e.key === 'G') {
        gPressed.current = true
        clearTimeout(timer.current)
        timer.current = setTimeout(() => { gPressed.current = false }, 1000)
        return
      }

      if (gPressed.current) {
        gPressed.current = false
        clearTimeout(timer.current)
        const routes: Record<string, string> = {
          d: '/',
          p: '/projects',
          s: '/sessions',
          t: '/tasks',
          c: '/chat',
          a: '/activity',
          e: '/decisions',
        }
        const route = routes[e.key.toLowerCase()]
        if (route) {
          e.preventDefault()
          navigate(route)
        }
      }
    }

    window.addEventListener('keydown', handler)
    return () => {
      window.removeEventListener('keydown', handler)
      clearTimeout(timer.current)
    }
  }, [navigate])
}

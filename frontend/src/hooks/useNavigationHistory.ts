import { useState, useEffect, useRef, useCallback } from 'react'
import { useLocation, useNavigate, useNavigationType } from 'react-router-dom'

/**
 * Tracks in-session navigation history using React Router location keys.
 * Provides browser-style back/forward navigation controls.
 */
export function useNavigationHistory() {
  const location = useLocation()
  const navigate = useNavigate()
  const navigationType = useNavigationType()

  // Use refs for the mutable stack/index to avoid re-render loops
  const stackRef = useRef<string[]>([location.key])
  const indexRef = useRef(0)
  const [, setTick] = useState(0)
  const isInitialMount = useRef(true)

  useEffect(() => {
    // Skip the initial mount — stack is already initialized with current key
    if (isInitialMount.current) {
      isInitialMount.current = false
      return
    }

    const stack = stackRef.current
    const key = location.key

    if (navigationType === 'PUSH') {
      // Truncate forward entries and append
      stackRef.current = [...stack.slice(0, indexRef.current + 1), key]
      indexRef.current = stackRef.current.length - 1
    } else if (navigationType === 'REPLACE') {
      // Swap current entry
      stack[indexRef.current] = key
    } else if (navigationType === 'POP') {
      // Browser back/forward or programmatic navigate(-1)/navigate(1)
      const found = stack.indexOf(key)
      if (found !== -1) {
        indexRef.current = found
      }
      // If key not found, user navigated outside our tracked history — ignore
    }

    setTick(t => t + 1)
  }, [location.key, navigationType])

  const canGoBack = indexRef.current > 0
  const canGoForward = indexRef.current < stackRef.current.length - 1

  const goBack = useCallback(() => {
    if (indexRef.current > 0) {
      navigate(-1)
    }
  }, [navigate])

  const goForward = useCallback(() => {
    if (indexRef.current < stackRef.current.length - 1) {
      navigate(1)
    }
  }, [navigate])

  return { canGoBack, canGoForward, goBack, goForward }
}

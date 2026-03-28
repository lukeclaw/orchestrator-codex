import { createContext, useContext, useCallback, type ReactNode } from 'react'
import { useBrainPanelState } from '../hooks/useBrainPanelState'

interface BrainPanelContextValue {
  collapsed: boolean
  width: number
  toggleCollapsed: () => void
  collapse: () => void
  updateWidth: (w: number) => void
  MIN_WIDTH: number
  MAX_WIDTH: number
}

const BrainPanelContext = createContext<BrainPanelContextValue | null>(null)

export function BrainPanelProvider({ children }: { children: ReactNode }) {
  const state = useBrainPanelState()

  const collapse = useCallback(() => {
    if (!state.collapsed) {
      state.toggleCollapsed()
    }
  }, [state.collapsed, state.toggleCollapsed])

  return (
    <BrainPanelContext.Provider
      value={{
        collapsed: state.collapsed,
        width: state.width,
        toggleCollapsed: state.toggleCollapsed,
        collapse,
        updateWidth: state.updateWidth,
        MIN_WIDTH: state.MIN_WIDTH,
        MAX_WIDTH: state.MAX_WIDTH,
      }}
    >
      {children}
    </BrainPanelContext.Provider>
  )
}

export function useBrainPanel(): BrainPanelContextValue {
  const ctx = useContext(BrainPanelContext)
  if (!ctx) throw new Error('useBrainPanel must be used within BrainPanelProvider')
  return ctx
}

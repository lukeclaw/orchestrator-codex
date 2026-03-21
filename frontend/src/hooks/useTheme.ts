import { useEffect } from 'react'
import { useSettings } from '../context/SettingsContext'

export type ThemeMode = 'dark' | 'light' | 'system'

export function useTheme() {
  const { loading, getValue } = useSettings()
  const mode = (loading ? null : (getValue('ui.theme') as ThemeMode | null)) ?? 'dark'

  useEffect(() => {
    if (!mode) return

    function apply(resolved: 'dark' | 'light') {
      const root = document.documentElement
      root.classList.add('theme-transitioning')
      root.setAttribute('data-theme', resolved)
      localStorage.setItem('theme-mode', mode)
      setTimeout(() => root.classList.remove('theme-transitioning'), 300)
    }

    if (mode === 'system') {
      const mql = window.matchMedia('(prefers-color-scheme: dark)')
      const handler = (e: MediaQueryListEvent | MediaQueryList) =>
        apply(e.matches ? 'dark' : 'light')
      handler(mql)
      mql.addEventListener('change', handler as EventListener)
      return () => mql.removeEventListener('change', handler as EventListener)
    } else {
      apply(mode)
    }
  }, [mode])
}

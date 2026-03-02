import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { openUrl } from './api/client'
import './styles/variables.css'
import './styles/global.css'

// Block the default browser context menu globally.
// Components that need a custom context menu should call e.preventDefault()
// themselves (e.g. FileExplorerPanel) — that fires before this handler.
// Allow the native menu on inputs/textareas so copy/paste still works.
document.addEventListener('contextmenu', (e) => {
  const target = e.target as HTMLElement
  const isXtermTextarea = target.classList.contains('xterm-helper-textarea')
  if (!isXtermTextarea && target.closest('input, textarea, [contenteditable="true"]')) return
  e.preventDefault()
})

// Intercept external link clicks so they open in the system browser
// instead of navigating the Tauri webview away from the app.
// Internal links (same origin, e.g. React Router) are left alone.
// Uses CAPTURE phase so it fires before any component stopPropagation() can block it.
document.addEventListener('click', (e) => {
  const anchor = (e.target as HTMLElement).closest('a[href]') as HTMLAnchorElement | null
  if (!anchor) return
  const href = anchor.href
  if (href && /^https?:\/\//.test(href) && new URL(href).origin !== location.origin) {
    e.preventDefault()
    e.stopPropagation()
    openUrl(href)
  }
}, true)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)

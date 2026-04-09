# Notification Visibility

Real-time toast notifications and optional macOS system notifications when workers create alerts, replacing the 10-second polling delay.

## Problem

When workers create notifications (via `POST /api/notifications`), the only feedback is a badge count update that arrives via polling every 10 seconds. The user has no immediate visual cue that something happened. If the user is in another app, they have no way to know a worker needs attention until they switch back to Orchestrator.

## Solution

### 1. Real-Time In-App Toast

When a notification is created, the backend publishes a `notification.created` event through the existing event bus. The WebSocket wildcard subscriber broadcasts it to all connected frontend clients. The frontend shows an auto-dismissing toast (4 seconds) and immediately updates the badge count.

**Data flow:**
```
POST /api/notifications
  → repo.create_notification()
  → publish(Event("notification.created", data))
  → WebSocket broadcast (via wildcard subscriber in websocket.py)
  → AppContext onmessage handler
  → useNotify() shows toast + refreshNotificationCount()
```

The toast is clickable — clicking it navigates to `/notifications` and dismisses the toast. Only worker-generated notification toasts are clickable (they receive an `onClick` callback). Regular UI feedback toasts (success/error from form submissions) remain passive.

### 2. macOS System Notifications (Optional)

A "System notifications" toggle in Settings > Preferences enables native macOS notifications. Uses `tauri-plugin-notification` in the desktop app and the Web Notifications API as a fallback in browser mode.

**Permission flow:** When the toggle is enabled, `requestNotificationPermission()` is called. If denied, the toggle reverts and an error toast explains how to enable notifications in System Preferences.

**Click behavior:**
- **Browser mode**: `Notification.onclick` focuses the window and navigates to `/notifications`
- **Tauri mode**: macOS brings the app to foreground on click (native behavior). A `window` focus listener detects when the app gains focus within 30 seconds of a system notification and auto-navigates to `/notifications`

**Throttle:** System notifications are throttled to at most one per 5 seconds to prevent notification center flooding.

**Dev mode caveat:** In `cargo tauri dev`, `notify_rust` sets the notification source to `com.apple.Terminal` (hardcoded in `desktop.rs`). In the production `.app` bundle, notifications correctly show as from "Orchestrator" (`com.yudongqiu.orchestrator`).

### 3. Deduplication

React StrictMode and Vite HMR can create multiple WebSocket connections that each receive the same broadcast. Two layers prevent duplicate toasts:

1. **Handler cleanup:** WebSocket `onmessage`, `onclose`, and `onerror` handlers are nullified before `close()` in the effect cleanup, preventing stale connections from processing messages
2. **Module-level dedup:** A `Set<string>` at module scope tracks seen notification IDs for 10 seconds. This survives StrictMode remounts within the same module version

## Setting

| Key | Default | Description |
|-----|---------|-------------|
| `notifications.system` | `false` | Enable macOS system notifications for worker alerts |

## Files

**Backend:**
- `orchestrator/api/routes/notifications.py` — publishes `notification.created` event after creation
- `orchestrator/config_defaults.py` — `notifications.system` default

**Frontend:**
- `frontend/src/context/AppContext.tsx` — WebSocket handler for `notification.created`, dedup, focus listener
- `frontend/src/context/NotificationContext.tsx` — `notify()` accepts optional `onClick`, exposes `dismiss()`
- `frontend/src/components/common/NotificationToast.tsx` — clickable toasts with dismiss-on-click
- `frontend/src/utils/systemNotification.ts` — Tauri/Web notification abstraction with throttle
- `frontend/src/pages/SettingsPage.tsx` — "Notifications" section in Preferences

**Tauri:**
- `src-tauri/Cargo.toml` — `tauri-plugin-notification = "2"`
- `src-tauri/src/lib.rs` — plugin registration
- `src-tauri/capabilities/default.json` — `notification:default` permission

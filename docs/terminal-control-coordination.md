# Terminal Control Coordination - Feature Evaluation

> **Status**: Planning Phase  
> **Created**: Feb 10, 2026  
> **Goal**: Prevent conflicts between user input and background terminal operations

---

## 1. Problem Statement

The orchestrator manages tmux terminals that can be accessed by:
1. **Users** - typing directly in the web terminal (via WebSocket)
2. **Background operations** - automated commands sent by the backend

When both try to use the same terminal simultaneously, conflicts arise:
- Interleaved keystrokes corrupt commands
- Users may unknowingly interrupt setup/reconnect operations
- Automated commands may disrupt manual debugging sessions

---

## 2. Current Architecture Analysis

### 2.1 Terminal Control Entry Points

| Entry Point | File | Operations | Frequency |
|-------------|------|------------|-----------|
| **User WebSocket input** | `ws_terminal.py:141` | `send_keys_async()` | Real-time, every keystroke |
| **Session creation** | `session.py:198-384` | Setup SSH, screen, Claude | Once per worker |
| **Session reconnect** | `reconnect.py:217-383` | Tunnel, SSH, screen reattach | On disconnect |
| **Health check** | `sessions.py:643-754` | `send_keys()` for hostname check | Periodic |
| **Pause/Stop/Continue** | `sessions.py:480-569` | Escape, `/clear`, messages | User-triggered |
| **Send message** | `sessions.py:439-452` | `send_keys_literal()` | User-triggered |

### 2.2 Current Input Flow

```
┌─────────────┐     WebSocket      ┌─────────────┐     tmux control     ┌─────────────┐
│   Browser   │ ─────────────────▶ │   Backend   │ ──────────────────▶  │    tmux     │
│  (xterm.js) │                    │ ws_terminal │                      │   session   │
└─────────────┘                    └─────────────┘                      └─────────────┘
                                          ▲
                                          │ send_keys()
                                   ┌──────┴──────┐
                                   │  Background │
                                   │  Operations │
                                   └─────────────┘
```

**Problem**: No coordination between user input path and background operations.

---

## 3. Feature Requirements

### Feature 1: User-First Priority
> If the user started typing first, pause all background processes that might affect that terminal.

### Feature 2: Background-First Lock
> If a background job started first, show animation + temporarily block user input.

### Feature 3: Disconnection Handling
> If WebSocket disconnects, display status, block input, auto-reconnect every 10s.

---

## 4. Edge Cases & Race Conditions

### 4.1 Feature 1 - User-First Priority

| Edge Case | Description | Risk | Mitigation |
|-----------|-------------|------|------------|
| **Rapid typing** | User types, pauses <1s, background starts | Medium | Grace period (5-10s idle threshold) |
| **Mid-command interrupt** | Background command half-sent when user types | High | Atomic command batching, rollback |
| **Stale activity flag** | User leaves tab, activity stuck as "active" | Medium | Heartbeat / focus-based tracking |
| **Multi-terminal user** | User has 2 terminals open, types in one | Low | Per-terminal activity tracking |
| **Reconnect during typing** | User typing, tunnel dies, reconnect needed | High | Queue reconnect, show warning |

**Race Condition Analysis**:
```
Timeline A (Safe):
  User types ──────────────────▶
                    Background checks, sees activity, waits
                    
Timeline B (Conflict):  
  Background starts ────▶
       User types ─────────────▶   ← Commands interleaved!
```

**Critical Question**: What is "started typing first"?
- Option A: Any keystroke in last N seconds → simple but may block critical reconnects
- Option B: Currently in mid-line (no Enter sent) → complex to track
- Option C: Terminal is focused + recent activity → requires focus tracking

### 4.2 Feature 2 - Background-First Lock

| Edge Case | Description | Risk | Mitigation |
|-----------|-------------|------|------------|
| **Lock starvation** | Background job hangs, user locked out forever | Critical | Timeout + manual unlock button |
| **Queued keystrokes** | User types during lock, keys buffered | Medium | Discard or queue with warning |
| **Partial visibility** | User sees garbled output during setup | Low | Show progress overlay |
| **Nested locks** | Health check during reconnect | Medium | Lock priority / single lock holder |
| **Lock released early** | Background exits without releasing | Medium | Heartbeat / timeout auto-release |

**State Transitions**:
```
                    ┌─────────────────┐
                    │     UNLOCKED    │
                    └────────┬────────┘
          background_start() │ ▲ background_finish()
                             ▼ │
                    ┌─────────────────┐
                    │     LOCKED      │ ◄── timeout after 60s → UNLOCKED
                    └────────┬────────┘
              user_types()   │
                             ▼
                    ┌─────────────────┐
                    │  LOCKED_QUEUED  │ ← Show "input will be sent after..."
                    └─────────────────┘
```

### 4.3 Feature 3 - Disconnection Handling

| Edge Case | Description | Risk | Mitigation |
|-----------|-------------|------|------------|
| **Server restart** | Backend restarts, all WS connections drop | High | Reconnect with exponential backoff |
| **Network blip** | Brief disconnect (<2s) | Low | Immediate reconnect attempt |
| **Tab backgrounded** | Browser suspends WS | Medium | Visibility API to detect |
| **Multiple reconnects** | 10s timer fires multiple times | Low | Single active reconnect attempt |
| **Permanent failure** | Server down for extended period | Medium | Max retries (10), then give up UI |
| **Session deleted** | Server deleted session during disconnect | Medium | Handle 404, show "session ended" |
| **Content desync** | Missed output during disconnect | High | Request full history on reconnect |

**Reconnection Strategy Options**:

| Strategy | Pros | Cons |
|----------|------|------|
| **Fixed interval (10s)** | Simple, predictable | May be slow for quick blips |
| **Exponential backoff** | Efficient, reduces server load | Slower recovery after long outage |
| **Immediate + backoff** | Fast for blips, safe for outages | More complex |
| **Visibility-aware** | Battery efficient | May delay reconnect |

**Recommended**: Immediate retry → 1s → 2s → 5s → 10s (capped) with max 10 attempts.

---

## 5. State Machine Design

### 5.1 Terminal Control States

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         TERMINAL CONTROL STATES                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────┐                              ┌──────────────┐         │
│  │   CONNECTED  │ ◄────── ws.open ─────────────│ DISCONNECTED │         │
│  │   UNLOCKED   │                              │              │         │
│  └──────┬───────┘                              └──────┬───────┘         │
│         │                                             │                  │
│         │ bg_start()              ws.close ───────────┘                  │
│         ▼                                   ▲                            │
│  ┌──────────────┐                           │                            │
│  │   CONNECTED  │ ───── bg_finish() ────────┤                            │
│  │    LOCKED    │                           │                            │
│  └──────────────┘                           │                            │
│         │                                   │                            │
│         │ user_types()                      │                            │
│         ▼                                   │                            │
│  ┌──────────────┐                           │                            │
│  │   CONNECTED  │ ───── bg_finish() ────────┘                            │
│  │ LOCK_PENDING │  (input queued, sent after unlock)                     │
│  └──────────────┘                                                        │
│                                                                          │
│  ┌──────────────┐                                                        │
│  │ USER_ACTIVE  │ ◄── typing detected, bg operations pause               │
│  │   UNLOCKED   │                                                        │
│  └──────────────┘                                                        │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.2 Combined State (Frontend)

```typescript
interface TerminalControlState {
  // WebSocket connection
  wsState: 'connected' | 'connecting' | 'disconnected';
  reconnectAttempt: number;
  reconnectCountdown: number | null;  // seconds until next attempt
  
  // Lock state (background operations)
  lockState: 'unlocked' | 'locked' | 'lock_pending';
  lockReason: string | null;  // "Setting up worker...", "Reconnecting..."
  lockStartTime: number | null;
  
  // User activity tracking
  userActive: boolean;
  lastUserInput: number | null;  // timestamp
  
  // Input queue (when locked)
  pendingInput: string[];
}
```

---

## 6. API Contract (Minimal)

**No new APIs needed.** Use existing infrastructure:

| Need | Existing Solution |
|------|-------------------|
| Lock state | Session `status` field (`connecting` = locked) |
| Connection state | WebSocket `readyState` |
| User activity | Frontend-only `lastUserInput` timestamp |

One small addition for Feature 1:
```
GET /api/sessions/{id}/user-active?since=3
Response: { active: boolean }
```
Backend checks if WebSocket received input in last N seconds.

---

## 7. Implementation Approach (Simplified)

**Frontend-only** - minimal backend changes, fast to implement.

| Aspect | Implementation |
|--------|----------------|
| **Lock detection** | Infer from session status (`connecting` = locked) |
| **Input blocking** | `if (isLocked || !isConnected) return` in `terminal.onData()` |
| **Reconnect** | Frontend WebSocket reconnect with simple backoff |
| **User activity** | Track `lastUserInput`, backend checks before ops |

No new WebSocket message types needed. Frontend polls session status which already exists.

---

## 8. UX Design Considerations

### 8.1 Visual Feedback

| State | Border | Overlay | Input |
|-------|--------|---------|-------|
| Connected, Unlocked | Blue when focused | None | Enabled |
| Connected, Locked | Animated gradient | Semi-transparent + status | Blocked + toast |
| Disconnected | Red pulse | "Connection lost" + countdown | Blocked |
| Reconnecting | Yellow pulse | "Reconnecting..." | Blocked |

### 8.2 Animation Specifications

**Locked State Border**:
```css
@keyframes border-pulse-locked {
  0%, 100% { border-color: rgba(217, 146, 34, 0.6); }  /* yellow */
  50% { border-color: rgba(217, 146, 34, 1.0); }
}
```

**Disconnected State Border**:
```css
@keyframes border-pulse-disconnected {
  0%, 100% { border-color: rgba(255, 123, 114, 0.4); }  /* red */
  50% { border-color: rgba(255, 123, 114, 0.8); }
}
```

### 8.3 User Messaging

| Event | Toast/Message | Duration |
|-------|---------------|----------|
| Lock acquired | "Terminal busy: [reason]" | Until unlock |
| Lock released | "Terminal ready" | 2s |
| Disconnected | "Connection lost. Reconnecting in Xs..." | Until reconnect |
| Reconnect failed | "Reconnect failed. Retrying..." | 3s |
| Max retries | "Unable to connect. [Retry] [Close]" | Persistent |
| Input blocked | Flash border red | 200ms |

---

## 9. Testing Strategy

### 9.1 Unit Tests

| Test Case | Description |
|-----------|-------------|
| `test_lock_blocks_input` | Input rejected when locked |
| `test_unlock_releases_queue` | Queued input sent after unlock |
| `test_activity_detection` | User activity correctly tracked |
| `test_reconnect_backoff` | Exponential backoff timing correct |
| `test_max_retries` | Stops after N attempts |

### 9.2 Integration Tests

| Test Case | Description |
|-----------|-------------|
| `test_reconnect_preserves_history` | Full history restored after reconnect |
| `test_lock_during_user_typing` | Lock acquired, user input queued |
| `test_concurrent_lock_requests` | Only one lock holder at a time |
| `test_server_restart_recovery` | Client reconnects after server restart |

### 9.3 Manual Test Scenarios

1. **Rapid disconnect/reconnect**: Kill backend, restart, verify smooth recovery
2. **Type during setup**: Create worker, immediately type, verify no corruption
3. **Long setup**: Slow network, verify lock doesn't timeout prematurely
4. **Tab switch**: Background tab, verify reconnect on return

---

## 10. Implementation Plan

### Phase 1: WebSocket Reconnection (Feature 3)
**Effort**: 2-3 hours

1. Add reconnection state to `TerminalView.tsx`
2. Implement exponential backoff reconnect logic
3. Add disconnected overlay UI
4. Handle `ws.onclose` and `ws.onerror`
5. Request history on reconnect

### Phase 2: Background Lock UI (Feature 2)
**Effort**: 3-4 hours

1. Add `lock`/`unlock` message handling in frontend
2. Backend sends lock before background operations
3. Add animated border CSS
4. Block input during lock
5. Show lock reason overlay

### Phase 3: User Activity Respect (Feature 1)
**Effort**: 4-6 hours

1. Track `lastUserInput` in frontend
2. Add `activity_query`/`activity_report` WebSocket messages
3. Backend checks activity before background ops
4. Add grace period logic (5s idle = safe to proceed)
5. Queue background ops if user active

---

## 11. Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Lock timeout too short | User locked out during slow ops | Medium | Configurable timeout, heartbeat extension |
| Lock timeout too long | User waits unnecessarily | Low | Progress updates, manual unlock |
| Reconnect storm | Many clients reconnect simultaneously | Medium | Jittered backoff (random offset) |
| State desync | Frontend/backend lock state mismatch | Medium | Periodic sync message, timeout fallback |
| Browser memory leak | Many reconnect attempts accumulate | Low | Clean up timers on unmount |

---

## 12. Decisions (Keep It Simple)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Input during lock** | **Discard** | No queue complexity, user sees blocked state |
| **Activity threshold** | **3 seconds** | Short enough to feel responsive |
| **Max reconnect attempts** | **5 attempts** | ~30s total, then show retry button |
| **Lock timeout** | **60 seconds** | Auto-release, no heartbeat needed |
| **Manual unlock** | **No** | Lock auto-releases, keeps UI simple |

---

## 13. Next Steps

1. [ ] Review this document and confirm decisions
2. [ ] Implement Phase 1 (WebSocket reconnection)
3. [ ] Test Phase 1 thoroughly
4. [ ] Implement Phase 2 (Background lock)
5. [ ] Implement Phase 3 (User activity)
6. [ ] End-to-end testing
7. [ ] Documentation update

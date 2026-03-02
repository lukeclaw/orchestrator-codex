---
title: "orch-browser — Worker CLI for Browser Lifecycle Management"
author: Yudong Qiu
created: 2026-03-01
last_modified: 2026-03-01
status: Implemented
---

# orch-browser — Worker CLI for Browser Lifecycle Management

## 1. Problem

Today, workers must manually launch a Chromium browser with `--remote-debugging-port=9222` before the operator can use the Browser View feature. This has several issues:

1. **No unified command**: Workers use ad-hoc Playwright/Chromium invocations with no standard pattern.
2. **No install detection**: If Playwright browsers aren't installed on the rdev, the worker gets a cryptic error instead of actionable guidance.
3. **No UI integration**: The worker launches a browser, but the operator must separately click "View Browser" in the dashboard. There's no way for the worker to trigger the browser view overlay.
4. **Playwright MCP can't reuse the browser**: The Playwright MCP server plugin (`@playwright/mcp`) launches its own browser by default. When the worker launches a separate browser via CLI, the MCP tools operate on a different instance — two browsers instead of one.

## 2. Goals

- Single `orch-browser` CLI command that workers use to launch, connect to, and close browsers.
- Auto-detect whether Playwright browsers are installed; provide install instructions if not.
- Trigger the browser view overlay on the operator's dashboard automatically.
- Ensure the Playwright MCP server connects to the **same** browser instance the operator sees.

## 3. Playwright MCP — Connecting to an Existing Browser

### 3.1 The Core Question

> "If we trigger the browser with the CLI, will the Playwright MCP on the worker be able to connect to this browser?"

**Yes — tested and confirmed.** The `@playwright/mcp` server (which underpins the Claude Code Playwright plugin) supports connecting to an existing browser via CDP:

| Mechanism | Value |
|---|---|
| CLI argument | `--cdp-endpoint <url>` |
| Environment variable | `PLAYWRIGHT_MCP_CDP_ENDPOINT` |
| Config JSON | `{ "browser": { "cdpEndpoint": "ws://..." } }` |

When given a CDP endpoint, the MCP server calls `chromium.connectOverCDP(endpointURL)` instead of `chromium.launch()`. This means:

- It reuses the existing browser process (same tabs, cookies, auth state).
- CDP screencast (our Browser View) and Playwright MCP operate on the **same** browser simultaneously — CDP screencast is read-only observation, while Playwright drives actions.
- The operator sees exactly what Playwright is doing in real time.

#### Verified by testing

The Claude Code Playwright plugin (`.mcp.json`) is simply:
```json
{
  "playwright": {
    "command": "npx",
    "args": ["@playwright/mcp@latest"]
  }
}
```

This is a thin wrapper around the standard `@playwright/mcp` package from Microsoft. All `@playwright/mcp` CLI arguments — including `--cdp-endpoint` — work directly by appending to the `args` array.

### 3.2 How connectOverCDP Works

Playwright's `chromium.connectOverCDP()` connects via the Chrome DevTools Protocol endpoint URL. It accepts either:
- **HTTP URL**: `http://localhost:9222/` — Playwright queries `/json/version` to discover the WebSocket endpoint.
- **WebSocket URL**: `ws://127.0.0.1:9222/devtools/browser/<id>` — direct connection.

Key properties:
- Lower fidelity than Playwright's native protocol, but sufficient for navigation, clicks, typing, screenshots.
- Only works with Chromium-based browsers (Chrome, Chromium, Edge).
- Playwright gets access to the default browser context and all existing pages.
- Multiple CDP clients can connect simultaneously (our CDP proxy + Playwright MCP).

### 3.3 Recommended Configuration Approach

Set the `PLAYWRIGHT_MCP_CDP_ENDPOINT` environment variable in the worker's Claude Code configuration when a browser is active:

```
PLAYWRIGHT_MCP_CDP_ENDPOINT=http://localhost:9222
```

**Two approaches for setting this at the right time:**

**Option A — Static config (simpler, recommended)**

Always set the env var to `http://localhost:9222` in the worker's MCP config. If no browser is running, the MCP server will fail to connect and the worker gets a clear error — at which point it calls `orch-browser open` to launch one. This "fail-then-launch" pattern is simple and predictable.

```json
// .mcp.json deployed to worker
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["-y", "@anthropic-ai/claude-code-mcp-server-playwright"],
      "env": {
        "PLAYWRIGHT_MCP_CDP_ENDPOINT": "http://localhost:9222"
      }
    }
  }
}
```

> **Note:** The exact package name for the Playwright MCP server may differ (e.g., `@playwright/mcp`). This should be verified against the actual Claude Code plugin registry. If the built-in `plugin-playwright-playwright` plugin is used instead, the configuration mechanism may be through the plugin's own settings rather than `.mcp.json`.

**Option B — Dynamic env var (if Claude Code supports runtime MCP reconfiguration)**

The `orch-browser open` command sets the env var and restarts the MCP server. This avoids the initial connection failure but requires MCP hot-reload support, which Claude Code may not currently offer.

**Recommendation: Option A.** The static config is simpler, requires no runtime reconfiguration, and the "fail → launch → retry" flow is natural for an agent.

## 4. `orch-browser` CLI Design

### 4.1 Commands

```
orch-browser open [--port PORT]    Launch browser, start tunnel, open browser view
orch-browser close                 Close browser view and browser
orch-browser status                Check browser and browser view status
orch-browser install               Install Playwright browsers (chromium)
orch-browser -h, --help            Show help
```

### 4.2 `orch-browser open` Flow

```
┌─────────────────────────────────────────────────────┐
│ Worker calls: orch-browser open                      │
└─────────────────┬───────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────┐
│ 1. Check if Chromium is installed                    │
│    - Run: npx playwright chromium --version          │
│      (or check ~/.cache/ms-playwright/)              │
│    - If not installed → print install instructions   │
│      and exit 1                                      │
└─────────────────┬───────────────────────────────────┘
                  │ Installed
                  ▼
┌─────────────────────────────────────────────────────┐
│ 2. Check if browser already running on :PORT         │
│    - curl http://localhost:PORT/json/version         │
│    - If yes → skip launch, go to step 4             │
└─────────────────┬───────────────────────────────────┘
                  │ Not running
                  ▼
┌─────────────────────────────────────────────────────┐
│ 3. Launch Chromium with CDP enabled                  │
│    - npx playwright launch chromium                  │
│      --remote-debugging-port=PORT                    │
│    - Or: node -e "require('playwright').chromium     │
│      .launch({args:['--remote-debugging-port=PORT'], │
│               headless: false})"                     │
│    - Background the process, save PID                │
│    - Wait for CDP endpoint to become available       │
└─────────────────┬───────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────┐
│ 4. Trigger browser view on dashboard                 │
│    - POST /api/sessions/$SESSION_ID/browser-view     │
│      { "cdp_port": PORT }                            │
│    - This creates the SSH tunnel + CDP proxy +       │
│      broadcasts browser_view_started to UI           │
└─────────────────┬───────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────┐
│ 5. Output JSON result                                │
│    { "ok": true,                                     │
│      "cdp_port": 9222,                               │
│      "page_url": "about:blank",                      │
│      "browser_view": true }                          │
│                                                      │
│    Worker + Playwright MCP can now use the browser.  │
│    Operator sees it in the dashboard overlay.         │
└─────────────────────────────────────────────────────┘
```

### 4.3 `orch-browser close` Flow

1. Call `DELETE /api/sessions/$SESSION_ID/browser-view` (stops CDP proxy + tunnel).
2. Kill the browser process (if PID was saved and still running).
3. Output `{ "ok": true }`.

### 4.4 `orch-browser status`

1. Check if browser process is running (saved PID or scan for `--remote-debugging-port`).
2. Call `GET /api/sessions/$SESSION_ID/browser-view` for dashboard view status.
3. Output combined status:
   ```json
   {
     "browser_running": true,
     "cdp_port": 9222,
     "browser_view_active": true,
     "page_url": "https://sso.example.com/login",
     "page_title": "Sign In"
   }
   ```

### 4.5 `orch-browser install`

1. Run `npx playwright install chromium`.
2. Stream output so the worker sees installation progress.
3. Output success/failure JSON.

### 4.6 Install Detection

Before launching, check for Playwright's browser binaries:

```bash
# Check if chromium is installed via Playwright
if ! npx playwright chromium --version &>/dev/null; then
    echo "Error: Playwright Chromium is not installed." >&2
    echo "" >&2
    echo "To install, run:" >&2
    echo "  orch-browser install" >&2
    echo "" >&2
    echo "Or manually:" >&2
    echo "  npx playwright install chromium" >&2
    exit 1
fi
```

## 5. Browser Launch Strategy

### 5.1 Headless vs Headed

On rdev machines (remote Linux), there is typically no display server. The browser must run **headless** but still expose CDP:

```javascript
const browser = await chromium.launch({
  headless: true,
  args: ['--remote-debugging-port=9222'],
});
```

CDP screencast works in headless mode — it renders pages to an off-screen buffer and captures frames. This is the same mode Playwright uses for testing.

### 5.2 Launch Script

The actual launch uses a small Node.js script that Playwright can execute:

```bash
# Launch browser in background using Playwright's bundled Chromium
nohup npx playwright launch --browser chromium \
    --remote-debugging-port="${PORT}" \
    > /tmp/orchestrator/browser.log 2>&1 &
BROWSER_PID=$!
```

If `npx playwright launch` is not available as a CLI command, fall back to:

```bash
nohup node -e "
  const { chromium } = require('playwright');
  (async () => {
    const browser = await chromium.launch({
      headless: true,
      args: ['--remote-debugging-port=${PORT}']
    });
    // Keep process alive
    await new Promise(() => {});
  })();
" > /tmp/orchestrator/browser.log 2>&1 &
BROWSER_PID=$!
```

### 5.3 PID Management

Store the browser PID in `/tmp/orchestrator/workers/{name}/browser.pid` so `orch-browser close` and `orch-browser status` can find it. Also check `/tmp/orchestrator/workers/{name}/browser.log` for launch errors.

## 6. Simultaneous CDP Access — Browser View + Playwright MCP

A common concern: can CDP screencast and Playwright MCP operate on the same browser simultaneously?

**Yes — tested and confirmed.** Chrome's CDP supports multiple simultaneous clients. Each client gets its own CDP session. Specifically:

- **Browser View (our CDP proxy)**: Connects to a page target, runs `Page.startScreencast` for frame streaming, and dispatches input events via `Input.dispatch*`.
- **Playwright MCP (via connectOverCDP)**: Connects to the browser-level CDP endpoint, gets access to all pages, and drives actions via Playwright's higher-level API.

These two connections are independent and non-interfering:
- Screencast frames are delivered to our CDP proxy client only.
- Playwright commands go through Playwright's own CDP session.
- Both see the same page state — if Playwright navigates, our screencast shows the new page; if the operator clicks via Browser View, Playwright's page state reflects the click.

### Test results

Launched Chromium with `--remote-debugging-port=9333`, then simultaneously:
1. Connected `chromium.connectOverCDP('http://localhost:9333')` — success, got access to existing page.
2. Opened a CDP session on the same page and ran `Page.startScreencast` — received JPEG frames.
3. Used Playwright to `page.goto('https://example.com')` while screencast was running — screencast captured frames of the new page.
4. Disconnected both clients — browser survived (still responding on `/json/version`).

```
[CDPProxy] Screencast frame #1, data size: 12484 chars
[Playwright] Navigating to https://example.com...
[CDPProxy] Screencast frame #2, data size: 12484 chars  ← captured during navigation
[Playwright] h1 text: Example Domain
[Browser] Still alive: Chrome/145.0.7632.6
```

The only contention scenario is **simultaneous input**: if both the operator (via Browser View) and Playwright MCP send clicks at the same moment, the browser processes both. This is a feature, not a bug — the operator can assist Playwright by clicking auth buttons while Playwright handles the rest.

## 7. Configuration Changes

### 7.1 Worker Settings

Add `orch-browser` to allowed bash permissions:

```json
// agents/worker/settings.json
{
  "permissions": {
    "allow": [
      "Bash(orch-browser *)",
      // ... existing permissions
    ]
  }
}
```

### 7.2 Deploy Script

Add `"orch-browser"` to `WORKER_SCRIPT_NAMES` in `orchestrator/agents/deploy.py`:

```python
WORKER_SCRIPT_NAMES = [
    "orch-task",
    "orch-subtask",
    "orch-worker",
    "orch-context",
    "orch-notify",
    "orch-tunnel",
    "orch-prs",
    "orch-interactive",
    "orch-browser",  # NEW
]
```

### 7.3 Worker Prompt Update

Replace the current "Browser — Remote Debugging" section in `agents/worker/prompt.md`:

```markdown
### `orch-browser` — Browser Management
Launch and manage a browser for web tasks. The browser is visible to the operator
in the dashboard and shared with Playwright MCP tools.
\```bash
orch-browser open              # Launch browser + open dashboard view
orch-browser open --port 9222  # Specify CDP port (default 9222)
orch-browser close             # Close browser + dashboard view
orch-browser status            # Check if browser is running
orch-browser install           # Install Playwright Chromium if missing
\```

When using Playwright MCP tools (browser_navigate, browser_click, etc.),
they automatically connect to the browser launched by `orch-browser open`.
No need to launch a separate browser.
```

### 7.4 Playwright MCP Configuration (Future)

If/when we configure the Playwright MCP plugin for workers, deploy a `.mcp.json` alongside the worker settings:

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["-y", "@playwright/mcp@latest", "--cdp-endpoint", "http://localhost:9222"],
      "env": {}
    }
  }
}
```

This tells the Playwright MCP server to connect to the browser on port 9222 instead of launching its own. The `--cdp-endpoint` argument is the key.

**Alternative using environment variable:**
```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["-y", "@playwright/mcp@latest"],
      "env": {
        "PLAYWRIGHT_MCP_CDP_ENDPOINT": "http://localhost:9222"
      }
    }
  }
}
```

**Note:** The exact integration depends on which Playwright MCP package the worker uses. If using Claude Code's built-in `plugin-playwright-playwright` plugin, the configuration mechanism may differ (plugin config vs `.mcp.json`). The `--cdp-endpoint` / `PLAYWRIGHT_MCP_CDP_ENDPOINT` approach works with the standard `@playwright/mcp` package from Microsoft.

## 8. Files to Create/Modify

| File | Action | Description |
|---|---|---|
| `agents/worker/bin/orch-browser` | **Create** | New CLI script (bash, ~120 lines) |
| `agents/worker/settings.json` | Modify | Add `Bash(orch-browser *)` permission |
| `agents/worker/prompt.md` | Modify | Replace browser section with `orch-browser` docs |
| `orchestrator/agents/deploy.py` | Modify | Add `"orch-browser"` to `WORKER_SCRIPT_NAMES` |
| `tests/unit/test_deploy.py` | Modify | Update script name assertions if applicable |

## 9. Error Handling

| Scenario | Behavior |
|---|---|
| Chromium not installed | Exit 1 with install instructions (`orch-browser install` or `npx playwright install chromium`) |
| Browser already running on port | Skip launch, reuse existing browser, still trigger browser view |
| CDP port occupied by non-browser process | Error: "Port 9222 is in use but no CDP endpoint found" |
| Browser view already active | API returns 409 → print "Browser view already active" |
| Browser launch fails | Show last 10 lines of browser.log, exit 1 |
| Network/API unreachable | Standard lib.sh curl error handling |
| SSH tunnel to rdev fails | API returns 502 → surface tunnel error |

## 10. Sequence Diagram — Full Workflow

```
Worker                  orch-browser          Backend API           Dashboard
  │                         │                     │                    │
  │ orch-browser open       │                     │                    │
  │────────────────────────>│                     │                    │
  │                         │                     │                    │
  │                         │ Check Chromium      │                    │
  │                         │ installed?          │                    │
  │                         │                     │                    │
  │                         │ Launch Chromium     │                    │
  │                         │ --remote-debugging  │                    │
  │                         │ -port=9222          │                    │
  │                         │                     │                    │
  │                         │ POST /browser-view  │                    │
  │                         │────────────────────>│                    │
  │                         │                     │ Create tunnel      │
  │                         │                     │ Connect CDP        │
  │                         │                     │ Start screencast   │
  │                         │                     │                    │
  │                         │                     │ Broadcast event    │
  │                         │                     │───────────────────>│
  │                         │                     │                    │ Show BrowserView
  │                         │     { ok: true }    │                    │ overlay
  │                         │<────────────────────│                    │
  │   { ok, cdp_port, ... } │                     │                    │
  │<────────────────────────│                     │                    │
  │                         │                     │                    │
  │ (Playwright MCP tools   │                     │                    │
  │  now connect to same    │                     │  Screencast frames │
  │  browser via CDP)       │                     │───────────────────>│
  │                         │                     │                    │ Render on canvas
```

## 11. Resolved Questions

1. **Built-in plugin vs npm package** — **Resolved.** The Claude Code Playwright plugin is a thin wrapper that runs `npx @playwright/mcp@latest`. The `--cdp-endpoint` flag works by appending to the args array. For workers, we configure the MCP server with `args: ["@playwright/mcp@latest", "--cdp-endpoint", "http://localhost:9222"]` instead of the default plugin config.

2. **MCP startup ordering** — **Resolved.** Worker prompt instructs: always call `orch-browser open` before using Playwright MCP tools. The Playwright MCP server is configured with `--cdp-endpoint` and launched lazily (on first tool use), so as long as the browser is running by the time the worker calls a Playwright tool, the connection succeeds. If the browser isn't running, the MCP tool fails with a clear error and the worker knows to call `orch-browser open`.

3. **Playwright browser installation on rdev** — **Resolved.** The worker prompt instructs: if Playwright browsers are not installed, run `orch-browser install`. The `orch-browser open` command also auto-detects missing installations and prints instructions.

## 12. Remaining Considerations

1. **Multiple browser instances**: The current design supports only one browser per session. This matches the one-browser-view-per-session constraint already in place.

2. **Node.js availability on rdev**: The `@playwright/mcp` package requires Node.js 18+. On rdev, this is typically available. If not, the worker should install it or the deploy process should verify its presence.

## 13. Summary

The `orch-browser` command gives workers a single entry point for browser management:
- **Launch**: Detects installation, launches Chromium with CDP, triggers dashboard overlay.
- **Share**: Playwright MCP connects to the same browser via `--cdp-endpoint http://localhost:9222`, so the operator's Browser View and Playwright's automation see the same browser.
- **Close**: Cleans up the browser process, CDP proxy, tunnel, and dashboard overlay.

The key insight is that CDP supports multiple simultaneous clients, so our screencast-based Browser View and Playwright's CDP-based automation coexist without conflict. The `--cdp-endpoint` / `PLAYWRIGHT_MCP_CDP_ENDPOINT` configuration ensures Playwright MCP reuses the existing browser rather than launching a second one.

---
title: "On-Demand SSH Port Forwarding for rdev Workers"
author: Yudong Qiu
created: 2026-02-11
last_modified: 2026-02-11
status: Proposed
---

# On-Demand SSH Port Forwarding for rdev Workers

## Overview

Allow rdev workers to request SSH port forwarding from the local machine to the remote rdev host. This enables debugging scenarios where the worker opens a port (e.g., a dev server on port 4200) and needs local access.

## User Flow

1. Worker on rdev starts a dev server: `npm run dev` → listening on `:4200`
2. Worker calls: `orch-tunnel 4200`
3. CLI sends API request through reverse tunnel to local orchestrator server
4. Server spawns background SSH process: `ssh -N -L 4200:localhost:4200 <rdev_host>`
5. Local port 4200 now forwards to rdev's port 4200
6. Worker card in UI shows active tunnel ports in footer

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  rdev (remote)                                                       │
│  ┌──────────────────┐      ┌──────────────────────────────────────┐ │
│  │ Claude Worker    │      │ Dev Server (e.g. vite on :4200)      │ │
│  │ calls orch-tunnel│      │                                      │ │
│  └────────┬─────────┘      └──────────────────┬───────────────────┘ │
│           │                                   │                     │
│           │ (1) HTTP POST via reverse tunnel  │ (4) traffic        │
│           ▼                                   │                     │
└───────────┼───────────────────────────────────┼─────────────────────┘
            │                                   │
            │                                   │ SSH -L 4200:localhost:4200
            ▼                                   ▼
┌───────────────────────────────────────────────────────────────────────┐
│  Local Machine                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐ │
│  │ Orchestrator Server (:8093)                                      │ │
│  │   - POST /api/sessions/{id}/tunnel                               │ │
│  │   - Spawns: ssh -N -L {port}:localhost:{port} {rdev_host}        │ │
│  │   - Tracks active tunnels in-memory (verified via ps)            │ │
│  └──────────────────────────────────────────────────────────────────┘ │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐ │
│  │ Browser localhost:4200 → forwards to rdev:4200                   │ │
│  └──────────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────┘
```

## Components

### 1. CLI Script: `orch-tunnel`

**Location:** `agents/worker/bin/orch-tunnel`

```bash
#!/bin/bash
# orch-tunnel: Request port forwarding from local machine to this rdev
source "$(dirname "$0")/lib.sh"

PORT="$1"
if [[ -z "$PORT" ]]; then
    echo "Usage: orch-tunnel <port> [--close]"
    echo ""
    echo "Request SSH port forwarding from the user's local machine to this rdev."
    echo "After running, localhost:<port> on user's machine forwards to this rdev's <port>."
    echo ""
    echo "Examples:"
    echo "  orch-tunnel 4200         # Forward local:4200 -> rdev:4200"
    echo "  orch-tunnel 4200 --close # Close the tunnel"
    exit 1
fi

if [[ "$2" == "--close" ]]; then
    result=$(curl -s -X DELETE "$API_BASE/api/sessions/$SESSION_ID/tunnel/$PORT")
    echo "$result" | jq .
else
    result=$(curl -s -X POST "$API_BASE/api/sessions/$SESSION_ID/tunnel" \
        -H 'Content-Type: application/json' \
        -d "{\"port\": $PORT}")
    echo "$result" | jq .
    echo ""
    echo "Tunnel created. User can now access localhost:$PORT to reach this rdev's port $PORT."
fi
```

**Permission:** Add `Bash(orch-tunnel *)` to worker `settings.json`

### 2. API Endpoints

**Location:** `orchestrator/api/routes/sessions.py`

#### Tunnel Discovery via Process Scanning (Source of Truth)

Since SSH tunnel processes survive server restart, we **scan running processes** to discover active tunnels rather than relying solely on in-memory state.

```python
import re
import signal
import subprocess
from typing import Dict

# In-memory cache (rebuilt from process scan)
_tunnel_cache: Dict[str, Dict[int, dict]] = {}
_cache_timestamp: float = 0
CACHE_TTL = 5.0  # seconds

def discover_active_tunnels() -> Dict[int, dict]:
    """Scan for active SSH port-forward tunnels via ps.
    
    Returns:
        {local_port: {"pid": int, "remote_port": int, "host": str}}
    """
    tunnels = {}
    try:
        # Find all SSH processes with -L (local port forward)
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        # Pattern: ssh -N -L <local_port>:localhost:<remote_port> <host>
        # Example: ssh -N -L 4200:localhost:4200 user/rdev-vm
        pattern = re.compile(r'ssh\s+-N\s+-L\s+(\d+):localhost:(\d+)\s+(\S+)')
        
        for line in result.stdout.split('\n'):
            if 'ssh' not in line or '-L' not in line:
                continue
            
            match = pattern.search(line)
            if match:
                local_port = int(match.group(1))
                remote_port = int(match.group(2))
                host = match.group(3)
                
                # Extract PID (second column in ps aux)
                parts = line.split()
                if len(parts) > 1:
                    try:
                        pid = int(parts[1])
                        tunnels[local_port] = {
                            "pid": pid,
                            "remote_port": remote_port,
                            "host": host,
                        }
                        logger.debug("Discovered tunnel: local:%d -> %s:%d (pid=%d)", 
                                    local_port, host, remote_port, pid)
                    except ValueError:
                        pass
    except Exception as e:
        logger.warning("Tunnel discovery failed: %s", e)
    
    return tunnels

def get_tunnels_for_session(session_id: str, host: str) -> Dict[int, dict]:
    """Get tunnels for a specific session (filtered by host)."""
    global _tunnel_cache, _cache_timestamp
    
    now = time.time()
    if now - _cache_timestamp > CACHE_TTL:
        _tunnel_cache = {"__all__": discover_active_tunnels()}
        _cache_timestamp = now
    
    all_tunnels = _tunnel_cache.get("__all__", {})
    
    # Filter tunnels that match this session's host
    return {
        port: info for port, info in all_tunnels.items()
        if info["host"] == host
    }

def _is_process_alive(pid: int) -> bool:
    """Check if a process is still running."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
```

#### Endpoints

```python
class TunnelRequest(BaseModel):
    port: int
    local_port: int | None = None  # Optional: use different local port

@router.post("/sessions/{session_id}/tunnel")
def create_tunnel(session_id: str, body: TunnelRequest, db=Depends(get_db)):
    """Create SSH port forward from local machine to rdev worker."""
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")
    if not is_rdev_host(s.host):
        raise HTTPException(400, "Tunnel only supported for rdev workers")
    
    local_port = body.local_port or body.port
    remote_port = body.port
    
    # Check if port already in use (scan real processes)
    existing = discover_active_tunnels()
    if local_port in existing:
        info = existing[local_port]
        if info["host"] == s.host:
            # Same host, same port - tunnel already exists, return success
            return {"ok": True, "local_port": local_port, "remote_port": info["remote_port"], 
                    "pid": info["pid"], "existing": True}
        else:
            # Different host using this port
            raise HTTPException(409, f"Port {local_port} already tunneled to {info['host']}")
    
    # Spawn SSH tunnel in background
    proc = subprocess.Popen(
        ["ssh", "-N", "-L", f"{local_port}:localhost:{remote_port}", s.host],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    
    # Invalidate cache so next list picks up the new tunnel
    global _cache_timestamp
    _cache_timestamp = 0
    
    logger.info("Created tunnel local:%d -> %s:%d (pid=%d)", local_port, s.host, remote_port, proc.pid)
    return {"ok": True, "local_port": local_port, "remote_port": remote_port, "pid": proc.pid}

@router.delete("/sessions/{session_id}/tunnel/{port}")
def close_tunnel(session_id: str, port: int, db=Depends(get_db)):
    """Close a specific port tunnel."""
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")
    
    # Find tunnel by scanning processes
    tunnels = discover_active_tunnels()
    if port in tunnels:
        info = tunnels[port]
        # Verify it belongs to this session's host
        if info["host"] == s.host:
            try:
                os.kill(info["pid"], signal.SIGTERM)
                logger.info("Closed tunnel on port %d (pid=%d)", port, info["pid"])
            except ProcessLookupError:
                pass
            # Invalidate cache
            global _cache_timestamp
            _cache_timestamp = 0
            return {"ok": True, "closed": True}
    
    return {"ok": True, "closed": False, "message": "Tunnel not found"}

@router.get("/sessions/{session_id}/tunnels")
def list_tunnels(session_id: str, db=Depends(get_db)):
    """List active tunnels for a session (real-time via process scan)."""
    s = repo.get_session(db, session_id)
    if s is None:
        raise HTTPException(404, "Session not found")
    
    # Get tunnels filtered by this session's host
    tunnels = get_tunnels_for_session(session_id, s.host)
    return {
        "tunnels": {
            str(port): {"remote_port": info["remote_port"], "pid": info["pid"], "host": info["host"]}
            for port, info in tunnels.items()
        }
    }

@router.get("/tunnels")
def list_all_tunnels(db=Depends(get_db)):
    """List all active tunnels across all hosts (for brain/admin)."""
    tunnels = discover_active_tunnels()
    return {"tunnels": tunnels}
```

### 3. Worker Skill Documentation

**Add to:** `agents/worker/prompt.md`

```markdown
### orch-tunnel — Request Port Forwarding

When you need the user to access a port on this rdev (e.g., dev server, debugger):

```bash
orch-tunnel <port>           # Forward user's localhost:<port> to this rdev's <port>
orch-tunnel <port> --close   # Close the tunnel
```

**When to use:**
- You started a dev server (e.g., `npm run dev` on port 4200)
- You started a debugger that listens on a port
- You need the user to access a web UI running on rdev

**Example:**
```bash
# After starting vite dev server
orch-tunnel 4200
# User can now open http://localhost:4200 in their browser
```

The tunnel remains open until closed or the worker session ends.
```

### 4. Brain Access

**Add to:** `agents/brain/settings.json` permissions:

```json
{
  "permissions": {
    "allow": [
      "Bash(orch-tunnel *)"
    ]
  }
}
```

**Add to:** `agents/brain/prompt.md` or brain skills:

```markdown
### Tunnel Management (for rdev workers)

You can request port forwarding for any rdev worker:

**Via API (preferred for brain):**
```bash
# Create tunnel for a specific worker
curl -X POST "http://127.0.0.1:8093/api/sessions/{session_id}/tunnel" \
  -H 'Content-Type: application/json' \
  -d '{"port": 4200}'

# List all active tunnels
curl "http://127.0.0.1:8093/api/tunnels"

# Close a tunnel
curl -X DELETE "http://127.0.0.1:8093/api/sessions/{session_id}/tunnel/4200"
```

**When to use:**
- A worker reports they've started a dev server
- You need to help debug by accessing a worker's local service
- Coordinating multi-worker scenarios that need port access
```

### 5. Frontend: WorkerCard Footer

**Location:** `frontend/src/components/workers/WorkerCard.tsx`

Add tunnel display to footer:

```tsx
// In WorkerCard component
const [tunnels, setTunnels] = useState<Record<string, {remote_port: number}>>({})

useEffect(() => {
  // Fetch tunnels periodically for rdev workers
  if (!session.host.includes('/')) return
  
  const fetchTunnels = async () => {
    const data = await api<{tunnels: Record<string, {remote_port: number}>}>(
      `/api/sessions/${session.id}/tunnels`
    )
    setTunnels(data.tunnels || {})
  }
  fetchTunnels()
  const interval = setInterval(fetchTunnels, 10000)
  return () => clearInterval(interval)
}, [session.id, session.host])

// In footer JSX:
{Object.keys(tunnels).length > 0 && (
  <div className="wc-tunnels">
    {Object.entries(tunnels).map(([port, info]) => (
      <a 
        key={port}
        href={`http://localhost:${port}`}
        target="_blank"
        className="wc-tunnel-badge"
        onClick={e => e.stopPropagation()}
      >
        :{port}
      </a>
    ))}
  </div>
)}
```

### 6. Cleanup

Tunnels are cleaned up when:
1. **Session deleted** — `delete_session()` scans and kills all tunnels for that session's host
2. **Explicit close** — Worker or brain calls `orch-tunnel <port> --close`
3. **Manual kill** — User can always `kill <pid>` directly

**Note:** SSH tunnels survive server restart. This is intentional — they're discovered via process scan on next query.

Add to `delete_session()` in `sessions.py`:
```python
def cleanup_session_tunnels(host: str) -> None:
    """Kill all tunnels for a given rdev host."""
    tunnels = discover_active_tunnels()
    for port, info in tunnels.items():
        if info["host"] == host:
            try:
                os.kill(info["pid"], signal.SIGTERM)
                logger.info("Cleanup: killed tunnel on port %d for host %s", port, host)
            except ProcessLookupError:
                pass

# Call in delete_session():
if is_rdev_host(s.host):
    cleanup_session_tunnels(s.host)
```

## API Summary

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/sessions/{id}/tunnel` | Create tunnel `{"port": 4200}` |
| DELETE | `/api/sessions/{id}/tunnel/{port}` | Close specific tunnel |
| GET | `/api/sessions/{id}/tunnels` | List tunnels for session |
| GET | `/api/tunnels` | List all tunnels (brain/admin) |

## Implementation Order

1. **API endpoints** — In-memory registry + tunnel CRUD
2. **CLI script** — `orch-tunnel` command in `agents/worker/bin/`
3. **Worker skill** — Document in `agents/worker/prompt.md`
4. **Brain access** — Add permission + skill to brain
5. **Frontend** — Display active tunnels in WorkerCard footer
6. **Cleanup** — Hook into `delete_session()`

## Security Considerations

- Only allow tunnel creation for the session's own rdev host
- Validate port numbers (1024-65535 for non-root)
- Check for port conflicts before spawning SSH
- Tunnels auto-terminate when server stops (child processes)

## Future Enhancements

- Auto-close tunnels after idle timeout
- WebSocket notifications when tunnel opens/closes
- Tunnel health monitoring (detect broken SSH connections)
- Support for remote port != local port

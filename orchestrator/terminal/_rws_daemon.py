# ruff: noqa
# type: ignore
# WARNING: This file is NOT imported as a Python module.
# It is read as a text file, base64-encoded, and sent to remote hosts
# where it runs as a standalone daemon using only Python stdlib.
# Do NOT add any third-party imports.
# --- DAEMON SCRIPT START ---
import json, os, sys, socket, selectors, signal, time, errno
import pty as pty_mod, struct, fcntl, termios, subprocess, shutil
import base64, tempfile, re, uuid, threading

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 9741
INACTIVITY_TIMEOUT = 3600  # 60 min
RINGBUFFER_MAX = 524288    # 512 KB per PTY

# Set by bootstrap; used for version-aware daemon replacement
SCRIPT_VERSION = os.environ.get("_RWS_VERSION", "unknown")

# ── File operation handlers ──────────────────────────────────────────

DEFAULT_IGNORED = {
    "__pycache__", "node_modules", ".git", ".tox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", ".egg-info",
    ".venv", "venv", ".next", ".DS_Store", "Thumbs.db",
}
GIT_STATUS_MAP = {
    "M": "modified", "A": "added", "D": "deleted", "R": "renamed",
    "C": "copied", "U": "conflicting", "?": "untracked", "!": "ignored",
}
SEVERITY = ["conflicting", "deleted", "modified", "added", "renamed", "untracked", "ignored"]

def handle_ping(cmd):
    return {"status": "pong"}

def handle_server_info(cmd):
    return {
        "status": "ok",
        "pid": os.getpid(),
        "port": LISTEN_PORT,
        "pty_count": len(pty_sessions),
        "browser_count": len(browser_processes),
        "version": SCRIPT_VERSION,
    }

def handle_check_path(cmd):
    # Check if paths exist on the remote host.
    # NOT restricted to work_dir -- only returns booleans (no data leak).
    # Used by health check to detect /tmp wipes on the remote side.
    paths = cmd.get("paths", [])
    if not paths:
        return {"error": "paths is required and must be a non-empty list"}
    missing = [p for p in paths if not os.path.exists(p)]
    return {"missing": missing, "missing_count": len(missing)}

def handle_check_mtimes(cmd):
    work_dir = cmd["work_dir"]
    paths = cmd.get("paths", [])
    norm_work = os.path.normpath(work_dir)
    mtimes = {}
    for p in paths:
        target = os.path.normpath(os.path.join(work_dir, p))
        if not target.startswith(norm_work):
            mtimes[p] = None
            continue
        try:
            mtimes[p] = os.stat(target).st_mtime
        except OSError:
            mtimes[p] = None
    return {"mtimes": mtimes}

def handle_list_dir(cmd):
    work_dir = cmd["work_dir"]
    rel_path = cmd["path"]
    show_hidden = cmd.get("show_hidden", cmd.get("show_ignored", True))
    max_depth = cmd.get("depth", 1)

    norm_work = os.path.normpath(work_dir)
    target = os.path.normpath(os.path.join(work_dir, rel_path))
    if not target.startswith(norm_work):
        return {"error": "Path outside work_dir"}

    if not os.path.isdir(target):
        return {"error": "Directory not found"}

    git_statuses = {}
    git_available = False
    gcmd = ["git", "status", "--porcelain=v1", "-z", "--ignored"]
    try:
        r = subprocess.run(gcmd, cwd=work_dir, capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            git_available = True
            for entry in r.stdout.split("\0"):
                if len(entry) < 4:
                    continue
                xy = entry[:2]
                p = entry[3:].rstrip("/")
                code = xy[0] if xy[0] != " " else xy[1]
                git_statuses[p] = GIT_STATUS_MAP.get(code, "modified")
    except Exception:
        pass

    def apply_git(entries, inherited_status=None):
        if not git_available:
            return
        for ent in entries:
            p = ent["path"]
            if p in git_statuses:
                ent["git_status"] = git_statuses[p]
            elif ent["is_dir"]:
                prefix = p + "/"
                child = [s for k, s in git_statuses.items() if k.startswith(prefix)]
                if child:
                    non_ignored = [s for s in child if s != "ignored"]
                    if non_ignored:
                        for sev in SEVERITY:
                            if sev in non_ignored:
                                ent["git_status"] = sev
                                break
                        else:
                            ent["git_status"] = non_ignored[0]
                elif inherited_status:
                    ent["git_status"] = inherited_status
            elif inherited_status:
                ent["git_status"] = inherited_status
            # Propagate untracked/ignored downward (like VS Code)
            propagate = None
            if ent.get("git_status") in ("untracked", "ignored"):
                propagate = ent["git_status"]
            if ent.get("children"):
                apply_git(ent["children"], propagate)

    def scan_dir(abs_path, current_depth):
        entries = []
        try:
            for e in os.scandir(abs_path):
                name = e.name
                if not show_hidden and name.startswith(".") and name != ".":
                    continue
                rp = os.path.relpath(e.path, work_dir)
                is_dir = e.is_dir(follow_symlinks=False)
                try:
                    st = e.stat(follow_symlinks=False)
                    size = st.st_size if not is_dir else None
                    modified = st.st_mtime
                except OSError:
                    size = None
                    modified = None
                children_count = None
                sub_children = None
                if is_dir:
                    try:
                        children_count = sum(1 for _ in os.scandir(e.path))
                    except OSError:
                        pass
                    if current_depth < max_depth:
                        sub_children = scan_dir(e.path, current_depth + 1)
                entries.append({
                    "name": name, "path": rp, "is_dir": is_dir,
                    "size": size, "modified": modified,
                    "children_count": children_count,
                    "git_status": None,
                    "children": sub_children,
                })
        except PermissionError:
            pass
        entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        return entries

    entries = scan_dir(target, 1)
    # If the listed directory itself is untracked/ignored, propagate to children
    parent_status = git_statuses.get(rel_path) if rel_path != "." else None
    initial_inherit = parent_status if parent_status in ("untracked", "ignored") else None
    apply_git(entries, initial_inherit)
    return {"entries": entries, "git_available": git_available}

def handle_read_file(cmd):
    work_dir = cmd["work_dir"]
    rel_path = cmd["path"]
    max_lines = cmd.get("max_lines", 500)

    target = os.path.normpath(os.path.join(work_dir, rel_path))
    if not target.startswith(os.path.normpath(work_dir)):
        return {"error": "Path outside work_dir"}

    if not os.path.isfile(target):
        return {"error": "File not found"}

    st = os.stat(target)
    file_size = st.st_size
    file_mtime = st.st_mtime
    if file_size > 5 * 1024 * 1024:
        return {"error": "File too large (>5MB)", "code": 413}

    try:
        with open(target, "rb") as f:
            chunk = f.read(8192)
            if b"\x00" in chunk:
                return {
                    "content": "", "truncated": False,
                    "total_lines": None, "size": file_size,
                    "binary": True, "modified": file_mtime,
                }
    except OSError as e:
        return {"error": str(e)}

    try:
        with open(target, encoding="utf-8", errors="replace") as f:
            lines = []
            total = 0
            for line in f:
                total += 1
                if total <= max_lines:
                    lines.append(line)
            return {
                "content": "".join(lines),
                "truncated": total > max_lines,
                "total_lines": total,
                "size": file_size,
                "modified": file_mtime,
                "binary": False,
            }
    except OSError as e:
        return {"error": str(e)}

def handle_read_file_raw(cmd):
    work_dir = cmd["work_dir"]
    rel_path = cmd["path"]
    max_size = cmd.get("max_size", 10 * 1024 * 1024)

    target = os.path.normpath(os.path.join(work_dir, rel_path))
    if not target.startswith(os.path.normpath(work_dir)):
        return {"error": "Path outside work_dir"}

    if not os.path.isfile(target):
        return {"error": "File not found"}

    file_size = os.path.getsize(target)
    if file_size > max_size:
        return {"error": f"File too large (>{max_size // (1024*1024)}MB)", "code": 413}

    try:
        with open(target, "rb") as f:
            raw = f.read()
        return {"content_b64": base64.b64encode(raw).decode("ascii"), "size": len(raw)}
    except OSError as e:
        return {"error": str(e)}

def handle_write_file(cmd):
    work_dir = cmd["work_dir"]
    rel_path = cmd["path"]
    content_b64 = cmd["content_b64"]
    expected_mtime = cmd.get("expected_mtime")
    allow_create = cmd.get("create", False)

    target = os.path.normpath(os.path.join(work_dir, rel_path))
    if not target.startswith(os.path.normpath(work_dir)):
        return {"error": "Path outside work_dir"}

    if not allow_create and not os.path.isfile(target):
        return {"error": "File not found"}

    if allow_create:
        os.makedirs(os.path.dirname(target), exist_ok=True)

    if expected_mtime is not None and os.path.isfile(target):
        cur = os.stat(target).st_mtime
        if abs(cur - expected_mtime) > 0.5:
            return {"conflict": True, "size": os.path.getsize(target), "modified": cur}

    content = base64.b64decode(content_b64).decode("utf-8")

    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        os.replace(tmp, target)
    except PermissionError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return {"error": "Permission denied"}
    except Exception as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return {"error": str(exc)}

    st = os.stat(target)
    return {"conflict": False, "size": st.st_size, "modified": st.st_mtime}

def handle_delete(cmd):
    work_dir = cmd["work_dir"]
    rel_path = cmd.get("path", "")
    if not rel_path:
        return {"error": "No path provided"}
    norm_work = os.path.normpath(work_dir)
    target = os.path.normpath(os.path.join(work_dir, rel_path))
    if not target.startswith(norm_work + os.sep) and target != norm_work:
        return {"error": "Path outside work_dir"}
    if target == norm_work:
        return {"error": "Cannot delete work_dir itself"}
    if os.path.isdir(target):
        shutil.rmtree(target)
    elif os.path.isfile(target) or os.path.islink(target):
        os.remove(target)
    else:
        return {"error": "Not found"}
    return {"status": "ok"}

def handle_move(cmd):
    work_dir = cmd["work_dir"]
    from_path = cmd.get("from_path", "")
    to_path = cmd.get("to_path", "")
    if not from_path or not to_path:
        return {"error": "Both from_path and to_path are required"}
    norm_work = os.path.normpath(work_dir)
    src = os.path.normpath(os.path.join(work_dir, from_path))
    dst = os.path.normpath(os.path.join(work_dir, to_path))
    if not src.startswith(norm_work + os.sep) and src != norm_work:
        return {"error": "Source path outside work_dir"}
    if not dst.startswith(norm_work + os.sep) and dst != norm_work:
        return {"error": "Destination path outside work_dir"}
    if not os.path.exists(src):
        return {"error": "Not found"}
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    return {"status": "ok"}

def handle_mkdir(cmd):
    work_dir = cmd["work_dir"]
    rel_path = cmd.get("path", "")
    if not rel_path:
        return {"error": "No path provided"}
    norm_work = os.path.normpath(work_dir)
    target = os.path.normpath(os.path.join(work_dir, rel_path))
    if not target.startswith(norm_work + os.sep) and target != norm_work:
        return {"error": "Path outside work_dir"}
    if target == norm_work:
        return {"error": "Cannot mkdir work_dir itself"}
    os.makedirs(target, exist_ok=True)
    return {"status": "ok"}

# ── Browser process management ───────────────────────────────────────

# session_id -> {"pid": int, "port": int, "started_at": float}
browser_processes = {}

def _find_chromium():
    """Find a Chromium executable. Checks Playwright cache first, then PATH."""
    import glob as _glob
    pw_dir = os.path.expanduser("~/.cache/ms-playwright")
    # Search for chrome/chromium binaries in Playwright cache.
    # Different Playwright versions and fallback builds use different
    # directory layouts (chrome-linux/, chrome/, platform-specific, etc.)
    # so we search broadly for known binary names.
    binary_names = ("chrome", "headless_shell", "chromium")
    for name in binary_names:
        pattern = os.path.join(pw_dir, "chromium*", "**", name)
        matches = sorted(_glob.glob(pattern, recursive=True), reverse=True)
        for m in matches:
            if os.path.isfile(m) and os.access(m, os.X_OK):
                return m
    # Fallback: check PATH
    for name in ("chromium-browser", "chromium", "google-chrome", "chrome"):
        path = shutil.which(name)
        if path:
            return path
    return None

def _ensure_fonts():
    """Install system fonts so Chromium renders text properly.

    On headless Linux servers, default font packages are often missing,
    causing garbled/box characters in the browser.
    """
    # Quick check: if liberation fonts exist, skip install
    try:
        result = subprocess.run(
            ["fc-list"],
            capture_output=True, text=True, timeout=5,
        )
        if "liberation" in result.stdout.lower() or "noto" in result.stdout.lower():
            return
    except (OSError, subprocess.TimeoutExpired):
        pass

    # Download Noto Sans fonts to user-local directory (no root required).
    # Uses Google Fonts CDN (fonts.gstatic.com) which serves TTF files directly.
    font_dir = os.path.join(os.path.expanduser("~"), ".local", "share", "fonts")
    ns = "https://fonts.gstatic.com/s/notosans/v42"
    nm = "https://fonts.gstatic.com/s/notosansmono/v37"
    fonts = {
        "NotoSans-Regular.ttf": (
            f"{ns}/o-0mIpQlx3QUlC5A4PNB6Ryti20_6n1iPHjcz6L1SoM"
            "-jCpoiyD9A99d.ttf"
        ),
        "NotoSans-Bold.ttf": (
            f"{ns}/o-0mIpQlx3QUlC5A4PNB6Ryti20_6n1iPHjcz6L1SoM"
            "-jCpoiyAaBN9d.ttf"
        ),
        "NotoSansMono-Regular.ttf": (
            f"{nm}/BngrUXNETWXI6LwhGYvaxZikqZqK6fBq6kPvUce2oAZ"
            "cdthSBUsYck4-_FNJ49o.ttf"
        ),
    }
    try:
        os.makedirs(font_dir, exist_ok=True)
        for fname, url in fonts.items():
            dest = os.path.join(font_dir, fname)
            if os.path.exists(dest):
                continue
            subprocess.run(
                ["curl", "-fSL", "-o", dest, url],
                timeout=30, check=True,
            )
        subprocess.run(["fc-cache", "-f", font_dir], timeout=10)
    except Exception:
        pass

def _find_npx():
    """Find npx binary, setting up Node 24 via volta if needed.

    The daemon runs with minimal PATH (non-interactive SSH), so npx
    from nvm/volta shims is not available.  Search known locations and
    bootstrap Node 24 via volta if needed.
    """
    import glob as _glob

    # 1. Check existing node-bin symlinks (created by ensure_rdev_node)
    for npx_path in sorted(
        _glob.glob("/tmp/orchestrator/workers/*/node-bin/npx"), reverse=True
    ):
        if os.path.isfile(npx_path) and os.access(npx_path, os.X_OK):
            return npx_path

    # 2. Try to set up Node 24 via volta (rdev ships volta at ~/.volta)
    volta = os.path.expanduser("~/.volta/bin/volta")
    if os.path.isfile(volta) and os.access(volta, os.X_OK):
        try:
            subprocess.run(
                [volta, "install", "node@24"],
                timeout=60,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass
        try:
            result = subprocess.run(
                [volta, "which", "npx"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                npx_path = result.stdout.strip()
                if os.path.isfile(npx_path) and os.access(npx_path, os.X_OK):
                    return npx_path
        except (subprocess.TimeoutExpired, OSError):
            pass

    # 3. Fallback: bare PATH lookup
    return shutil.which("npx")

def _install_chromium():
    """Install Chromium via Playwright, return the binary path or None."""
    npx = _find_npx()
    if not npx:
        return None
    # Ensure npx can find node by prepending its directory to PATH
    env = os.environ.copy()
    npx_dir = os.path.dirname(os.path.realpath(npx))
    env["PATH"] = npx_dir + ":" + env.get("PATH", "")
    try:
        subprocess.run(
            [npx, "playwright", "install", "chromium"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=300,
            env=env,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    _ensure_fonts()
    return _find_chromium()

def _is_port_in_use(port):
    """Check if a TCP port is in use on localhost."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(1)
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except (ConnectionRefusedError, OSError):
        s.close()
        return False

def _wait_for_cdp(port, timeout=10):
    """Wait for CDP to become available on a port."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.settimeout(1)
            s.connect(("127.0.0.1", port))
            s.close()
            return True
        except (ConnectionRefusedError, OSError):
            s.close()
            time.sleep(0.5)
    return False

def _cleanup_browser(session_id):
    """Stop a browser process and remove from registry."""
    info = browser_processes.pop(session_id, None)
    if not info:
        return
    pid = info["pid"]
    try:
        os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        return
    # Wait up to 3s for graceful shutdown
    for _ in range(30):
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.1)
    # Force kill
    try:
        os.kill(pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass

# Background Chromium install state (avoids blocking the event loop)
_chromium_install = {"active": False, "result": None, "error": None}

def _bg_install_chromium():
    """Run _install_chromium() in a background thread, updating _chromium_install."""
    try:
        path = _install_chromium()
        _chromium_install["result"] = path
        _chromium_install["error"] = None if path else "install returned None"
    except Exception as exc:
        _chromium_install["result"] = None
        _chromium_install["error"] = str(exc)
    finally:
        _chromium_install["active"] = False

def handle_browser_start(cmd):
    session_id = cmd.get("session_id", "")
    port = cmd.get("port", 9222)
    chromium_path = cmd.get("chromium_path")

    if not session_id:
        return {"error": "session_id required"}

    # Already running for this session?
    existing = browser_processes.get(session_id)
    if existing:
        # Verify still alive
        try:
            os.kill(existing["pid"], 0)
            return {
                "status": "ok",
                "already_running": True,
                "pid": existing["pid"],
                "port": existing["port"],
            }
        except OSError:
            # Dead — clean up stale entry
            browser_processes.pop(session_id, None)

    # Check if port is in use
    if _is_port_in_use(port):
        return {"error": f"Port {port} is already in use"}

    # Find Chromium, auto-install if missing
    if not chromium_path:
        chromium_path = _find_chromium()
    if not chromium_path:
        # Check background install state
        if _chromium_install["active"]:
            return {"status": "installing"}
        if _chromium_install["result"]:
            chromium_path = _chromium_install["result"]
            _chromium_install["result"] = None
        else:
            # Start background install thread
            _chromium_install["active"] = True
            _chromium_install["result"] = None
            _chromium_install["error"] = None
            t = threading.Thread(target=_bg_install_chromium, daemon=True)
            t.start()
            return {"status": "installing"}
    if not chromium_path:
        return {"error": "Chromium not found and auto-install failed"}

    # Ensure system fonts are available (best-effort, no-op if present)
    _ensure_fonts()

    # Launch Chromium directly (no Node.js dependency)
    args = [
        chromium_path,
        "--headless",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--remote-debugging-port=" + str(port),
        "--remote-debugging-address=127.0.0.1",
        "about:blank",
    ]
    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=False,  # Keep in daemon process group
        )
    except OSError as e:
        return {"error": f"Failed to launch Chromium: {e}"}

    # Wait for CDP to become available
    if not _wait_for_cdp(port, timeout=10):
        # Kill the process since it didn't start properly
        try:
            proc.kill()
        except OSError:
            pass
        return {"error": "Chromium started but CDP did not become available within 10s"}

    browser_processes[session_id] = {
        "pid": proc.pid,
        "port": port,
        "started_at": time.time(),
    }

    return {
        "status": "ok",
        "already_running": False,
        "pid": proc.pid,
        "port": port,
    }

def handle_browser_stop(cmd):
    session_id = cmd.get("session_id", "")
    if not session_id:
        return {"error": "session_id required"}

    if session_id not in browser_processes:
        return {"status": "ok", "was_running": False}

    _cleanup_browser(session_id)
    return {"status": "ok", "was_running": True}

def handle_browser_status(cmd):
    session_id = cmd.get("session_id")
    if session_id:
        info = browser_processes.get(session_id)
        if not info:
            return {"status": "ok", "running": False}
        # Verify still alive
        try:
            os.kill(info["pid"], 0)
        except OSError:
            browser_processes.pop(session_id, None)
            return {"status": "ok", "running": False}
        return {
            "status": "ok",
            "running": True,
            "pid": info["pid"],
            "port": info["port"],
            "started_at": info["started_at"],
        }
    else:
        # Return all browsers
        result = []
        for sid, info in list(browser_processes.items()):
            alive = True
            try:
                os.kill(info["pid"], 0)
            except OSError:
                alive = False
                browser_processes.pop(sid, None)
            if alive:
                result.append({
                    "session_id": sid,
                    "pid": info["pid"],
                    "port": info["port"],
                    "started_at": info["started_at"],
                })
        return {"status": "ok", "browsers": result}

# ── Environment setup ─────────────────────────────────────────────────

def handle_setup_env(cmd):
    # Ensure $HOME/.local/bin is on PATH and run `claude update`.
    # Hard-coded action -- no arbitrary commands accepted.
    # Rdev images often ship with stale PATH and outdated Claude.
    home = os.path.expanduser("~")
    local_bin = os.path.join(home, ".local", "bin")

    # 1. Ensure PATH includes ~/.local/bin for this daemon process
    #    (affects future subprocess calls within this daemon)
    current_path = os.environ.get("PATH", "")
    if local_bin not in current_path.split(os.pathsep):
        os.environ["PATH"] = local_bin + os.pathsep + current_path

    # 2. Run claude update (best-effort, never blocks setup)
    update_result = {"ran_update": False, "update_output": ""}
    claude_bin = shutil.which("claude")
    if claude_bin:
        try:
            proc = subprocess.run(
                [claude_bin, "update"],
                capture_output=True,
                timeout=60,
                env={**os.environ, "PATH": os.environ["PATH"]},
            )
            update_result["ran_update"] = True
            update_result["update_output"] = (
                proc.stdout.decode("utf-8", errors="replace")[:500]
            )
            update_result["return_code"] = proc.returncode
        except Exception as e:
            update_result["update_output"] = str(e)

    return {
        "status": "ok",
        "local_bin": local_bin,
        "path_updated": local_bin in os.environ.get("PATH", ""),
        **update_result,
    }

# ── PTY management ────────────────────────────────────────────────────

class PtySession:
    def __init__(self, pty_id, master_fd, child_pid, cmd, cwd, cols, rows,
                 session_id=None, role=None):
        self.pty_id = pty_id
        self.master_fd = master_fd
        self.child_pid = child_pid
        self.cmd = cmd
        self.cwd = cwd
        self.cols = cols
        self.rows = rows
        self.session_id = session_id
        self.role = role
        self.created_at = time.time()
        self.ringbuffer = bytearray()
        self.stream_conns = []  # list of socket connections for streaming
        self.alive = True
        self._last_stream_time = time.time()

    def append_output(self, data):
        self.ringbuffer.extend(data)
        if len(self.ringbuffer) > RINGBUFFER_MAX:
            self.ringbuffer = self.ringbuffer[-RINGBUFFER_MAX:]

    def is_child_alive(self):
        if not self.alive:
            return False
        try:
            pid, status = os.waitpid(self.child_pid, os.WNOHANG)
            if pid != 0:
                self.alive = False
                return False
            return True
        except ChildProcessError:
            self.alive = False
            return False

pty_sessions = {}  # pty_id -> PtySession
_server_fd = -1  # Set by run_server(); closed in PTY children
sel = selectors.DefaultSelector()

def handle_pty_create(cmd):
    shell_cmd = cmd.get("cmd", "/bin/bash")
    cwd = cmd.get("cwd", os.path.expanduser("~"))
    cols = cmd.get("cols", 80)
    rows = cmd.get("rows", 24)
    session_id = cmd.get("session_id")
    role = cmd.get("role")
    env_vars = cmd.get("env")  # dict or None
    pty_id = uuid.uuid4().hex[:12]

    master_fd, slave_fd = pty_mod.openpty()

    # Set terminal size
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

    child_pid = os.fork()
    if child_pid == 0:
        # Child process
        os.setsid()
        # Close the server listen socket so exec'd shell doesn't
        # hold port 9741 open (prevents daemon upgrades).
        if _server_fd >= 0:
            try:
                os.close(_server_fd)
            except OSError:
                pass
        # Set slave as controlling terminal
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        os.close(master_fd)
        os.close(slave_fd)
        try:
            os.chdir(cwd)
        except OSError:
            pass
        # Ensure TERM is set so programs output colors
        os.environ["TERM"] = "xterm-256color"
        # Apply custom environment variables
        if env_vars and isinstance(env_vars, dict):
            for k, v in env_vars.items():
                os.environ[k] = str(v)
        # Login shell wrapping: when cmd is a command string (not /bin/bash),
        # wrap in login shell so PATH, VOLTA_HOME, etc. from profiles are loaded.
        if shell_cmd == "/bin/bash":
            os.execvp("/bin/bash", ["bash", "-l"])
        else:
            os.execvp("/bin/bash", ["bash", "-l", "-c", shell_cmd])

    # Parent
    os.close(slave_fd)

    # Set master_fd to non-blocking
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    session = PtySession(
        pty_id, master_fd, child_pid, shell_cmd, cwd, cols, rows,
        session_id, role,
    )
    pty_sessions[pty_id] = session

    # Register master_fd with selector for reading
    sel.register(master_fd, selectors.EVENT_READ, data=("pty_output", pty_id))

    return {"status": "ok", "pty_id": pty_id}

def handle_pty_destroy(cmd):
    pty_id = cmd.get("pty_id", "")
    session = pty_sessions.get(pty_id)
    if not session:
        return {"error": "PTY not found"}
    cleanup_pty(pty_id)
    return {"status": "ok"}

def handle_pty_list(cmd):
    result = []
    for pty_id, session in list(pty_sessions.items()):
        result.append({
            "pty_id": pty_id,
            "cmd": session.cmd,
            "cwd": session.cwd,
            "cols": session.cols,
            "rows": session.rows,
            "alive": session.is_child_alive(),
            "created_at": session.created_at,
            "session_id": session.session_id,
            "role": session.role,
        })
    return {"status": "ok", "ptys": result}

def handle_pty_capture(cmd):
    pty_id = cmd.get("pty_id", "")
    max_lines = cmd.get("lines", 30)
    session = pty_sessions.get(pty_id)
    if not session:
        return {"error": "PTY not found"}

    # Return raw ringbuffer bytes (base64) for proper rendering by orchestrator.
    # Also include cols/rows so the orchestrator can create a matching virtual screen.
    raw_b64 = base64.b64encode(bytes(session.ringbuffer)).decode("ascii")
    return {
        "status": "ok",
        "raw": raw_b64,
        "cols": session.cols,
        "rows": session.rows,
    }

def handle_pty_resize(cmd):
    pty_id = cmd.get("pty_id", "")
    cols = cmd.get("cols", 80)
    rows = cmd.get("rows", 24)
    session = pty_sessions.get(pty_id)
    if not session:
        return {"error": "PTY not found"}
    session.cols = cols
    session.rows = rows
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    try:
        fcntl.ioctl(session.master_fd, termios.TIOCSWINSZ, winsize)
        # Send SIGWINCH to child process group
        os.killpg(os.getpgid(session.child_pid), signal.SIGWINCH)
    except (OSError, ProcessLookupError):
        pass
    return {"status": "ok"}

def handle_pty_input(cmd):
    pty_id = cmd.get("pty_id", "")
    data = cmd.get("data", "")
    session = pty_sessions.get(pty_id)
    if not session:
        return {"error": "PTY not found"}
    try:
        os.write(session.master_fd, data.encode("utf-8"))
    except OSError as e:
        return {"error": str(e)}
    return {"status": "ok"}

def cleanup_pty(pty_id):
    session = pty_sessions.pop(pty_id, None)
    if not session:
        return
    # Unregister from selector
    try:
        sel.unregister(session.master_fd)
    except (KeyError, ValueError):
        pass
    # Close master fd
    try:
        os.close(session.master_fd)
    except OSError:
        pass
    # Kill child
    try:
        os.killpg(os.getpgid(session.child_pid), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    try:
        os.waitpid(session.child_pid, os.WNOHANG)
    except ChildProcessError:
        pass
    # Close stream connections
    for conn in session.stream_conns:
        try:
            sel.unregister(conn)
        except (KeyError, ValueError):
            pass
        try:
            conn.close()
        except OSError:
            pass

COMMAND_HANDLERS = {
    "ping": handle_ping,
    "server_info": handle_server_info,
    "check_path": handle_check_path,
    "check_mtimes": handle_check_mtimes,
    "list_dir": handle_list_dir,
    "read_file": handle_read_file,
    "read_file_raw": handle_read_file_raw,
    "write_file": handle_write_file,
    "delete": handle_delete,
    "move": handle_move,
    "mkdir": handle_mkdir,
    "pty_create": handle_pty_create,
    "pty_destroy": handle_pty_destroy,
    "pty_list": handle_pty_list,
    "pty_capture": handle_pty_capture,
    "pty_resize": handle_pty_resize,
    "pty_input": handle_pty_input,
    "browser_start": handle_browser_start,
    "browser_stop": handle_browser_stop,
    "browser_status": handle_browser_status,
    "setup_env": handle_setup_env,
}

# ── Connection management ─────────────────────────────────────────────

command_conns = {}  # fileno -> {"conn": sock, "buffer": bytearray}
pty_stream_conns = {}  # fileno -> {"conn": sock, "pty_id": str}
pending_conns = {}  # fileno -> {"conn": sock, "buffer": bytearray} (awaiting handshake)

last_activity = time.time()

def update_activity():
    global last_activity
    last_activity = time.time()

def handle_new_connection(server_sock):
    conn, addr = server_sock.accept()
    conn.setblocking(False)
    fileno = conn.fileno()
    pending_conns[fileno] = {"conn": conn, "buffer": bytearray()}
    sel.register(conn, selectors.EVENT_READ, data=("pending", fileno))
    update_activity()

def handle_pending_data(fileno):
    info = pending_conns.get(fileno)
    if not info:
        return
    conn = info["conn"]
    try:
        data = conn.recv(4096)
    except (BlockingIOError, ConnectionError):
        return
    if not data:
        # Connection closed before handshake
        remove_pending(fileno)
        return

    info["buffer"].extend(data)
    # Look for newline (end of handshake JSON)
    if b"\n" not in info["buffer"]:
        if len(info["buffer"]) > 4096:
            remove_pending(fileno)
        return

    line, rest = info["buffer"].split(b"\n", 1)
    try:
        handshake = json.loads(line.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        remove_pending(fileno)
        return

    conn_type = handshake.get("type")
    if conn_type == "command":
        # Promote to command connection
        del pending_conns[fileno]
        try:
            sel.unregister(conn)
        except (KeyError, ValueError):
            pass
        command_conns[fileno] = {"conn": conn, "buffer": bytearray(rest)}
        sel.register(conn, selectors.EVENT_READ, data=("command", fileno))
        # Send handshake ack
        try:
            conn.sendall(json.dumps({"status": "ok", "type": "command"}).encode() + b"\n")
        except OSError:
            remove_command(fileno)

    elif conn_type == "pty_stream":
        pty_id = handshake.get("pty_id", "")
        session = pty_sessions.get(pty_id)
        if not session:
            try:
                conn.sendall(json.dumps({"error": "PTY not found"}).encode() + b"\n")
            except OSError:
                pass
            remove_pending(fileno)
            return

        del pending_conns[fileno]
        try:
            sel.unregister(conn)
        except (KeyError, ValueError):
            pass
        pty_stream_conns[fileno] = {"conn": conn, "pty_id": pty_id, "buffer": bytearray(rest)}
        session.stream_conns.append(conn)
        sel.register(conn, selectors.EVENT_READ, data=("pty_stream", fileno))

        # Send handshake ack
        try:
            ack = {"status": "ok", "type": "pty_stream", "pty_id": pty_id}
            conn.sendall(json.dumps(ack).encode() + b"\n")
        except OSError:
            remove_pty_stream(fileno)
            return

        # Send ringbuffer (history replay) unless client opted out
        skip_rb = handshake.get("skip_ringbuffer", False)
        if session.ringbuffer and not skip_rb:
            try:
                # Temporarily set blocking with timeout to prevent busy-loop
                # on large ringbuffers (sendall on non-blocking socket can spin)
                conn.setblocking(True)
                conn.settimeout(30.0)
                conn.sendall(bytes(session.ringbuffer))
                conn.setblocking(False)
            except OSError:
                remove_pty_stream(fileno)
    else:
        remove_pending(fileno)

def handle_command_data(fileno):
    info = command_conns.get(fileno)
    if not info:
        return
    conn = info["conn"]
    try:
        data = conn.recv(65536)
    except (BlockingIOError, ConnectionError):
        return
    if not data:
        remove_command(fileno)
        return

    update_activity()
    info["buffer"].extend(data)

    while b"\n" in info["buffer"]:
        line, info["buffer"] = info["buffer"].split(b"\n", 1)
        try:
            cmd = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            try:
                conn.sendall(json.dumps({"error": "Invalid JSON"}).encode() + b"\n")
            except OSError:
                remove_command(fileno)
                return
            continue

        action = cmd.get("action", "")
        handler = COMMAND_HANDLERS.get(action)
        if handler:
            try:
                result = handler(cmd)
            except Exception as exc:
                result = {"error": str(exc)}
        else:
            result = {"error": f"Unknown action: {action}"}

        try:
            conn.sendall(json.dumps(result).encode() + b"\n")
        except OSError:
            remove_command(fileno)
            return

def handle_pty_stream_data(fileno):
    info = pty_stream_conns.get(fileno)
    if not info:
        return
    conn = info["conn"]
    pty_id = info["pty_id"]
    try:
        data = conn.recv(65536)
    except (BlockingIOError, ConnectionError):
        return
    if not data:
        remove_pty_stream(fileno)
        return

    update_activity()
    info.setdefault("buffer", bytearray()).extend(data)

    # Parse JSON-line commands from client (input, resize)
    while b"\n" in info["buffer"]:
        line, info["buffer"] = info["buffer"].split(b"\n", 1)
        try:
            cmd = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        msg_type = cmd.get("type", "")
        session = pty_sessions.get(pty_id)
        if not session:
            continue

        if msg_type == "input":
            try:
                os.write(session.master_fd, cmd.get("data", "").encode("utf-8"))
            except OSError:
                pass
        elif msg_type == "resize":
            cols = cmd.get("cols", 80)
            rows = cmd.get("rows", 24)
            session.cols = cols
            session.rows = rows
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            try:
                fcntl.ioctl(session.master_fd, termios.TIOCSWINSZ, winsize)
                os.killpg(os.getpgid(session.child_pid), signal.SIGWINCH)
            except (OSError, ProcessLookupError):
                pass

def handle_pty_output(pty_id):
    session = pty_sessions.get(pty_id)
    if not session:
        return
    try:
        data = os.read(session.master_fd, 65536)
    except OSError as e:
        if e.errno in (errno.EIO, errno.EBADF):
            # PTY child exited
            cleanup_pty(pty_id)
            return
        if e.errno == errno.EAGAIN:
            return
        cleanup_pty(pty_id)
        return

    if not data:
        cleanup_pty(pty_id)
        return

    update_activity()
    session.append_output(data)
    session._last_stream_time = time.time()

    # Push to all stream connections
    dead_conns = []
    for conn in session.stream_conns:
        try:
            conn.sendall(data)
        except OSError:
            dead_conns.append(conn)
    for conn in dead_conns:
        session.stream_conns.remove(conn)
        # Find and remove from pty_stream_conns
        for fn, info in list(pty_stream_conns.items()):
            if info["conn"] is conn:
                remove_pty_stream(fn)
                break

def remove_pending(fileno):
    info = pending_conns.pop(fileno, None)
    if info:
        try:
            sel.unregister(info["conn"])
        except (KeyError, ValueError):
            pass
        try:
            info["conn"].close()
        except OSError:
            pass

def remove_command(fileno):
    info = command_conns.pop(fileno, None)
    if info:
        try:
            sel.unregister(info["conn"])
        except (KeyError, ValueError):
            pass
        try:
            info["conn"].close()
        except OSError:
            pass

def remove_pty_stream(fileno):
    info = pty_stream_conns.pop(fileno, None)
    if info:
        conn = info["conn"]
        pty_id = info["pty_id"]
        try:
            sel.unregister(conn)
        except (KeyError, ValueError):
            pass
        try:
            conn.close()
        except OSError:
            pass
        # Remove from PTY session's stream list
        session = pty_sessions.get(pty_id)
        if session and conn in session.stream_conns:
            session.stream_conns.remove(conn)

# ── Daemonize and run ─────────────────────────────────────────────────

def _kill_pid(pid):
    # Send SIGTERM then SIGKILL to a process. Best-effort.
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(20):  # wait up to 2s
            time.sleep(0.1)
            try:
                os.kill(pid, 0)
            except OSError:
                return  # Dead
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.2)
    except OSError:
        pass

def _find_port_owner():
    # Find the PID of the process listening on LISTEN_PORT by
    # parsing /proc/net/tcp + scanning /proc/*/fd.  Returns PID or None.
    try:
        hex_port = f"{LISTEN_PORT:04X}"
        target_local = f"0100007F:{hex_port}"  # 127.0.0.1:PORT
        inode = None
        with open("/proc/net/tcp") as f:
            for line in f:
                fields = line.split()
                if len(fields) >= 10 and fields[1] == target_local:
                    # State 0A = LISTEN
                    if fields[3] == "0A":
                        inode = fields[9]
                        break
        if not inode or inode == "0":
            return None

        # Scan /proc/*/fd for the socket inode
        target = f"socket:[{inode}]"
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            fd_dir = f"/proc/{entry}/fd"
            try:
                for fd in os.listdir(fd_dir):
                    try:
                        link = os.readlink(f"{fd_dir}/{fd}")
                        if link == target:
                            return int(entry)
                    except OSError:
                        continue
            except OSError:
                continue
    except OSError:
        pass
    return None

def check_existing_daemon():
    pid_file = f"/tmp/orchestrator-rws-{LISTEN_PORT}.pid"
    ver_file = f"/tmp/orchestrator-rws-{LISTEN_PORT}.version"
    if os.path.exists(pid_file):
        try:
            with open(pid_file) as f:
                old_pid = int(f.read().strip())
            # Check if process is alive
            os.kill(old_pid, 0)

            # Check version — if outdated, kill and replace
            old_version = None
            try:
                with open(ver_file) as f:
                    old_version = f.read().strip()
            except OSError:
                pass

            if old_version != SCRIPT_VERSION:
                # Version mismatch — check if PTYs are active before killing
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(3)
                    s.connect((LISTEN_HOST, LISTEN_PORT))
                    s.sendall(json.dumps({"type": "command"}).encode() + b"\n")
                    ack = b""
                    while b"\n" not in ack:
                        ack += s.recv(4096)
                    s.sendall(json.dumps({"action": "pty_list"}).encode() + b"\n")
                    resp = b""
                    while b"\n" not in resp:
                        resp += s.recv(4096)
                    result = json.loads(resp.split(b"\n")[0].decode())
                    s.close()
                    ptys = result.get("ptys", [])
                    alive_ptys = [p for p in ptys if p.get("alive")]
                    if alive_ptys:
                        # Defer upgrade — reuse old daemon to avoid killing active PTYs
                        log("Deferring daemon upgrade: %d active PTYs" % len(alive_ptys))
                        return old_pid
                except Exception:
                    pass  # Can't connect — safe to kill
                # No active PTYs (or can't connect) — kill and replace
                _kill_pid(old_pid)
                for f_path in (pid_file, ver_file):
                    try:
                        os.unlink(f_path)
                    except OSError:
                        pass
                return None  # Force fresh start

            # Process exists — try to connect and ping
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2)
                s.connect((LISTEN_HOST, LISTEN_PORT))
                s.sendall(json.dumps({"type": "command"}).encode() + b"\n")
                # Read handshake ack
                ack = b""
                while b"\n" not in ack:
                    ack += s.recv(4096)
                # Send ping
                s.sendall(json.dumps({"action": "ping"}).encode() + b"\n")
                resp = b""
                while b"\n" not in resp:
                    resp += s.recv(4096)
                result = json.loads(resp.split(b"\n")[0].decode())
                s.close()
                if result.get("status") == "pong":
                    return old_pid  # Daemon is alive and responding
            except Exception:
                pass
        except (ValueError, OSError):
            pass
        # Stale PID file
        for f_path in (pid_file, ver_file):
            try:
                os.unlink(f_path)
            except OSError:
                pass

    # Fallback: check if something else is holding the port.
    # This catches orphaned daemons whose PID file was overwritten
    # by a later (failed) deployment attempt.
    owner = _find_port_owner()
    if owner and owner != os.getpid():
        _kill_pid(owner)

    return None

def write_pid_file():
    pid_file = f"/tmp/orchestrator-rws-{LISTEN_PORT}.pid"
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
    ver_file = f"/tmp/orchestrator-rws-{LISTEN_PORT}.version"
    with open(ver_file, "w") as f:
        f.write(SCRIPT_VERSION)

def daemonize():
    # First fork
    pid = os.fork()
    if pid > 0:
        # Parent returns child PID
        return pid

    # Child — create new session
    os.setsid()

    # Second fork (prevent terminal acquisition)
    pid = os.fork()
    if pid > 0:
        os._exit(0)

    # Grandchild — the actual daemon
    # Close inherited file descriptors
    try:
        os.close(0)
        os.close(1)
        os.close(2)
    except OSError:
        pass

    # Redirect to /dev/null
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    if devnull > 2:
        os.close(devnull)

    # Write PID file
    write_pid_file()

    # Set up signal handler for clean shutdown
    def shutdown_handler(signum, frame):
        for sid in list(browser_processes.keys()):
            _cleanup_browser(sid)
        for pty_id in list(pty_sessions.keys()):
            cleanup_pty(pty_id)
        for suffix in (".pid", ".version"):
            try:
                os.unlink(f"/tmp/orchestrator-rws-{LISTEN_PORT}{suffix}")
            except OSError:
                pass
        os._exit(0)

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    # Ignore SIGHUP (terminal hangup)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)

    run_server()

def run_server():
    global _server_fd
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LISTEN_HOST, LISTEN_PORT))
    server.listen(16)
    server.setblocking(False)
    _server_fd = server.fileno()
    sel.register(server, selectors.EVENT_READ, data=("accept", None))

    while True:
        # Inactivity check
        if (time.time() - last_activity > INACTIVITY_TIMEOUT
                and not pty_sessions
                and not browser_processes
                and not command_conns
                and not pty_stream_conns):
            break

        # Clean up dead PTYs
        for pty_id in list(pty_sessions.keys()):
            session = pty_sessions.get(pty_id)
            if session and not session.is_child_alive():
                cleanup_pty(pty_id)

        # Clean up dead browsers
        for sid in list(browser_processes.keys()):
            info = browser_processes.get(sid)
            if info:
                try:
                    os.kill(info["pid"], 0)
                except OSError:
                    browser_processes.pop(sid, None)

        try:
            events = sel.select(timeout=5.0)
        except OSError:
            continue

        for key, mask in events:
            kind, ident = key.data
            if kind == "accept":
                handle_new_connection(key.fileobj)
            elif kind == "pending":
                handle_pending_data(ident)
            elif kind == "command":
                handle_command_data(ident)
            elif kind == "pty_stream":
                handle_pty_stream_data(ident)
            elif kind == "pty_output":
                handle_pty_output(ident)

        # Send heartbeats on idle PTY stream connections (every 15s)
        now = time.time()
        for pty_id, session in list(pty_sessions.items()):
            if not session.stream_conns:
                continue
            if now - session._last_stream_time >= 15.0:
                session._last_stream_time = now
                dead_conns = []
                for conn in session.stream_conns:
                    try:
                        conn.sendall(b"\x00")
                    except OSError:
                        dead_conns.append(conn)
                for conn in dead_conns:
                    session.stream_conns.remove(conn)
                    for fn, info in list(pty_stream_conns.items()):
                        if info["conn"] is conn:
                            remove_pty_stream(fn)
                            break

    # Shutdown
    for sid in list(browser_processes.keys()):
        _cleanup_browser(sid)
    for pty_id in list(pty_sessions.keys()):
        cleanup_pty(pty_id)
    for suffix in (".pid", ".version"):
        try:
            os.unlink(f"/tmp/orchestrator-rws-{LISTEN_PORT}{suffix}")
        except OSError:
            pass

# ── Entry point ───────────────────────────────────────────────────────

existing = check_existing_daemon()
if existing:
    # Daemon already running — report it
    print(json.dumps({"status": "ok", "pid": existing, "port": LISTEN_PORT, "reused": True}))
    sys.stdout.flush()
    sys.exit(0)

# Fork to background
daemon_pid = daemonize()
if daemon_pid is not None and daemon_pid > 0:
    # Parent (SSH session) — wait a moment for daemon to bind, then report
    time.sleep(0.5)
    print(json.dumps({"status": "ok", "pid": daemon_pid, "port": LISTEN_PORT, "reused": False}))
    sys.stdout.flush()
    sys.exit(0)
# else: we are the daemon, run_server() was called from daemonize()

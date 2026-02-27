"""Persistent remote file server over SSH.

Instead of spawning a fresh ``python3 -`` per SSH call, this module keeps a
long-lived Python process running on the remote host.  Commands are sent as
JSON lines over stdin; responses come back as JSON lines on stdout.
"""

from __future__ import annotations

import base64
import json
import logging
import subprocess
import textwrap
import threading
from typing import Any

from orchestrator.terminal.file_sync import _SSH_OPTS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Remote server script (executed on the remote host)
# ---------------------------------------------------------------------------
_REMOTE_FILE_SERVER_SCRIPT = textwrap.dedent("""\
    import json, os, shutil, subprocess, sys, base64, tempfile

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

    def respond(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    def handle_ping(cmd):
        respond({"status": "pong"})

    def handle_list_dir(cmd):
        work_dir = cmd["work_dir"]
        rel_path = cmd["path"]
        show_ignored = cmd.get("show_ignored", False)
        max_depth = cmd.get("depth", 1)

        norm_work = os.path.normpath(work_dir)
        target = os.path.normpath(os.path.join(work_dir, rel_path))
        if not target.startswith(norm_work):
            respond({"error": "Path outside work_dir"})
            return

        if not os.path.isdir(target):
            respond({"error": "Directory not found"})
            return

        # Git status (once, reused by all depths)
        git_statuses = {}
        git_available = False
        gcmd = ["git", "status", "--porcelain=v1", "-z"]
        if show_ignored:
            gcmd.append("--ignored")
        try:
            r = subprocess.run(gcmd, cwd=work_dir, capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                git_available = True
                for entry in r.stdout.split("\\0"):
                    if len(entry) < 4:
                        continue
                    xy = entry[:2]
                    p = entry[3:]
                    code = xy[0] if xy[0] != " " else xy[1]
                    git_statuses[p] = GIT_STATUS_MAP.get(code, "modified")
        except Exception:
            pass

        def apply_git(entries):
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
                        for sev in SEVERITY:
                            if sev in child:
                                ent["git_status"] = sev
                                break
                        else:
                            ent["git_status"] = child[0]
                if ent.get("children"):
                    apply_git(ent["children"])

        def scan_dir(abs_path, current_depth):
            entries = []
            try:
                for e in os.scandir(abs_path):
                    name = e.name
                    if not show_ignored and name in DEFAULT_IGNORED:
                        continue
                    if not show_ignored and name.startswith(".") and name != ".":
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
        apply_git(entries)
        respond({"entries": entries, "git_available": git_available})

    def handle_read_file(cmd):
        work_dir = cmd["work_dir"]
        rel_path = cmd["path"]
        max_lines = cmd.get("max_lines", 500)

        target = os.path.normpath(os.path.join(work_dir, rel_path))
        if not target.startswith(os.path.normpath(work_dir)):
            respond({"error": "Path outside work_dir"})
            return

        if not os.path.isfile(target):
            respond({"error": "File not found"})
            return

        st = os.stat(target)
        file_size = st.st_size
        file_mtime = st.st_mtime
        if file_size > 5 * 1024 * 1024:
            respond({"error": "File too large (>5MB)", "code": 413})
            return

        # Binary detection
        try:
            with open(target, "rb") as f:
                chunk = f.read(8192)
                if b"\\x00" in chunk:
                    respond({
                        "content": "", "truncated": False,
                        "total_lines": None, "size": file_size,
                        "binary": True, "modified": file_mtime,
                    })
                    return
        except OSError as e:
            respond({"error": str(e)})
            return

        # Read text
        try:
            with open(target, encoding="utf-8", errors="replace") as f:
                lines = []
                total = 0
                for line in f:
                    total += 1
                    if total <= max_lines:
                        lines.append(line)
                respond({
                    "content": "".join(lines),
                    "truncated": total > max_lines,
                    "total_lines": total,
                    "size": file_size,
                    "modified": file_mtime,
                    "binary": False,
                })
        except OSError as e:
            respond({"error": str(e)})

    def handle_write_file(cmd):
        work_dir = cmd["work_dir"]
        rel_path = cmd["path"]
        content_b64 = cmd["content_b64"]
        expected_mtime = cmd.get("expected_mtime")
        allow_create = cmd.get("create", False)

        target = os.path.normpath(os.path.join(work_dir, rel_path))
        if not target.startswith(os.path.normpath(work_dir)):
            respond({"error": "Path outside work_dir"})
            return

        if not allow_create and not os.path.isfile(target):
            respond({"error": "File not found"})
            return

        if allow_create:
            os.makedirs(os.path.dirname(target), exist_ok=True)

        # Conflict detection
        if expected_mtime is not None and os.path.isfile(target):
            cur = os.stat(target).st_mtime
            if abs(cur - expected_mtime) > 0.5:
                respond({"conflict": True, "size": os.path.getsize(target), "modified": cur})
                return

        content = base64.b64decode(content_b64).decode("utf-8")

        # Atomic write
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
            respond({"error": "Permission denied"})
            return
        except Exception as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            respond({"error": str(exc)})
            return

        st = os.stat(target)
        respond({"conflict": False, "size": st.st_size, "modified": st.st_mtime})

    def handle_delete(cmd):
        work_dir = cmd["work_dir"]
        rel_path = cmd.get("path", "")
        if not rel_path:
            respond({"error": "No path provided"})
            return
        norm_work = os.path.normpath(work_dir)
        target = os.path.normpath(os.path.join(work_dir, rel_path))
        if not target.startswith(norm_work + os.sep) and target != norm_work:
            respond({"error": "Path outside work_dir"})
            return
        if target == norm_work:
            respond({"error": "Cannot delete work_dir itself"})
            return
        if os.path.isdir(target):
            shutil.rmtree(target)
        elif os.path.isfile(target) or os.path.islink(target):
            os.remove(target)
        else:
            respond({"error": "Not found"})
            return
        respond({"status": "ok"})

    def handle_move(cmd):
        work_dir = cmd["work_dir"]
        from_path = cmd.get("from_path", "")
        to_path = cmd.get("to_path", "")
        if not from_path or not to_path:
            respond({"error": "Both from_path and to_path are required"})
            return
        norm_work = os.path.normpath(work_dir)
        src = os.path.normpath(os.path.join(work_dir, from_path))
        dst = os.path.normpath(os.path.join(work_dir, to_path))
        if not src.startswith(norm_work + os.sep) and src != norm_work:
            respond({"error": "Source path outside work_dir"})
            return
        if not dst.startswith(norm_work + os.sep) and dst != norm_work:
            respond({"error": "Destination path outside work_dir"})
            return
        if not os.path.exists(src):
            respond({"error": "Not found"})
            return
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        respond({"status": "ok"})

    HANDLERS = {
        "ping": handle_ping,
        "list_dir": handle_list_dir,
        "read_file": handle_read_file,
        "write_file": handle_write_file,
        "delete": handle_delete,
        "move": handle_move,
    }

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError:
            respond({"error": "Invalid JSON"})
            continue
        action = cmd.get("action", "")
        handler = HANDLERS.get(action)
        if handler:
            try:
                handler(cmd)
            except Exception as exc:
                respond({"error": str(exc)})
        else:
            respond({"error": f"Unknown action: {action}"})
""")

# Bootstrap: python3 -u -c '<this>' reads the server script from the first
# stdin line (base64-encoded), then exec()s it.  Subsequent stdin lines are
# consumed by the server's ``for line in sys.stdin`` loop.
_BOOTSTRAP = "import sys,base64;exec(base64.b64decode(sys.stdin.readline().strip()).decode())"


# ---------------------------------------------------------------------------
# RemoteFileServer — manages one persistent SSH process per host
# ---------------------------------------------------------------------------
class RemoteFileServer:
    """A persistent Python process on a remote host, communicated with via
    JSON lines over SSH stdin/stdout."""

    def __init__(self, host: str):
        self.host = host
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def start(self, timeout: float = 10.0) -> None:
        """Launch the SSH process, send the server script, and verify with a ping."""
        # Pass the entire remote command as a single string so the remote
        # shell doesn't split on semicolons in the bootstrap code.
        remote_cmd = f"python3 -u -c '{_BOOTSTRAP}'"
        cmd = ["ssh", *_SSH_OPTS, self.host, remote_cmd]
        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Send the base64-encoded server script as the first line
        encoded_script = base64.b64encode(_REMOTE_FILE_SERVER_SCRIPT.encode()).decode() + "\n"
        assert self._process.stdin is not None
        self._process.stdin.write(encoded_script.encode())
        self._process.stdin.flush()

        # Verify with a ping
        resp = self.execute({"action": "ping"}, timeout=timeout)
        if resp.get("status") != "pong":
            self.stop()
            raise RuntimeError(f"Remote file server on {self.host} failed ping: {resp}")
        logger.info("Remote file server started on %s", self.host)

    def execute(self, command: dict[str, Any], timeout: float = 15.0) -> dict:
        """Send a JSON command and return the parsed JSON response."""
        if self._process is None or self._process.poll() is not None:
            raise RuntimeError(f"Remote file server on {self.host} is not running")

        with self._lock:
            line = json.dumps(command) + "\n"
            assert self._process.stdin is not None
            assert self._process.stdout is not None
            self._process.stdin.write(line.encode())
            self._process.stdin.flush()

            response_line = self._read_response_with_timeout(timeout)
            if response_line is None:
                raise RuntimeError(f"Remote file server on {self.host} timed out after {timeout}s")
            return json.loads(response_line)

    def _read_response_with_timeout(self, timeout: float) -> str | None:
        """Read a single line from stdout using a daemon thread for timeout."""
        result: list[str] = []
        error: list[Exception] = []

        def _reader():
            try:
                assert self._process is not None and self._process.stdout is not None
                line = self._process.stdout.readline()
                if line:
                    result.append(line.decode().strip())
            except Exception as e:
                error.append(e)

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        t.join(timeout)

        if t.is_alive():
            # Thread is still blocked on readline — server is unresponsive
            return None
        if error:
            raise error[0]
        return result[0] if result else None

    def is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def stop(self) -> None:
        """Shut down the remote server process."""
        if self._process is None:
            return
        try:
            if self._process.stdin:
                self._process.stdin.close()
        except OSError:
            pass
        try:
            self._process.kill()
            self._process.wait(timeout=5)
        except Exception:
            pass
        self._process = None
        logger.info("Remote file server stopped on %s", self.host)


# ---------------------------------------------------------------------------
# Server pool
# ---------------------------------------------------------------------------
_server_pool: dict[str, RemoteFileServer] = {}
_starting: dict[str, threading.Thread] = {}
_pool_lock = threading.Lock()


def get_remote_file_server(host: str) -> RemoteFileServer:
    """Return an alive RemoteFileServer for *host*, if one is ready.

    Never blocks: if no server is ready, kicks off a background start and
    raises ``RuntimeError`` immediately so the caller falls back to the
    one-shot SSH path.  Subsequent calls return the server once it's up.
    """
    with _pool_lock:
        server = _server_pool.get(host)
        if server is not None and server.is_alive():
            return server

        # Already starting in background — don't launch a second one
        if host in _starting and _starting[host].is_alive():
            raise RuntimeError(f"Server for {host} is still starting up")

        # Kick off background start
        def _start_in_background() -> None:
            try:
                s = RemoteFileServer(host)
                s.start()
                with _pool_lock:
                    _server_pool[host] = s
                logger.info("Persistent server ready for %s", host)
            except Exception:
                logger.warning(
                    "Background start of persistent server for %s failed",
                    host,
                    exc_info=True,
                )
            finally:
                with _pool_lock:
                    _starting.pop(host, None)

        t = threading.Thread(target=_start_in_background, daemon=True)
        _starting[host] = t
        t.start()
        raise RuntimeError(f"Server for {host} starting in background")


def ensure_server_starting(host: str) -> None:
    """Trigger a background server start for *host* if not already started.

    Called eagerly (e.g. on session resolve) so the server is ready by the
    time the first file operation arrives.  Never blocks or raises.
    """
    try:
        get_remote_file_server(host)
    except RuntimeError:
        pass  # Expected — "starting in background" or "still starting up"


def shutdown_all_servers() -> None:
    """Stop all persistent remote file servers.  Safe to call multiple times."""
    with _pool_lock:
        for host, server in _server_pool.items():
            try:
                server.stop()
            except Exception:
                logger.debug("Error stopping server for %s", host, exc_info=True)
        _server_pool.clear()
        _starting.clear()
    logger.info("All remote file servers shut down")

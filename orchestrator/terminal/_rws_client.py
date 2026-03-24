"""RemoteWorkerServer — client class managing daemon + forward tunnel.

Manages the lifecycle of:
  1. The daemon process on the remote host
  2. An SSH forward tunnel to reach it
  3. A persistent TCP command connection for JSON-line request/response
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from orchestrator.terminal._rws_pty_renderer import _render_pty_to_text
from orchestrator.terminal.file_sync import _SSH_OPTS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Daemon script loading — read from _rws_daemon.py at import time
# ---------------------------------------------------------------------------
_DAEMON_SCRIPT_PATH = Path(__file__).parent / "_rws_daemon.py"
_DAEMON_MARKER = "# --- DAEMON SCRIPT START ---\n"

_full_daemon_file = _DAEMON_SCRIPT_PATH.read_text(encoding="utf-8")
_REMOTE_WORKER_SERVER_SCRIPT = _full_daemon_file[
    _full_daemon_file.index(_DAEMON_MARKER) + len(_DAEMON_MARKER) :
]
del _full_daemon_file  # free the full-file copy

# Hash of the daemon script, used for version-aware daemon replacement.
# Computed at import time so it changes whenever the script content changes.
_SCRIPT_HASH = hashlib.md5(_REMOTE_WORKER_SERVER_SCRIPT.encode()).hexdigest()[:12]

# Bootstrap: Sets _RWS_VERSION env var (for version-aware upgrade), then reads
# the server script from stdin (base64-encoded) and exec()s it.
_BOOTSTRAP_TMPL = (
    "import sys,os,base64;"
    'os.environ["_RWS_VERSION"]="{version}";'
    "exec(base64.b64decode(sys.stdin.readline().strip()).decode())"
)

# SSH options for the forward tunnel — must NOT use ControlMaster
# so the tunnel process stays alive as the connection owner (not a slave).
# SSH uses first-match-wins for -o directives, so prepending ensures our
# overrides take precedence over the ControlMaster=auto in _SSH_OPTS.
_TUNNEL_SSH_OPTS = ["-o", "ControlMaster=no", "-o", "ControlPath=none", *_SSH_OPTS]

RWS_REMOTE_PORT = 9741


# ---------------------------------------------------------------------------
# RemoteWorkerServer — client class managing daemon + forward tunnel
# ---------------------------------------------------------------------------
class RemoteWorkerServer:
    """Client for a remote worker server daemon.

    Manages the lifecycle of:
      1. The daemon process on the remote host
      2. An SSH forward tunnel to reach it
      3. A persistent TCP command connection for JSON-line request/response
    """

    def __init__(self, host: str):
        self.host = host
        self._local_port: int | None = None
        self._remote_pid: int | None = None
        self._tunnel_proc: subprocess.Popen | None = None
        self._cmd_sock: socket.socket | None = None
        self._cmd_buffer = bytearray()
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()  # protects tunnel + stop operations

    def start(self, timeout: float = 30.0) -> None:
        """Deploy daemon via SSH, establish forward tunnel, verify with ping."""
        # Step 1: Deploy daemon on remote host
        self._deploy_daemon(timeout)

        # Step 2+3: Tunnel + socket with retry (daemon deploy is NOT repeated)
        max_tunnel_attempts = 3
        last_err: Exception | None = None
        for attempt in range(max_tunnel_attempts):
            try:
                self._start_tunnel()
                self._connect_command_socket(timeout)
                break
            except RuntimeError as e:
                last_err = e
                self._cleanup_tunnel()
                if attempt < max_tunnel_attempts - 1:
                    logger.warning(
                        "Tunnel attempt %d/%d for %s failed: %s",
                        attempt + 1,
                        max_tunnel_attempts,
                        self.host,
                        e,
                    )
                    time.sleep(1.0)
                else:
                    raise RuntimeError(
                        f"All {max_tunnel_attempts} tunnel attempts to {self.host} failed"
                    ) from last_err

        logger.info(
            "Remote worker server started on %s (pid=%s, local_port=%s)",
            self.host,
            self._remote_pid,
            self._local_port,
        )

    def _deploy_daemon(self, timeout: float) -> None:
        """Launch the daemon script on the remote host via SSH."""
        bootstrap = _BOOTSTRAP_TMPL.format(version=_SCRIPT_HASH)
        remote_cmd = f"python3 -u -c '{bootstrap}'"
        cmd = ["ssh", *_SSH_OPTS, self.host, remote_cmd]

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Send the base64-encoded daemon script as the first line
        encoded_script = base64.b64encode(_REMOTE_WORKER_SERVER_SCRIPT.encode()).decode() + "\n"
        assert proc.stdin is not None
        proc.stdin.write(encoded_script.encode())
        proc.stdin.flush()

        # Read the daemon's status response
        assert proc.stdout is not None
        try:
            proc.wait(timeout=timeout)
            output = proc.stdout.read().decode().strip()
        except subprocess.TimeoutExpired:
            proc.kill()
            raise RuntimeError(f"Daemon deployment on {self.host} timed out")

        if not output:
            stderr_out = proc.stderr.read().decode().strip() if proc.stderr else ""
            raise RuntimeError(
                f"No output from daemon deployment on {self.host}: stderr={stderr_out}"
            )

        try:
            result = json.loads(output.splitlines()[-1])
        except (json.JSONDecodeError, IndexError) as e:
            raise RuntimeError(f"Invalid daemon response on {self.host}: {output[:200]}") from e

        if result.get("status") != "ok":
            raise RuntimeError(f"Daemon deployment failed on {self.host}: {result}")

        self._remote_pid = result.get("pid")
        reused = result.get("reused", False)
        logger.info(
            "Daemon deployed on %s: pid=%s, reused=%s",
            self.host,
            self._remote_pid,
            reused,
        )

    def _start_tunnel(self) -> None:
        """Start SSH forward tunnel to the daemon."""
        # Find a free local port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            self._local_port = s.getsockname()[1]

        tunnel_cmd = [
            "ssh",
            *_TUNNEL_SSH_OPTS,
            "-N",  # No remote command
            "-L",
            f"{self._local_port}:127.0.0.1:{RWS_REMOTE_PORT}",
            self.host,
        ]
        self._tunnel_proc = subprocess.Popen(
            tunnel_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )

        # Wait for tunnel to actually forward traffic (replaces naive sleep).
        # On slow-auth hosts the SSH handshake can take 5-10s even though
        # the process is alive — polling the local port confirms end-to-end.
        deadline = time.monotonic() + 15.0
        ready = False
        while time.monotonic() < deadline:
            if self._tunnel_proc.poll() is not None:
                # Process exited — port may still work via ControlMaster
                if self._is_tunnel_port_open(timeout=1.0):
                    logger.warning(
                        "Tunnel process exited but port %d open (ControlMaster), continuing",
                        self._local_port,
                    )
                    ready = True
                    break
                stderr = (
                    self._tunnel_proc.stderr.read().decode() if self._tunnel_proc.stderr else ""
                )
                raise RuntimeError(f"SSH forward tunnel to {self.host} failed: {stderr}")
            if self._is_tunnel_port_open(timeout=1.0):
                ready = True
                break
            time.sleep(0.3)

        if not ready:
            raise RuntimeError(
                f"SSH tunnel to {self.host} timed out waiting for local port {self._local_port}"
            )

        logger.info(
            "Forward tunnel established: 127.0.0.1:%d -> %s:127.0.0.1:%d",
            self._local_port,
            self.host,
            RWS_REMOTE_PORT,
        )

    def _connect_command_socket(self, timeout: float = 10.0) -> None:
        """Connect a command TCP socket through the forward tunnel."""
        assert self._local_port is not None

        # Retry connection a few times (tunnel may still be establishing).
        # A fresh socket is needed each attempt because macOS marks a socket
        # as failed after a connect() error — reusing it yields EINVAL.
        sock = None
        for attempt in range(5):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            try:
                sock.connect(("127.0.0.1", self._local_port))
                break
            except (ConnectionRefusedError, OSError):
                sock.close()
                if attempt == 4:
                    raise RuntimeError(
                        f"Cannot connect to RWS on {self.host} via tunnel "
                        f"(local port {self._local_port})"
                    )
                time.sleep(0.5)

        # Send command handshake
        sock.sendall(json.dumps({"type": "command"}).encode() + b"\n")

        # Read handshake ack
        ack = self._recv_line(sock, timeout)
        if ack is None:
            sock.close()
            raise RuntimeError(f"No handshake ack from RWS on {self.host}")

        try:
            ack_data = json.loads(ack)
        except json.JSONDecodeError:
            sock.close()
            raise RuntimeError(f"Invalid handshake ack from RWS on {self.host}: {ack}")

        if ack_data.get("status") != "ok":
            sock.close()
            raise RuntimeError(f"RWS handshake failed on {self.host}: {ack_data}")

        self._cmd_sock = sock
        self._cmd_buffer = bytearray()

        # Verify with ping — done inline (not via execute()) to avoid
        # deadlocking when called from _ensure_connected inside execute().
        sock.sendall(json.dumps({"action": "ping"}).encode() + b"\n")
        pong = self._recv_line(sock, timeout)
        if pong is None:
            self._cmd_sock = None
            sock.close()
            raise RuntimeError(f"RWS ping timed out on {self.host}")
        try:
            resp = json.loads(pong)
        except json.JSONDecodeError:
            self._cmd_sock = None
            sock.close()
            raise RuntimeError(f"RWS ping returned invalid JSON on {self.host}: {pong}")
        if resp.get("status") != "pong":
            self._cmd_sock = None
            sock.close()
            raise RuntimeError(f"RWS ping failed on {self.host}: {resp}")

    def _recv_line(self, sock: socket.socket, timeout: float) -> str | None:
        """Read a single JSON line from a socket."""
        buf = bytearray()
        sock.settimeout(timeout)
        while b"\n" not in buf:
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                return None
            if not chunk:
                return None
            buf.extend(chunk)
        line, _ = buf.split(b"\n", 1)
        return line.decode("utf-8")

    def execute(
        self, command: dict[str, Any], timeout: float = 15.0, connect_timeout: float | None = None
    ) -> dict:
        """Send a JSON command and return the parsed JSON response.

        Thread-safe: uses a lock to serialize access to the command socket.
        Retries once on connection failure (closed connection, broken pipe,
        etc.) by reconnecting and re-sending the command.

        Args:
            command: JSON-serializable dict to send.
            timeout: Timeout for the response read (seconds).
            connect_timeout: Timeout for socket connect + handshake (seconds).
                Defaults to 10.0 if not specified.
        """
        last_err: Exception | None = None
        for attempt in range(2):
            # Reconnect outside the lock — this may block on TCP connect /
            # SSH handshake and we don't want to stall other callers.
            self._ensure_connected(connect_timeout=connect_timeout)
            with self._lock:
                # Re-check: another thread may have cleared _cmd_sock while
                # we waited for the lock.
                if self._cmd_sock is None:
                    if attempt == 0:
                        continue  # retry — _ensure_connected will reconnect
                    raise RuntimeError("Remote host not connected")
                try:
                    line = json.dumps(command) + "\n"
                    self._cmd_sock.sendall(line.encode())

                    # Read response line
                    self._cmd_sock.settimeout(timeout)
                    while b"\n" not in self._cmd_buffer:
                        chunk = self._cmd_sock.recv(1048576)  # 1MB chunks
                        if not chunk:
                            self._cmd_sock = None
                            raise RuntimeError("Remote connection closed")
                        self._cmd_buffer.extend(chunk)

                    resp_line, self._cmd_buffer = self._cmd_buffer.split(b"\n", 1)
                    return json.loads(resp_line.decode("utf-8"))
                except TimeoutError:
                    # Timeout leaves socket in indeterminate state — discard
                    self._cmd_sock = None
                    self._cmd_buffer = bytearray()
                    raise RuntimeError(f"Remote operation timed out after {timeout}s")
                except (RuntimeError, ConnectionError, OSError) as e:
                    self._cmd_sock = None
                    self._cmd_buffer = bytearray()
                    last_err = e
                    if attempt == 0:
                        logger.info(
                            "RWS command failed on %s (%s), retrying",
                            self.host,
                            e,
                        )
                        continue
                    if isinstance(e, RuntimeError):
                        raise
                    raise RuntimeError(f"Remote connection lost: {e}") from e
        # Should not reach here, but safety net
        raise RuntimeError(f"Remote connection failed: {last_err}")

    def _ensure_connected(self, connect_timeout: float | None = None) -> None:
        """Reconnect the command socket if it's dead but the tunnel is alive."""
        if self._cmd_sock is not None:
            return
        tunnel_alive = self._tunnel_proc is not None and (
            self._tunnel_proc.poll() is None or self._is_tunnel_port_open()
        )
        if tunnel_alive:
            try:
                self._connect_command_socket(timeout=connect_timeout or 10.0)
                logger.info("Auto-reconnected command socket for %s", self.host)
            except Exception:
                raise RuntimeError("Remote host not connected")
        else:
            raise RuntimeError("Remote host not connected")

    def create_pty(
        self,
        cmd: str = "/bin/bash",
        cwd: str | None = None,
        cols: int = 80,
        rows: int = 24,
        session_id: str | None = None,
        env: dict[str, str] | None = None,
        role: str | None = None,
    ) -> str:
        """Create a new PTY session on the remote daemon. Returns pty_id."""
        request: dict[str, Any] = {
            "action": "pty_create",
            "cmd": cmd,
            "cols": cols,
            "rows": rows,
        }
        if cwd:
            request["cwd"] = cwd
        if session_id:
            request["session_id"] = session_id
        if env:
            request["env"] = env
        if role:
            request["role"] = role
        resp = self.execute(request)
        if "error" in resp:
            raise RuntimeError(f"PTY create failed on {self.host}: {resp['error']}")
        return resp["pty_id"]

    def connect_pty_stream(
        self, pty_id: str, timeout: float = 10.0, skip_ringbuffer: bool = False
    ) -> tuple[socket.socket, bytes]:
        """Open a dedicated TCP connection for PTY streaming.

        Returns ``(sock, initial_data)`` where:
          - *sock* receives raw PTY output bytes (server→client) and accepts
            JSON-line input/resize commands (client→server), in non-blocking mode.
          - *initial_data* contains ringbuffer history bytes replayed on attach.
            Empty when *skip_ringbuffer* is True and the daemon supports it.
        """
        assert self._local_port is not None
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(("127.0.0.1", self._local_port))

        # Send PTY stream handshake
        hs: dict[str, Any] = {"type": "pty_stream", "pty_id": pty_id}
        if skip_ringbuffer:
            hs["skip_ringbuffer"] = True
        handshake = json.dumps(hs).encode() + b"\n"
        sock.sendall(handshake)

        # Read handshake ack (first line)
        ack_buf = bytearray()
        while b"\n" not in ack_buf:
            chunk = sock.recv(65536)
            if not chunk:
                sock.close()
                raise RuntimeError(f"PTY stream handshake failed for {pty_id} on {self.host}")
            ack_buf.extend(chunk)

        ack_line, remaining = ack_buf.split(b"\n", 1)
        try:
            ack = json.loads(ack_line.decode("utf-8"))
        except json.JSONDecodeError:
            sock.close()
            raise RuntimeError(f"Invalid PTY stream ack for {pty_id}: {ack_line}")

        if "error" in ack:
            sock.close()
            raise RuntimeError(f"PTY stream connect failed: {ack['error']}")

        # Set to non-blocking for async reading
        sock.setblocking(False)

        return sock, bytes(remaining)

    def destroy_pty(self, pty_id: str) -> None:
        """Destroy a PTY session on the remote daemon."""
        resp = self.execute({"action": "pty_destroy", "pty_id": pty_id})
        if "error" in resp:
            logger.warning("PTY destroy failed on %s: %s", self.host, resp["error"])

    def list_ptys(self) -> list[dict]:
        """List active PTY sessions on the remote daemon."""
        resp = self.execute({"action": "pty_list"})
        return resp.get("ptys", [])

    def write_to_pty(self, pty_id: str, data: str) -> None:
        """Write data to a PTY's stdin via the command socket."""
        resp = self.execute({"action": "pty_input", "pty_id": pty_id, "data": data})
        if "error" in resp:
            raise RuntimeError(f"PTY input failed on {self.host}: {resp['error']}")

    def capture_pty(self, pty_id: str, lines: int = 30) -> str:
        """Capture the last N lines of PTY output rendered through a virtual terminal."""
        resp = self.execute({"action": "pty_capture", "pty_id": pty_id, "lines": lines})
        if "error" in resp:
            raise RuntimeError(f"PTY capture failed on {self.host}: {resp['error']}")
        raw_b64 = resp.get("raw")
        if raw_b64:
            raw_bytes = base64.b64decode(raw_b64)
            # Render at a wide virtual screen (min 200 cols) so that lines
            # soft-wrapped at the PTY's actual (possibly narrow) width are
            # unwrapped for the caller (e.g. orch-interactive --capture).
            cols = max(resp.get("cols", 200), 200)
            rows = max(resp.get("rows", 50), 50)
            return _render_pty_to_text(raw_bytes, cols=cols, rows=rows, last_n=lines)
        # Fallback for old daemon versions that return pre-stripped text
        return resp.get("output", "")

    def start_browser(
        self,
        session_id: str,
        port: int = 9222,
        chromium_path: str | None = None,
        timeout: float = 300.0,
    ) -> dict:
        """Start a browser on the remote daemon. Returns status dict with pid/port.

        If the daemon returns ``{"status": "installing"}`` (Chromium being
        installed in a background thread), this method polls every 5 s until
        the install finishes or *timeout* is reached.
        """
        import time as _time

        request: dict[str, Any] = {
            "action": "browser_start",
            "session_id": session_id,
            "port": port,
        }
        if chromium_path:
            request["chromium_path"] = chromium_path
        deadline = _time.monotonic() + timeout
        while True:
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    f"Browser start timed out on {self.host} after {timeout}s "
                    "(Chromium install may still be running)"
                )
            resp = self.execute(request, timeout=min(remaining, 30.0))
            if "error" in resp:
                raise RuntimeError(f"Browser start failed on {self.host}: {resp['error']}")
            if resp.get("status") == "installing":
                _time.sleep(min(5.0, remaining))
                continue
            return resp

    def stop_browser(self, session_id: str) -> None:
        """Stop a browser on the remote daemon."""
        resp = self.execute({"action": "browser_stop", "session_id": session_id})
        if "error" in resp:
            raise RuntimeError(f"Browser stop failed on {self.host}: {resp['error']}")

    def browser_status(self, session_id: str | None = None) -> dict:
        """Get browser status from the remote daemon."""
        request: dict[str, Any] = {"action": "browser_status"}
        if session_id:
            request["session_id"] = session_id
        resp = self.execute(request)
        if "error" in resp:
            raise RuntimeError(f"Browser status failed on {self.host}: {resp['error']}")
        return resp

    def setup_env(self) -> dict:
        """Ensure PATH includes ~/.local/bin and run ``claude update`` on the remote.

        This is a hard-coded action — the daemon does not accept arbitrary
        commands.  Returns a dict with ``path_updated``, ``ran_update``, etc.
        """
        return self.execute({"action": "setup_env"}, timeout=90.0)

    def _cleanup_tunnel(self) -> None:
        """Kill tunnel process and close command socket (used between retries)."""
        if self._cmd_sock is not None:
            try:
                self._cmd_sock.close()
            except OSError:
                pass
            self._cmd_sock = None
            self._cmd_buffer = bytearray()

        if self._tunnel_proc is not None:
            try:
                self._tunnel_proc.kill()
                self._tunnel_proc.wait(timeout=5)
            except Exception:
                pass
            self._tunnel_proc = None

        self._local_port = None

    def _test_daemon_via_ssh(self, timeout: float = 5.0) -> bool:
        """Test daemon reachability via SSH (bypasses tunnel).

        SSHes to the host and pings the daemon on localhost:9741 directly.
        Uses _SSH_OPTS (ControlMaster) so it's fast (~0.5s).
        """
        test_script = (
            f'python3 -c "'
            f"import socket,json; "
            f"s=socket.create_connection(('127.0.0.1',{RWS_REMOTE_PORT}),timeout=3); "
            f"s.sendall(json.dumps({{'type':'command'}}).encode()+b'\\n'); "
            f"s.recv(4096); "
            f"s.sendall(json.dumps({{'action':'ping'}}).encode()+b'\\n'); "
            f'r=s.recv(4096); print(r.decode().strip()); s.close()"'
        )
        cmd = ["ssh", *_SSH_OPTS, self.host, test_script]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return result.returncode == 0 and "pong" in result.stdout
        except Exception:
            return False

    def _is_tunnel_port_open(self, timeout: float = 2.0) -> bool:
        """Check if the tunnel's local forwarded port accepts TCP connections.

        Used as a fallback when ``poll()`` reports the tunnel process as dead —
        the port may still work via SSH ControlMaster multiplexing.
        """
        if self._local_port is None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", self._local_port), timeout=timeout):
                pass
            return True
        except (ConnectionRefusedError, OSError, TimeoutError):
            return False

    def is_alive(self) -> bool:
        """Check if the tunnel and command socket are still connected."""
        if self._tunnel_proc is not None and self._tunnel_proc.poll() is not None:
            if not self._is_tunnel_port_open():
                return False
        if self._cmd_sock is None:
            return False
        # Quick ping test
        try:
            resp = self.execute({"action": "ping"}, timeout=5.0)
            return resp.get("status") == "pong"
        except Exception:
            return False

    def reconnect_tunnel(self) -> None:
        """Re-establish the forward tunnel (e.g. after SSH reconnect).

        Kills old tunnel process and starts a new one, then reconnects
        the command socket.
        """
        with self._lifecycle_lock:
            # Kill old tunnel
            if self._tunnel_proc is not None:
                try:
                    self._tunnel_proc.kill()
                    self._tunnel_proc.wait(timeout=5)
                except Exception:
                    pass
                self._tunnel_proc = None

            # Close old command socket
            if self._cmd_sock is not None:
                try:
                    self._cmd_sock.close()
                except OSError:
                    pass
                self._cmd_sock = None
                self._cmd_buffer = bytearray()

            # Start new tunnel and reconnect
            self._start_tunnel()
            self._connect_command_socket()

    def kill_remote_daemon(self) -> None:
        """Kill the daemon process on the remote host via SSH (final resort)."""
        pid_file = f"/tmp/orchestrator-rws-{RWS_REMOTE_PORT}.pid"
        ver_file = f"/tmp/orchestrator-rws-{RWS_REMOTE_PORT}.version"
        kill_script = (
            f"if [ -f {pid_file} ]; then "
            f"pid=$(cat {pid_file}); "
            f"kill $pid 2>/dev/null; sleep 1; kill -9 $pid 2>/dev/null; "
            f"rm -f {pid_file} {ver_file}; "
            f'echo "killed $pid"; '
            f"else echo no_pid_file; fi"
        )
        cmd = ["ssh", *_SSH_OPTS, self.host, kill_script]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            logger.info(
                "Kill remote daemon on %s: %s",
                self.host,
                result.stdout.strip(),
            )
        except Exception as e:
            logger.warning("Failed to kill remote daemon on %s: %s", self.host, e)

    def stop(self) -> None:
        """Close connections and kill tunnel. Does NOT kill the daemon."""
        with self._lifecycle_lock:
            if self._cmd_sock is not None:
                try:
                    self._cmd_sock.close()
                except OSError:
                    pass
                self._cmd_sock = None

            if self._tunnel_proc is not None:
                try:
                    self._tunnel_proc.kill()
                    self._tunnel_proc.wait(timeout=5)
                except Exception:
                    pass
                self._tunnel_proc = None

            self._local_port = None
        logger.info("Remote worker server client stopped for %s", self.host)

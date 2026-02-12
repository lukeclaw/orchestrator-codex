"""SSH tunnel management for rdev workers.

Provides discovery and management of SSH port-forward tunnels that allow
local access to ports on rdev machines.

Tunnels are discovered via process scanning (ps aux) rather than stored in DB,
since SSH processes survive server restarts.
"""

import logging
import os
import re
import signal
import subprocess
import time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# In-memory cache (rebuilt from process scan)
_tunnel_cache: Dict[int, dict] = {}
_cache_timestamp: float = 0
CACHE_TTL = 5.0  # seconds

# Reserved ports that cannot be used for forward tunnels
# 8093 is used for the reverse tunnel (API access from rdev to local orchestrator)
RESERVED_PORTS = {8093}

def get_reserved_ports() -> set:
    """Get the set of reserved ports that cannot be used for forward tunnels."""
    return RESERVED_PORTS.copy()


def discover_active_tunnels(force_refresh: bool = False) -> Dict[int, dict]:
    """Scan for active SSH port-forward tunnels via ps.
    
    Returns:
        {local_port: {"pid": int, "remote_port": int, "host": str}}
    """
    global _tunnel_cache, _cache_timestamp
    
    now = time.time()
    if not force_refresh and (now - _cache_timestamp) < CACHE_TTL and _tunnel_cache:
        return _tunnel_cache.copy()
    
    tunnels = {}
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        # Pattern: ssh -N -L <local_port>:localhost:<remote_port> <host>
        # Example: ssh -N -L 4200:localhost:4200 user/rdev-vm
        # Also handle: ssh -N -L 4200:localhost:4200 -o Option=value host
        pattern = re.compile(r'ssh\s+.*-N\s+.*-L\s+(\d+):localhost:(\d+)\s+.*?(\S+)\s*$')
        
        for line in result.stdout.split('\n'):
            if 'ssh' not in line or '-L' not in line or '-N' not in line:
                continue
            
            # Skip grep processes
            if 'grep' in line:
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
    except subprocess.TimeoutExpired:
        logger.warning("Tunnel discovery timed out")
    except Exception as e:
        logger.warning("Tunnel discovery failed: %s", e)
    
    _tunnel_cache = tunnels
    _cache_timestamp = now
    return tunnels.copy()


def invalidate_cache() -> None:
    """Invalidate the tunnel cache to force fresh discovery."""
    global _cache_timestamp
    _cache_timestamp = 0


def get_tunnels_for_host(host: str) -> Dict[int, dict]:
    """Get all tunnels for a specific rdev host.
    
    Args:
        host: rdev host (e.g., "user/rdev-vm")
        
    Returns:
        {local_port: {"pid": int, "remote_port": int, "host": str}}
    """
    all_tunnels = discover_active_tunnels()
    return {
        port: info for port, info in all_tunnels.items()
        if info["host"] == host
    }


def find_tunnel_by_port(local_port: int) -> Optional[dict]:
    """Find tunnel info for a specific local port.
    
    Args:
        local_port: The local port number
        
    Returns:
        {"pid": int, "remote_port": int, "host": str} or None
    """
    tunnels = discover_active_tunnels()
    return tunnels.get(local_port)


def is_process_alive(pid: int) -> bool:
    """Check if a process is still running."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def create_tunnel(host: str, remote_port: int, local_port: Optional[int] = None) -> Tuple[bool, dict]:
    """Create an SSH port-forward tunnel.
    
    Args:
        host: rdev host to tunnel to
        remote_port: Port on remote host
        local_port: Local port (defaults to same as remote_port)
        
    Returns:
        (success: bool, info: dict)
        info contains: {"local_port", "remote_port", "pid", "host"} on success
        or {"error": str} on failure
    """
    local_port = local_port or remote_port
    
    # Validate port range
    if not (1 <= local_port <= 65535) or not (1 <= remote_port <= 65535):
        return False, {"error": "Port must be between 1 and 65535"}
    
    # Check for reserved ports (e.g., 8093 used by reverse tunnel for API)
    if local_port in RESERVED_PORTS:
        return False, {"error": f"Port {local_port} is reserved for orchestrator internal use (reverse tunnel)"}
    
    # Check if port already in use
    existing = find_tunnel_by_port(local_port)
    if existing:
        if is_process_alive(existing["pid"]):
            if existing["host"] == host:
                # Same host, same port - tunnel already exists
                return True, {
                    "local_port": local_port,
                    "remote_port": existing["remote_port"],
                    "pid": existing["pid"],
                    "host": host,
                    "existing": True,
                }
            else:
                return False, {"error": f"Port {local_port} already tunneled to {existing['host']}"}
    
    # Spawn SSH tunnel in background
    try:
        proc = subprocess.Popen(
            ["ssh", "-N", "-L", f"{local_port}:localhost:{remote_port}", host],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        
        # Give it a moment to fail if it's going to
        time.sleep(0.2)
        
        if proc.poll() is not None:
            # Process already exited - failed to establish
            return False, {"error": f"SSH tunnel failed to start (exit code: {proc.returncode})"}
        
        # Invalidate cache
        invalidate_cache()
        
        logger.info("Created tunnel local:%d -> %s:%d (pid=%d)", 
                   local_port, host, remote_port, proc.pid)
        
        return True, {
            "local_port": local_port,
            "remote_port": remote_port,
            "pid": proc.pid,
            "host": host,
        }
    except Exception as e:
        logger.error("Failed to create tunnel: %s", e)
        return False, {"error": str(e)}


def close_tunnel(local_port: int, host: Optional[str] = None) -> Tuple[bool, str]:
    """Close an SSH tunnel on a specific port.
    
    Args:
        local_port: The local port of the tunnel
        host: Optional host to verify ownership (safety check)
        
    Returns:
        (success: bool, message: str)
    """
    tunnel = find_tunnel_by_port(local_port)
    
    if not tunnel:
        return False, f"No tunnel found on port {local_port}"
    
    # Verify host if provided
    if host and tunnel["host"] != host:
        return False, f"Port {local_port} belongs to {tunnel['host']}, not {host}"
    
    pid = tunnel["pid"]
    try:
        os.kill(pid, signal.SIGTERM)
        logger.info("Closed tunnel on port %d (pid=%d)", local_port, pid)
        invalidate_cache()
        return True, f"Tunnel on port {local_port} closed"
    except ProcessLookupError:
        invalidate_cache()
        return True, f"Tunnel process {pid} already dead"
    except PermissionError:
        return False, f"Permission denied killing process {pid}"


def cleanup_tunnels_for_host(host: str) -> int:
    """Kill all tunnels for a given rdev host.
    
    Args:
        host: rdev host (e.g., "user/rdev-vm")
        
    Returns:
        Number of tunnels closed
    """
    tunnels = get_tunnels_for_host(host)
    closed = 0
    
    for port, info in tunnels.items():
        try:
            os.kill(info["pid"], signal.SIGTERM)
            logger.info("Cleanup: killed tunnel on port %d for host %s", port, host)
            closed += 1
        except ProcessLookupError:
            pass
        except PermissionError:
            logger.warning("Permission denied killing tunnel pid %d", info["pid"])
    
    if closed > 0:
        invalidate_cache()
    
    return closed

"""General-purpose file sync utility for local <-> remote transfers."""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)

# SSH options matching session.py conventions
_SSH_OPTS = ["-o", "ConnectTimeout=10", "-o", "BatchMode=yes"]


def get_worker_tmp_dir(worker_name: str) -> str:
    """Return the canonical tmp directory for a worker.

    Path is identical on local and remote:
      /tmp/orchestrator/workers/{name}/tmp
    """
    return f"/tmp/orchestrator/workers/{worker_name}/tmp"


def sync_file_to_remote(local_path: str, host: str, remote_path: str) -> bool:
    """Copy a single local file to a remote host via ssh.

    Uses ``ssh cat >`` instead of ``scp`` because rdev hostnames contain
    ``/`` (e.g. ``user/rdev-vm``) and scp treats any target with ``/``
    before the first ``:`` as a local path.

    Creates the remote parent directory first, then streams the file.

    Returns True on success, False otherwise.
    """
    import os

    remote_dir = os.path.dirname(remote_path)
    try:
        mkdir_result = subprocess.run(
            ["ssh", *_SSH_OPTS, host, f"mkdir -p {remote_dir}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if mkdir_result.returncode != 0:
            logger.error("Failed to create remote dir %s: %s", remote_dir, mkdir_result.stderr)
            return False

        with open(local_path, "rb") as f:
            cat_result = subprocess.run(
                ["ssh", *_SSH_OPTS, host, f"cat > {remote_path}"],
                stdin=f,
                capture_output=True,
                timeout=60,
            )
        if cat_result.returncode != 0:
            logger.error("ssh cat failed: %s", cat_result.stderr.decode())
            return False

        logger.info("Synced %s -> %s:%s", local_path, host, remote_path)
        return True

    except subprocess.TimeoutExpired:
        logger.error("File sync timed out: %s -> %s:%s", local_path, host, remote_path)
        return False
    except Exception as e:
        logger.error("File sync failed: %s", e)
        return False


def sync_file_from_remote(host: str, remote_path: str, local_path: str) -> bool:
    """Copy a single file from a remote host to a local path via ssh.

    Uses ``ssh cat`` instead of ``scp`` because rdev hostnames contain
    ``/`` which breaks scp's remote target parsing.

    Creates the local parent directory first, then streams the file.

    Returns True on success, False otherwise.
    """
    import os

    local_dir = os.path.dirname(local_path)
    os.makedirs(local_dir, exist_ok=True)

    try:
        with open(local_path, "wb") as f:
            cat_result = subprocess.run(
                ["ssh", *_SSH_OPTS, host, f"cat {remote_path}"],
                stdout=f,
                stderr=subprocess.PIPE,
                timeout=60,
            )
        if cat_result.returncode != 0:
            logger.error("ssh cat from remote failed: %s", cat_result.stderr.decode())
            # Clean up partial file
            if os.path.exists(local_path):
                os.unlink(local_path)
            return False

        logger.info("Synced %s:%s -> %s", host, remote_path, local_path)
        return True

    except subprocess.TimeoutExpired:
        logger.error("File sync timed out: %s:%s -> %s", host, remote_path, local_path)
        return False
    except Exception as e:
        logger.error("File sync from remote failed: %s", e)
        return False


def sync_dir_to_remote(local_dir: str, host: str, remote_dir: str) -> bool:
    """Copy a local directory to a remote host.

    Delegates to the existing tar-over-ssh implementation in session.py.
    """
    from orchestrator.terminal.session import _copy_dir_to_remote_ssh

    return _copy_dir_to_remote_ssh(local_dir, host, remote_dir)

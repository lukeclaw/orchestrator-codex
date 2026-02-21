#!/usr/bin/env python3
"""Build the PyInstaller sidecar binary for Tauri.

Steps:
1. Build the React frontend (npm ci + npm run build)
2. Run PyInstaller with orchestrator.spec
3. Rename the output binary with the target triple suffix
4. Copy to src-tauri/binaries/
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
SPEC_FILE = PROJECT_ROOT / "orchestrator.spec"
TAURI_BINARIES = PROJECT_ROOT / "src-tauri" / "binaries"


def get_target_triple() -> str:
    """Return the Tauri-compatible target triple for the current platform."""
    machine = platform.machine().lower()
    system = platform.system().lower()

    arch_map = {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
    }
    arch = arch_map.get(machine, machine)

    if system == "darwin":
        return f"{arch}-apple-darwin"
    elif system == "linux":
        return f"{arch}-unknown-linux-gnu"
    elif system == "windows":
        return f"{arch}-pc-windows-msvc"
    else:
        print(f"Warning: unknown platform {system}/{machine}", file=sys.stderr)
        return f"{arch}-{system}"


def build_frontend():
    """Build the React frontend."""
    if not FRONTEND_DIR.exists():
        print("Warning: frontend/ directory not found, skipping frontend build")
        return

    print("==> Building React frontend...")
    subprocess.run(["npm", "ci"], cwd=FRONTEND_DIR, check=True)
    subprocess.run(["npm", "run", "build"], cwd=FRONTEND_DIR, check=True)
    print("==> Frontend build complete")


def build_sidecar():
    """Run PyInstaller to build the sidecar binary."""
    print("==> Building PyInstaller sidecar...")

    # Ensure PyInstaller is available
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("Installing PyInstaller...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)

    subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(SPEC_FILE), "--clean", "--noconfirm"],
        cwd=PROJECT_ROOT,
        check=True,
    )
    print("==> PyInstaller build complete")


def copy_to_tauri():
    """Rename and copy the binary to src-tauri/binaries/ with target triple suffix."""
    dist_binary = PROJECT_ROOT / "dist" / "orchestrator-server"
    if not dist_binary.exists():
        print(f"Error: expected binary at {dist_binary}", file=sys.stderr)
        sys.exit(1)

    triple = get_target_triple()
    dest_name = f"orchestrator-server-{triple}"
    dest_path = TAURI_BINARIES / dest_name

    TAURI_BINARIES.mkdir(parents=True, exist_ok=True)

    print(f"==> Copying binary to {dest_path}")
    shutil.copy2(dist_binary, dest_path)
    dest_path.chmod(0o755)

    print(f"==> Sidecar ready: {dest_path}")
    print(f"    Size: {dest_path.stat().st_size / (1024*1024):.1f} MB")


def main():
    build_frontend()
    build_sidecar()
    copy_to_tauri()
    print("\n==> All done! Run 'cargo tauri build' from src-tauri/ to build the app.")


if __name__ == "__main__":
    main()

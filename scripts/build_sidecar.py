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
    """Copy the onedir bundle to src-tauri/binaries/orchestrator-server-sidecar/."""
    dist_dir = PROJECT_ROOT / "dist" / "orchestrator-server"
    if not dist_dir.exists() or not dist_dir.is_dir():
        print(f"Error: expected onedir output at {dist_dir}", file=sys.stderr)
        sys.exit(1)

    dest_dir = TAURI_BINARIES / "orchestrator-server-sidecar"

    # Clean previous build
    if dest_dir.exists():
        shutil.rmtree(dest_dir)

    print(f"==> Copying onedir bundle to {dest_dir}")
    shutil.copytree(dist_dir, dest_dir)

    # Ensure the main binary is executable
    binary = dest_dir / "orchestrator-server"
    binary.chmod(0o755)

    # Compute total size
    total = sum(f.stat().st_size for f in dest_dir.rglob("*") if f.is_file())
    print(f"==> Sidecar ready: {dest_dir}")
    print(f"    Total size: {total / (1024 * 1024):.1f} MB")


def main():
    build_frontend()
    build_sidecar()
    copy_to_tauri()
    print("\n==> All done! Run 'cargo tauri build' from src-tauri/ to build the app.")


if __name__ == "__main__":
    main()

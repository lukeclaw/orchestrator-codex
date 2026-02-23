from importlib.metadata import version as _pkg_version


def _get_version() -> str:
    # 1. Installed package metadata (pip install / uv pip install)
    try:
        return _pkg_version("claude-orchestrator")
    except Exception:
        pass

    # 2. PyInstaller bundle: read from baked _version.py
    try:
        from orchestrator._version import VERSION

        return VERSION
    except Exception:
        pass

    return "0.0.0"


__version__ = _get_version()

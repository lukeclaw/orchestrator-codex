"""Agent deployment utilities for worker and brain CLI scripts."""

from orchestrator.agents.deploy import (
    BRAIN_SCRIPT_NAMES,
    PLAYWRIGHT_PLUGIN,
    WORKER_SCRIPT_NAMES,
    deploy_brain_scripts,
    deploy_worker_scripts,
    generate_brain_hooks,
    generate_worker_hooks,
    get_brain_prompt,
    get_path_export_command,
    get_worker_prompt,
)

__all__ = [
    "deploy_worker_scripts",
    "deploy_brain_scripts",
    "get_worker_prompt",
    "get_brain_prompt",
    "get_path_export_command",
    "generate_worker_hooks",
    "generate_brain_hooks",
    "WORKER_SCRIPT_NAMES",
    "BRAIN_SCRIPT_NAMES",
    "PLAYWRIGHT_PLUGIN",
]

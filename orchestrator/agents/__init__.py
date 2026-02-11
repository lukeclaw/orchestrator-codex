"""Agent deployment utilities for worker and brain CLI scripts."""

from orchestrator.agents.deploy import (
    deploy_worker_scripts,
    deploy_brain_scripts,
    get_worker_prompt,
    get_brain_prompt,
    get_path_export_command,
    generate_worker_hooks,
    generate_brain_hooks,
    WORKER_SCRIPT_NAMES,
    BRAIN_SCRIPT_NAMES,
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
]

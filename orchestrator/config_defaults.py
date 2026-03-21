"""Centralized setting defaults — single source of truth.

DB only stores user overrides. GET /api/settings merges these defaults
for any key not yet in the database.
"""

SETTING_DEFAULTS: dict[str, object] = {
    "claude.update_before_start": False,
    "claude.skip_permissions": False,
    "ui.preserve_filters": False,
    "ui.theme": "dark",
}

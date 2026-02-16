"""App initialization, DI wiring, CLI entry point."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.table import Table

from orchestrator.state.db import get_connection
from orchestrator.state.migrations.runner import apply_migrations

console = Console()

# Resolve project root (the directory containing pyproject.toml)
PROJECT_ROOT = Path(__file__).parent.parent


def load_config(config_path: Path | None = None) -> dict:
    """Load bootstrap config from YAML."""
    if config_path is None:
        config_path = PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        console.print(f"[red]Config not found: {config_path}[/red]")
        sys.exit(1)
    with open(config_path) as f:
        return yaml.safe_load(f)


def setup_logging(config: dict):
    """Configure logging from bootstrap config."""
    log_cfg = config.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    log_file = log_cfg.get("file")

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        log_path = PROJECT_ROOT / log_file
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(str(log_path)))

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


def init_db(config: dict):
    """Initialize the database: open connection, run migrations."""
    db_path = PROJECT_ROOT / config["database"]["path"]
    conn = get_connection(db_path)
    applied = apply_migrations(conn)
    if applied:
        console.print(f"[green]Applied migrations: {applied}[/green]")
    return conn


@click.group(invoke_without_command=True)
@click.option("--config", "config_path", type=click.Path(exists=True), default=None)
@click.pass_context
def cli(ctx, config_path):
    """Claude Orchestrator — manage multiple Claude Code sessions."""
    config = load_config(Path(config_path) if config_path else None)
    setup_logging(config)

    conn = init_db(config)

    # Store in click context for subcommands
    ctx.ensure_object(dict)
    ctx.obj["config"] = config
    ctx.obj["conn"] = conn

    if ctx.invoked_subcommand is None:
        ctx.invoke(shell)


@cli.command()
@click.pass_context
def shell(ctx):
    """Interactive CLI shell."""
    conn = ctx.obj["conn"]
    config = ctx.obj["config"]

    console.print("[bold cyan]Claude Orchestrator[/bold cyan] v0.1.0")
    console.print("Type [bold]/help[/bold] for available commands, [bold]/quit[/bold] to exit.\n")

    while True:
        try:
            user_input = console.input("[bold green]orchestrator>[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nGoodbye.")
            break

        if not user_input:
            continue

        if user_input in ("/quit", "/exit", "/q"):
            console.print("Goodbye.")
            break
        elif user_input == "/help":
            _show_help()
        elif user_input == "/config":
            _show_config(config)
        elif user_input == "/status":
            _show_status(conn)
        elif user_input.startswith("/add "):
            _handle_add(conn, config, user_input)
        elif user_input.startswith("/remove "):
            _handle_remove(conn, config, user_input)
        elif user_input == "/list":
            _show_status(conn)
        elif user_input.startswith("/output "):
            _handle_output(user_input, config)
        elif user_input.startswith("/send "):
            _handle_send(user_input, config)
        elif user_input.startswith("/attach "):
            _handle_attach(user_input, config)
        else:
            console.print(f"[yellow]Unknown command:[/yellow] {user_input}")
            console.print("Type [bold]/help[/bold] for available commands.")


def _show_help():
    """Display available commands."""
    table = Table(title="Available Commands", show_header=True)
    table.add_column("Command", style="bold cyan")
    table.add_column("Description")

    commands = [
        ("/help", "Show this help message"),
        ("/config", "Show current configuration"),
        ("/status", "Show orchestrator status"),
        ("/list", "List all sessions (alias for /status)"),
        ("/add <name> <host> [path]", "Create a new session"),
        ("/remove <name>", "Remove a session"),
        ("/output <name> [lines]", "Show recent terminal output"),
        ("/send <name> <message>", "Send a message to a session"),
        ("/attach <name>", "Show tmux attach command"),
        ("/quit", "Exit the orchestrator"),
    ]
    for cmd, desc in commands:
        table.add_row(cmd, desc)

    console.print(table)


def _show_config(config: dict):
    """Display current configuration."""
    table = Table(title="Bootstrap Configuration", show_header=True)
    table.add_column("Section", style="bold")
    table.add_column("Key")
    table.add_column("Value", style="green")

    for section, values in config.items():
        if isinstance(values, dict):
            for key, value in values.items():
                table.add_row(section, key, str(value))
        else:
            table.add_row("", section, str(values))

    console.print(table)


def _show_status(conn):
    """Display orchestrator status."""
    from orchestrator.state.repositories.sessions import list_sessions

    sessions = list_sessions(conn)

    console.print("[bold]Orchestrator Status[/bold]\n")

    if not sessions:
        console.print("  [dim]No sessions. Use /add to create one (Phase 2).[/dim]")
    else:
        table = Table(show_header=True)
        table.add_column("Session", style="bold")
        table.add_column("Host")
        table.add_column("Status")
        table.add_column("Task")
        for s in sessions:
            status_color = {
                "idle": "blue",
                "working": "green",
                "waiting": "yellow",
                "error": "red",
                "disconnected": "dim",
            }.get(s.status, "white")
            # Look up task assigned to this session
            from orchestrator.state.repositories import tasks as tasks_repo
            assigned = tasks_repo.list_tasks(conn, assigned_session_id=s.id)
            task_id = assigned[0].id if assigned else "-"
            table.add_row(
                s.name,
                s.host,
                f"[{status_color}]{s.status}[/{status_color}]",
                task_id,
            )
        console.print(table)


def _handle_add(conn, config: dict, user_input: str):
    """Handle /add <name> <host> [path]."""
    from orchestrator.terminal.session import create_session

    parts = user_input.split(maxsplit=3)
    if len(parts) < 3:
        console.print("[yellow]Usage: /add <name> <host> [path][/yellow]")
        return

    name = parts[1]
    host = parts[2]
    work_dir = parts[3] if len(parts) > 3 else None
    tmux_session = config.get("tmux", {}).get("session_name", "orchestrator")

    try:
        session = create_session(conn, name, host, work_dir, tmux_session)
        console.print(f"[green]Created session:[/green] {session.name} ({session.host})")
        console.print(f"  tmux target: orchestrator:{session.name}")
    except Exception as e:
        console.print(f"[red]Failed to create session:[/red] {e}")


def _handle_remove(conn, config: dict, user_input: str):
    """Handle /remove <name>."""
    from orchestrator.terminal.session import remove_session

    parts = user_input.split()
    if len(parts) < 2:
        console.print("[yellow]Usage: /remove <name>[/yellow]")
        return

    name = parts[1]
    tmux_session = config.get("tmux", {}).get("session_name", "orchestrator")

    if remove_session(conn, name, tmux_session):
        console.print(f"[green]Removed session:[/green] {name}")
    else:
        console.print(f"[red]Session not found:[/red] {name}")


def _handle_output(user_input: str, config: dict):
    """Handle /output <name> [lines]."""
    from orchestrator.terminal.session import get_session_output

    parts = user_input.split()
    if len(parts) < 2:
        console.print("[yellow]Usage: /output <name> [lines][/yellow]")
        return

    name = parts[1]
    lines = int(parts[2]) if len(parts) > 2 else 50
    tmux_session = config.get("tmux", {}).get("session_name", "orchestrator")

    output = get_session_output(name, tmux_session, lines)
    if output:
        console.print(f"[bold]Output from {name}:[/bold]")
        console.print(output)
    else:
        console.print(f"[yellow]No output from {name} (window may not exist)[/yellow]")


def _handle_send(user_input: str, config: dict):
    """Handle /send <name> <message>."""
    from orchestrator.terminal.session import send_to_session

    parts = user_input.split(maxsplit=2)
    if len(parts) < 3:
        console.print("[yellow]Usage: /send <name> <message>[/yellow]")
        return

    name = parts[1]
    message = parts[2]
    tmux_session = config.get("tmux", {}).get("session_name", "orchestrator")

    if send_to_session(name, message, tmux_session):
        console.print(f"[green]Sent to {name}:[/green] {message}")
    else:
        console.print(f"[red]Failed to send to {name}[/red]")


def _handle_attach(user_input: str, config: dict):
    """Handle /attach <name>."""
    parts = user_input.split()
    if len(parts) < 2:
        console.print("[yellow]Usage: /attach <name>[/yellow]")
        return

    name = parts[1]
    tmux_session = config.get("tmux", {}).get("session_name", "orchestrator")

    console.print(f"[bold]To attach to session {name}, run:[/bold]")
    console.print(f"  tmux select-window -t {tmux_session}:{name}")
    console.print(f"\nOr attach to the full orchestrator session:")
    console.print(f"  tmux attach -t {tmux_session}")


if __name__ == "__main__":
    cli()

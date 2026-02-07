# Implementation Plan: Claude Orchestrator

**Version:** 1.0  
**Author:** Yu Qiu  
**Date:** February 7, 2026  
**Status:** Planning

---

## Table of Contents

1. [Overview](#1-overview)
2. [Technical Stack](#2-technical-stack)
3. [Project Structure](#3-project-structure)
4. [Phase 1: Foundation](#4-phase-1-foundation-3-4-days)
5. [Phase 2: Core Functionality](#5-phase-2-core-functionality-4-5-days)
6. [Phase 3: Intelligence](#6-phase-3-intelligence-3-4-days)
7. [Phase 4: Polish & Visualization](#7-phase-4-polish--visualization-3-4-days)
8. [Detailed Module Specifications](#8-detailed-module-specifications)
9. [Testing Strategy](#9-testing-strategy)
10. [Deployment](#10-deployment)
11. [Timeline Summary](#11-timeline-summary)

---

## 1. Overview

### 1.1 Implementation Goals

Build a functional Claude Orchestrator MVP that:
- Manages multiple Claude Code sessions via tmux
- Provides unified status across all sessions
- Routes commands and decisions between user and sessions
- Learns from user feedback over time

### 1.2 Guiding Principles

| Principle | Description |
|-----------|-------------|
| **Simplicity First** | Start with minimal viable features, iterate |
| **Local Only** | Everything runs on user's Mac, no cloud dependencies |
| **No rdev Changes** | Remote environments only need SSH access |
| **Fail Gracefully** | Handle errors without crashing |
| **Explicit Actions** | Always confirm before executing commands |

---

## 2. Technical Stack

### 2.1 Core Technologies

| Component | Technology | Rationale |
|-----------|------------|-----------|
| Language | Python 3.11+ | Rapid development, good ecosystem |
| Terminal Control | tmux 3.x | Universal, scriptable, attachable |
| State Storage | SQLite | Zero config, portable, SQL queries |
| Vector Store | ChromaDB | Lightweight, embedded, good for RAG |
| LLM Client | Anthropic SDK / curl | Direct API access |
| CLI Framework | Click + Rich | Beautiful terminal UI |
| Config | YAML | Human-readable |

### 2.2 Python Dependencies

```toml
[project]
name = "claude-orchestrator"
version = "0.1.0"
requires-python = ">=3.11"

dependencies = [
    # CLI & Display
    "click>=8.0",          # CLI framework
    "rich>=13.0",          # Beautiful terminal output
    
    # Web Server & API
    "flask>=3.0",          # Web framework
    "flask-socketio>=5.0", # WebSocket support
    "flask-cors>=4.0",     # CORS for local dev
    
    # LLM & HTTP
    "anthropic>=0.18.0",   # Anthropic SDK (optional, can use curl)
    "httpx>=0.25.0",       # HTTP client
    
    # Config & Data
    "pyyaml>=6.0",         # Config parsing
    "python-dateutil",     # Date handling
    
    # Phase 3: Intelligence
    "chromadb>=0.4.0",     # Vector store
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-asyncio",
    "black",
    "ruff",
    "mypy",
]
```

### 2.3 External Dependencies

| Dependency | Version | Install Method |
|------------|---------|----------------|
| tmux | 3.x | `brew install tmux` |
| Python | 3.11+ | `brew install python@3.11` |
| SQLite | 3.x | Built into macOS |

---

## 3. Project Structure

```
project-manage/orchestrator/
├── pyproject.toml              # Project metadata and dependencies
├── README.md                   # Quick start guide
├── config.yaml                 # User configuration
│
├── docs/
│   ├── PRD.md                  # Product requirements
│   └── IMPLEMENTATION.md       # This document
│
├── orchestrator/
│   ├── __init__.py
│   ├── __main__.py             # Entry point: python -m orchestrator
│   ├── main.py                 # Application initialization
│   │
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── keychain.py         # macOS Keychain operations
│   │   └── token.py            # Token management
│   │
│   ├── terminal/
│   │   ├── __init__.py
│   │   ├── manager.py          # tmux session management
│   │   ├── session.py          # Individual session control
│   │   ├── monitor.py          # Passive output monitoring
│   │   └── ssh.py              # SSH connection helper
│   │
│   ├── state/
│   │   ├── __init__.py
│   │   ├── db.py               # SQLite connection and queries
│   │   ├── models.py           # Data models (dataclasses)
│   │   ├── sessions.py         # Session state management
│   │   ├── decisions.py        # Decision queue management
│   │   └── migrations.py       # Schema migrations
│   │
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── client.py           # Anthropic API client (curl or SDK)
│   │   ├── brain.py            # Decision logic and reasoning
│   │   ├── actions.py          # Action parser and executor
│   │   └── prompts.py          # System prompts
│   │
│   ├── web/                    # Dashboard UI (Primary interface)
│   │   ├── __init__.py
│   │   ├── server.py           # Flask application
│   │   ├── websocket.py        # Socket.IO handlers
│   │   ├── templates/
│   │   │   └── index.html      # Dashboard SPA
│   │   └── static/
│   │       ├── app.js          # Frontend JavaScript
│   │       ├── styles.css      # Dashboard styles
│   │       └── xterm.min.js    # Terminal emulator (for takeover)
│   │
│   ├── api/                    # REST API endpoints
│   │   ├── __init__.py
│   │   ├── routes.py           # API route definitions
│   │   └── handlers.py         # Request handlers
│   │
│   ├── knowledge/              # Phase 3
│   │   ├── __init__.py
│   │   ├── vectors.py          # ChromaDB integration
│   │   └── learning.py         # Pattern learning
│   │
│   └── core/
│       ├── __init__.py
│       ├── orchestrator.py     # Main orchestrator class
│       └── lifecycle.py        # Startup/shutdown management
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py             # Pytest fixtures
│   ├── test_auth.py
│   ├── test_terminal.py
│   ├── test_state.py
│   ├── test_llm.py
│   ├── test_api.py
│   └── test_web.py
│
└── scripts/
    ├── test_auth.py            # Auth testing script (exists)
    └── setup.sh                # Development setup
```

---

## 4. Phase 1: Foundation (3-4 Days)

### 4.1 Goals

- ✅ Authentication working (token from Keychain)
- Project structure set up
- Configuration system
- Basic CLI shell running
- tmux basic operations

### 4.2 Tasks

#### 4.2.1 Project Setup (Day 1 - 2 hours)

```bash
# Task: Initialize project structure
- [ ] Create pyproject.toml with dependencies
- [ ] Create orchestrator package structure
- [ ] Set up __main__.py entry point
- [ ] Create config.yaml template
- [ ] Set up development environment (venv)
```

**Deliverable:** `python -m orchestrator --version` works

#### 4.2.2 Auth Module (Day 1 - 2 hours) ✅ DONE

```bash
# Task: Complete auth module
- [x] test_auth.py working (verified)
- [ ] Create auth/keychain.py with macOS Keychain operations
- [ ] Create auth/manager.py with token retrieval and validation
- [ ] Handle missing token case (prompt to run Claude Code)
- [ ] Add token validation via test API call
```

**Code: auth/keychain.py**
```python
"""macOS Keychain operations for credential storage."""

import subprocess
from typing import Optional

KEYCHAIN_SERVICE = "Claude Code"


def get_api_token() -> Optional[str]:
    """Read API token from macOS Keychain."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            check=True,
        )
        token = result.stdout.strip()
        if token.startswith("sk-ant-"):
            return token
        return None
    except subprocess.CalledProcessError:
        return None


def store_api_token(token: str, account: str = "orchestrator") -> bool:
    """Store API token in macOS Keychain (for future use)."""
    try:
        subprocess.run(
            [
                "security", "add-generic-password",
                "-s", "Claude Orchestrator",
                "-a", account,
                "-w", token,
                "-U",  # Update if exists
            ],
            check=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False
```

**Deliverable:** Auth module imports and returns valid token

#### 4.2.3 Configuration System (Day 1 - 1 hour)

```bash
# Task: Create configuration system
- [ ] Define config schema (YAML)
- [ ] Create config loader with defaults
- [ ] Support environment variable overrides
- [ ] Create sample config.yaml
```

**Code: config.yaml (template)**
```yaml
# Claude Orchestrator Configuration

# tmux settings
tmux:
  session_name: orchestrator
  default_shell: /bin/zsh

# Session defaults
sessions:
  ssh_options: "-o StrictHostKeyChecking=no"
  claude_command: "claude"
  working_dir: "~"

# LLM settings
llm:
  model: "claude-sonnet-4-20250514"
  max_tokens: 4096
  temperature: 0.7

# UI settings
ui:
  confirm_actions: true
  max_output_lines: 100
  status_bar: true

# State storage
storage:
  db_path: "~/.claude-orchestrator/state.db"
  vector_db_path: "~/.claude-orchestrator/vectors"

# Logging
logging:
  level: INFO
  file: "~/.claude-orchestrator/orchestrator.log"
```

#### 4.2.4 CLI Shell (Day 1-2 - 3 hours)

```bash
# Task: Create basic CLI application
- [ ] Set up Click application
- [ ] Create main entry point
- [ ] Implement basic commands: version, config, help
- [ ] Set up Rich console for output
- [ ] Create interactive shell mode
```

**Code: cli/app.py**
```python
"""CLI application entry point."""

import click
from rich.console import Console

from orchestrator import __version__
from orchestrator.cli.chat import chat_loop

console = Console()


@click.group(invoke_without_command=True)
@click.option("--version", is_flag=True, help="Show version")
@click.pass_context
def cli(ctx, version):
    """Claude Orchestrator - Manage multiple Claude Code sessions."""
    if version:
        console.print(f"Claude Orchestrator v{__version__}")
        return
    
    if ctx.invoked_subcommand is None:
        # No subcommand - start interactive mode
        chat_loop()


@cli.command()
def config():
    """Show current configuration."""
    # TODO: Implement
    pass


@cli.command()
@click.argument("name")
@click.argument("host")
@click.option("--path", default="~", help="Working directory on remote")
def add(name, host, path):
    """Add a new remote session."""
    # TODO: Implement
    pass


if __name__ == "__main__":
    cli()
```

#### 4.2.5 Terminal Manager Basics (Day 2 - 4 hours)

```bash
# Task: Implement tmux operations
- [ ] Create terminal/manager.py with tmux wrapper
- [ ] Implement: create_session, list_sessions, kill_session
- [ ] Implement: create_window, send_keys, capture_output
- [ ] Test with local terminals first (no SSH)
- [ ] Add error handling for common failures
```

**Code: terminal/manager.py**
```python
"""tmux session management."""

import subprocess
import shlex
from dataclasses import dataclass
from typing import Optional
from pathlib import Path


@dataclass
class TmuxWindow:
    """Represents a tmux window."""
    session: str
    name: str
    index: int
    
    @property
    def target(self) -> str:
        return f"{self.session}:{self.name}"


class TerminalManager:
    """Manages tmux sessions and windows."""
    
    def __init__(self, session_name: str = "orchestrator"):
        self.session_name = session_name
        self._ensure_session()
    
    def _run(self, *args, check: bool = True) -> subprocess.CompletedProcess:
        """Run a tmux command."""
        cmd = ["tmux"] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True, check=check)
    
    def _ensure_session(self) -> None:
        """Create tmux session if it doesn't exist."""
        result = self._run("has-session", "-t", self.session_name, check=False)
        if result.returncode != 0:
            self._run("new-session", "-d", "-s", self.session_name)
    
    def create_window(self, name: str) -> TmuxWindow:
        """Create a new tmux window."""
        self._run("new-window", "-t", self.session_name, "-n", name)
        
        # Get window index
        result = self._run(
            "list-windows", "-t", self.session_name,
            "-F", "#{window_name}:#{window_index}"
        )
        for line in result.stdout.strip().split("\n"):
            wname, idx = line.split(":")
            if wname == name:
                return TmuxWindow(self.session_name, name, int(idx))
        
        raise RuntimeError(f"Failed to create window {name}")
    
    def send_keys(self, window: TmuxWindow | str, keys: str, enter: bool = True) -> None:
        """Send keystrokes to a window."""
        target = window.target if isinstance(window, TmuxWindow) else f"{self.session_name}:{window}"
        args = ["send-keys", "-t", target, keys]
        if enter:
            args.append("Enter")
        self._run(*args)
    
    def capture_output(self, window: TmuxWindow | str, lines: int = 100) -> str:
        """Capture recent output from a window."""
        target = window.target if isinstance(window, TmuxWindow) else f"{self.session_name}:{window}"
        result = self._run(
            "capture-pane", "-t", target,
            "-p",           # Print to stdout
            "-S", f"-{lines}"  # Start from N lines back
        )
        return result.stdout
    
    def list_windows(self) -> list[TmuxWindow]:
        """List all windows in the session."""
        result = self._run(
            "list-windows", "-t", self.session_name,
            "-F", "#{window_name}:#{window_index}",
            check=False
        )
        if result.returncode != 0:
            return []
        
        windows = []
        for line in result.stdout.strip().split("\n"):
            if ":" in line:
                name, idx = line.split(":")
                windows.append(TmuxWindow(self.session_name, name, int(idx)))
        return windows
    
    def kill_window(self, window: TmuxWindow | str) -> None:
        """Kill a window."""
        target = window.target if isinstance(window, TmuxWindow) else f"{self.session_name}:{window}"
        self._run("kill-window", "-t", target, check=False)
    
    def attach(self, window: Optional[TmuxWindow | str] = None) -> None:
        """Attach to the tmux session (blocking)."""
        if window:
            target = window.target if isinstance(window, TmuxWindow) else f"{self.session_name}:{window}"
            self._run("select-window", "-t", target, check=False)
        subprocess.run(["tmux", "attach", "-t", self.session_name])
```

#### 4.2.6 State Module Basics (Day 2-3 - 3 hours)

```bash
# Task: Implement SQLite state storage
- [ ] Create state/db.py with connection management
- [ ] Create state/models.py with dataclasses
- [ ] Implement schema creation (migrations.py)
- [ ] Implement basic CRUD operations
- [ ] Test with sample data
```

**Code: state/models.py**
```python
"""Data models for orchestrator state."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class SessionStatus(Enum):
    IDLE = "idle"
    WORKING = "working"
    WAITING = "waiting"
    ERROR = "error"
    DISCONNECTED = "disconnected"


class DecisionUrgency(Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class DecisionStatus(Enum):
    PENDING = "pending"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


@dataclass
class Session:
    """Represents a managed remote session."""
    id: str
    name: str
    host: str
    mp_path: Optional[str] = None
    tmux_window: Optional[str] = None
    status: SessionStatus = SessionStatus.IDLE
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: Optional[datetime] = None
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "mp_path": self.mp_path,
            "tmux_window": self.tmux_window,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
        }


@dataclass
class PullRequest:
    """Represents a PR created by a session."""
    id: str
    session_id: str
    url: str
    title: Optional[str] = None
    status: str = "open"
    created_at: datetime = field(default_factory=datetime.now)
    merged_at: Optional[datetime] = None


@dataclass
class Decision:
    """Represents a decision request from a session."""
    id: str
    session_id: str
    question: str
    context: Optional[str] = None
    urgency: DecisionUrgency = DecisionUrgency.NORMAL
    status: DecisionStatus = DecisionStatus.PENDING
    response: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    resolved_at: Optional[datetime] = None
```

**Code: state/db.py**
```python
"""SQLite database operations."""

import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
from datetime import datetime

from orchestrator.state.models import (
    Session, SessionStatus, PullRequest, 
    Decision, DecisionUrgency, DecisionStatus
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    host TEXT NOT NULL,
    mp_path TEXT,
    tmux_window TEXT,
    status TEXT DEFAULT 'idle',
    created_at TEXT NOT NULL,
    last_activity TEXT
);

CREATE TABLE IF NOT EXISTS pull_requests (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    status TEXT DEFAULT 'open',
    created_at TEXT NOT NULL,
    merged_at TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    question TEXT NOT NULL,
    context TEXT,
    urgency TEXT DEFAULT 'normal',
    status TEXT DEFAULT 'pending',
    response TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS decision_history (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    question TEXT,
    context TEXT,
    decision TEXT,
    user_feedback TEXT,
    created_at TEXT NOT NULL
);
"""


class Database:
    """SQLite database wrapper."""
    
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
    
    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
    
    def _init_schema(self):
        with self._connect() as conn:
            conn.executescript(SCHEMA)
    
    # Session operations
    
    def add_session(self, session: Session) -> Session:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO sessions 
                   (id, name, host, mp_path, tmux_window, status, created_at, last_activity)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (session.id, session.name, session.host, session.mp_path,
                 session.tmux_window, session.status.value,
                 session.created_at.isoformat(),
                 session.last_activity.isoformat() if session.last_activity else None)
            )
        return session
    
    def get_session(self, name: str) -> Optional[Session]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE name = ?", (name,)
            ).fetchone()
            if row:
                return self._row_to_session(row)
        return None
    
    def get_all_sessions(self) -> list[Session]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM sessions").fetchall()
            return [self._row_to_session(row) for row in rows]
    
    def update_session_status(self, name: str, status: SessionStatus) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET status = ?, last_activity = ? WHERE name = ?",
                (status.value, datetime.now().isoformat(), name)
            )
    
    def delete_session(self, name: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE name = ?", (name,))
    
    def _row_to_session(self, row: sqlite3.Row) -> Session:
        return Session(
            id=row["id"],
            name=row["name"],
            host=row["host"],
            mp_path=row["mp_path"],
            tmux_window=row["tmux_window"],
            status=SessionStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            last_activity=datetime.fromisoformat(row["last_activity"]) if row["last_activity"] else None,
        )
    
    # Decision operations
    
    def add_decision(self, decision: Decision) -> Decision:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO decisions 
                   (id, session_id, question, context, urgency, status, response, created_at, resolved_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (decision.id, decision.session_id, decision.question, decision.context,
                 decision.urgency.value, decision.status.value, decision.response,
                 decision.created_at.isoformat(),
                 decision.resolved_at.isoformat() if decision.resolved_at else None)
            )
        return decision
    
    def get_pending_decisions(self) -> list[Decision]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM decisions WHERE status = 'pending' ORDER BY created_at"
            ).fetchall()
            return [self._row_to_decision(row) for row in rows]
    
    def resolve_decision(self, decision_id: str, response: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE decisions 
                   SET status = 'resolved', response = ?, resolved_at = ?
                   WHERE id = ?""",
                (response, datetime.now().isoformat(), decision_id)
            )
    
    def _row_to_decision(self, row: sqlite3.Row) -> Decision:
        return Decision(
            id=row["id"],
            session_id=row["session_id"],
            question=row["question"],
            context=row["context"],
            urgency=DecisionUrgency(row["urgency"]),
            status=DecisionStatus(row["status"]),
            response=row["response"],
            created_at=datetime.fromisoformat(row["created_at"]),
            resolved_at=datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
        )
```

### 4.3 Phase 1 Deliverables

| Deliverable | Verification |
|-------------|--------------|
| Project runs | `python -m orchestrator --version` |
| Auth works | Token retrieved from Keychain |
| tmux works | Can create/list/kill windows |
| State works | Can add/query sessions in SQLite |
| CLI shell | Basic interactive prompt works |

---

## 5. Phase 2: Core Functionality (4-5 Days)

### 5.1 Goals

- Web dashboard with session grid visualization
- API endpoints for Claude Code reporting
- SSH session management (connect to rdevs)
- Claude Code startup in sessions
- LLM integration for orchestrator brain
- Passive monitoring + active reporting
- Terminal takeover capability

### 5.2 Tasks

#### 5.2.1 Web Server & Dashboard (Day 3 - 6 hours)

```bash
# Task: Create web dashboard
- [ ] Create web/server.py with Flask app
- [ ] Set up Socket.IO for real-time updates
- [ ] Create templates/index.html with session grid
- [ ] Implement static/app.js with WebSocket client
- [ ] Create REST API routes in api/routes.py
- [ ] Style dashboard with static/styles.css
```

**Code: web/server.py**
```python
"""Web dashboard server."""

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
from flask_cors import CORS

from orchestrator.state.db import Database
from orchestrator.terminal.manager import TerminalManager


def create_app(db: Database, terminal: TerminalManager) -> Flask:
    """Create and configure Flask application."""
    app = Flask(__name__, 
                template_folder='templates',
                static_folder='static')
    CORS(app)
    socketio = SocketIO(app, cors_allowed_origins="*")
    
    # Store references
    app.config['db'] = db
    app.config['terminal'] = terminal
    
    # Dashboard route
    @app.route('/')
    def dashboard():
        return render_template('index.html')
    
    # API routes
    @app.route('/api/sessions')
    def get_sessions():
        sessions = db.get_all_sessions()
        return jsonify([s.to_dict() for s in sessions])
    
    @app.route('/api/sessions/<session_id>')
    def get_session(session_id):
        session = db.get_session(session_id)
        if not session:
            return jsonify({'error': 'Session not found'}), 404
        
        # Include recent terminal output
        window = terminal.get_window(session.tmux_window)
        output = terminal.capture_output(window, lines=50) if window else ""
        
        return jsonify({
            **session.to_dict(),
            'terminal_output': output
        })
    
    @app.route('/api/sessions', methods=['POST'])
    def create_session():
        data = request.json
        # Session creation logic
        return jsonify({'status': 'created'})
    
    @app.route('/api/sessions/<session_id>/send', methods=['POST'])
    def send_to_session(session_id):
        data = request.json
        message = data.get('message', '')
        session = db.get_session(session_id)
        if not session:
            return jsonify({'error': 'Session not found'}), 404
        
        window = terminal.get_window(session.tmux_window)
        terminal.send_keys(window, message)
        
        # Broadcast update
        socketio.emit('session_updated', session.to_dict())
        return jsonify({'status': 'sent'})
    
    # Active reporting endpoints (called by Claude Code)
    @app.route('/api/report', methods=['POST'])
    def receive_report():
        """Receive status report from Claude Code instance."""
        data = request.json
        session_name = data.get('session')
        event_type = data.get('event')
        event_data = data.get('data', {})
        
        # Update session state
        session = db.get_session_by_name(session_name)
        if session:
            if event_type == 'task_progress':
                db.update_session_task(session_name, event_data)
            elif event_type == 'pr_created':
                db.add_pr(session_name, event_data)
            elif event_type == 'error':
                db.update_session_status(session_name, 'error')
            
            # Broadcast to dashboard
            socketio.emit('report_received', {
                'session': session_name,
                'event': event_type,
                'data': event_data
            })
        
        return jsonify({'status': 'received'})
    
    @app.route('/api/decision', methods=['POST'])
    def request_decision():
        """Claude Code requesting a decision from user."""
        data = request.json
        decision_id = db.add_decision(
            session=data.get('session'),
            question=data.get('question'),
            options=data.get('options'),
            context=data.get('context'),
            urgency=data.get('urgency', 'normal')
        )
        
        # Notify dashboard
        socketio.emit('decision_requested', {
            'id': decision_id,
            **data
        })
        
        return jsonify({'decision_id': decision_id, 'status': 'pending'})
    
    @app.route('/api/guidance')
    def get_guidance():
        """Claude Code checking for pending instructions."""
        session_name = request.args.get('session')
        guidance = db.get_pending_guidance(session_name)
        return jsonify(guidance or {'guidance': None})
    
    @app.route('/api/decisions')
    def get_decisions():
        decisions = db.get_pending_decisions()
        return jsonify([d.to_dict() for d in decisions])
    
    @app.route('/api/decisions/<decision_id>/respond', methods=['POST'])
    def respond_to_decision(decision_id):
        data = request.json
        response = data.get('response')
        
        decision = db.get_decision(decision_id)
        if not decision:
            return jsonify({'error': 'Decision not found'}), 404
        
        # Update decision
        db.resolve_decision(decision_id, response)
        
        # Send response to Claude Code
        session = db.get_session_by_name(decision.session)
        if session:
            window = terminal.get_window(session.tmux_window)
            terminal.send_keys(window, f"Decision from user: {response}")
        
        # Broadcast update
        socketio.emit('decision_resolved', {
            'decision_id': decision_id,
            'response': response
        })
        
        return jsonify({'status': 'responded'})
    
    # Chat API
    @app.route('/api/chat', methods=['POST'])
    def chat():
        data = request.json
        message = data.get('message')
        # Process through LLM brain
        # This will be connected in Phase 2
        return jsonify({'response': 'Chat endpoint ready'})
    
    # WebSocket events
    @socketio.on('connect')
    def handle_connect():
        emit('connected', {'status': 'connected'})
    
    @socketio.on('takeover')
    def handle_takeover(data):
        session_id = data.get('session_id')
        db.set_takeover_mode(session_id, True)
        emit('takeover_started', {'session_id': session_id})
    
    @socketio.on('release')
    def handle_release(data):
        session_id = data.get('session_id')
        db.set_takeover_mode(session_id, False)
        emit('takeover_released', {'session_id': session_id})
    
    @socketio.on('terminal_input')
    def handle_terminal_input(data):
        """Relay user input during takeover mode."""
        session_id = data.get('session_id')
        input_text = data.get('input')
        
        session = db.get_session(session_id)
        if session:
            window = terminal.get_window(session.tmux_window)
            terminal.send_keys(window, input_text)
    
    return app, socketio


def run_server(app: Flask, socketio: SocketIO, port: int = 8080):
    """Run the web server."""
    socketio.run(app, host='127.0.0.1', port=port, debug=False)
```

**Code: templates/index.html**
```html
<!DOCTYPE html>
<html>
<head>
    <title>🎭 Claude Orchestrator</title>
    <link rel="stylesheet" href="/static/styles.css">
    <script src="https://cdn.socket.io/4.6.0/socket.io.min.js"></script>
</head>
<body>
    <header>
        <h1>🎭 Claude Orchestrator</h1>
        <button id="add-session" class="btn-primary">+ New Session</button>
    </header>
    
    <main>
        <section id="session-grid" class="session-grid">
            <!-- Session cards rendered by JS -->
        </section>
        
        <section id="decisions" class="decisions-panel">
            <h2>⚠️ Decisions Requiring Approval</h2>
            <div id="decision-list">
                <!-- Decision cards rendered by JS -->
            </div>
        </section>
        
        <section id="chat" class="chat-panel">
            <h2>💬 Orchestrator Chat</h2>
            <div id="chat-messages"></div>
            <div class="chat-input">
                <input type="text" id="chat-input" placeholder="Talk to orchestrator...">
                <button id="chat-send">Send</button>
            </div>
        </section>
    </main>
    
    <!-- Session Detail Modal -->
    <div id="session-modal" class="modal hidden">
        <div class="modal-content">
            <header>
                <h2 id="modal-title">Session</h2>
                <button class="close-btn">&times;</button>
            </header>
            <div id="modal-body"></div>
        </div>
    </div>
    
    <script src="/static/app.js"></script>
</body>
</html>
```

#### 5.2.2 SSH Session Management (Day 4 - 4 hours)

```bash
# Task: Connect to remote hosts via SSH
- [ ] Extend terminal/manager.py with SSH support
- [ ] Create terminal/session.py for session lifecycle
- [ ] Implement: connect, disconnect, reconnect
- [ ] Handle SSH key authentication
- [ ] Handle connection failures gracefully
- [ ] Test with actual rdev connections
```

**Code: terminal/session.py**
```python
"""Remote session management."""

import uuid
from dataclasses import dataclass
from typing import Optional
import time

from orchestrator.terminal.manager import TerminalManager, TmuxWindow
from orchestrator.state.models import Session, SessionStatus
from orchestrator.state.db import Database


@dataclass
class RemoteSession:
    """Manages a remote Claude Code session."""
    session: Session
    window: TmuxWindow
    manager: TerminalManager
    db: Database
    
    @classmethod
    def create(
        cls,
        name: str,
        host: str,
        mp_path: Optional[str],
        manager: TerminalManager,
        db: Database,
        ssh_options: str = "",
    ) -> "RemoteSession":
        """Create and connect a new remote session."""
        # Create tmux window
        window = manager.create_window(name)
        
        # Create session record
        session = Session(
            id=str(uuid.uuid4()),
            name=name,
            host=host,
            mp_path=mp_path,
            tmux_window=window.target,
            status=SessionStatus.IDLE,
        )
        db.add_session(session)
        
        remote = cls(session, window, manager, db)
        
        # Connect via SSH
        remote.connect(ssh_options)
        
        return remote
    
    def connect(self, ssh_options: str = "") -> None:
        """SSH into the remote host."""
        ssh_cmd = f"ssh {ssh_options} {self.session.host}"
        self.manager.send_keys(self.window, ssh_cmd)
        time.sleep(2)  # Wait for connection
        
        # Change to working directory if specified
        if self.session.mp_path:
            self.manager.send_keys(self.window, f"cd {self.session.mp_path}")
            time.sleep(0.5)
        
        self.db.update_session_status(self.session.name, SessionStatus.IDLE)
    
    def start_claude(self, claude_cmd: str = "claude") -> None:
        """Start Claude Code in the session."""
        self.manager.send_keys(self.window, claude_cmd)
        time.sleep(2)  # Wait for Claude to start
        self.db.update_session_status(self.session.name, SessionStatus.WORKING)
    
    def send_message(self, message: str) -> None:
        """Send a message to Claude Code."""
        self.manager.send_keys(self.window, message)
        self.db.update_session_status(self.session.name, SessionStatus.WORKING)
    
    def get_output(self, lines: int = 100) -> str:
        """Get recent terminal output."""
        return self.manager.capture_output(self.window, lines)
    
    def disconnect(self) -> None:
        """Close the SSH connection."""
        # Send exit to Claude, then exit SSH
        self.manager.send_keys(self.window, "exit")
        time.sleep(0.5)
        self.manager.send_keys(self.window, "exit")
        self.db.update_session_status(self.session.name, SessionStatus.DISCONNECTED)
    
    def destroy(self) -> None:
        """Completely remove the session."""
        self.disconnect()
        self.manager.kill_window(self.window)
        self.db.delete_session(self.session.name)
```

#### 5.2.2 LLM Client (Day 4 - 3 hours)

```bash
# Task: Implement Anthropic API client
- [ ] Create llm/client.py with API wrapper
- [ ] Support both SDK and curl methods
- [ ] Handle rate limits and errors
- [ ] Implement streaming responses (optional)
- [ ] Add token usage tracking
```

**Code: llm/client.py**
```python
"""Anthropic API client using curl (no dependencies)."""

import subprocess
import json
from dataclasses import dataclass
from typing import Optional

from orchestrator.auth.keychain import get_api_token


@dataclass
class Message:
    role: str
    content: str


@dataclass
class LLMResponse:
    content: str
    model: str
    input_tokens: int
    output_tokens: int
    

class LLMClient:
    """Anthropic API client using curl."""
    
    API_URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"
    
    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 4096,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self._token: Optional[str] = None
    
    @property
    def token(self) -> str:
        if not self._token:
            self._token = get_api_token()
            if not self._token:
                raise RuntimeError(
                    "No API token found. Please log into Claude Code first:\n"
                    "  $ claude\n"
                    "  Then run /login and select 'Console account'"
                )
        return self._token
    
    def chat(
        self,
        messages: list[Message],
        system: Optional[str] = None,
    ) -> LLMResponse:
        """Send a chat request to Claude."""
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if system:
            payload["system"] = system
        
        result = subprocess.run(
            [
                "curl", "-s",
                self.API_URL,
                "-H", f"x-api-key: {self.token}",
                "-H", f"anthropic-version: {self.API_VERSION}",
                "-H", "content-type: application/json",
                "-d", json.dumps(payload),
            ],
            capture_output=True,
            text=True,
        )
        
        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(f"Invalid API response: {result.stdout[:200]}")
        
        if "error" in response:
            error_msg = response["error"].get("message", str(response["error"]))
            raise RuntimeError(f"API error: {error_msg}")
        
        return LLMResponse(
            content=response["content"][0]["text"],
            model=response["model"],
            input_tokens=response["usage"]["input_tokens"],
            output_tokens=response["usage"]["output_tokens"],
        )
    
    def simple_query(self, prompt: str, system: Optional[str] = None) -> str:
        """Simple single-turn query."""
        response = self.chat([Message("user", prompt)], system)
        return response.content
```

#### 5.2.3 Context Builder (Day 4-5 - 4 hours)

```bash
# Task: Build context from current state
- [ ] Create llm/context.py
- [ ] Gather session statuses
- [ ] Capture terminal outputs
- [ ] Include pending decisions
- [ ] Format context for LLM consumption
- [ ] Implement context size limits
```

**Code: llm/context.py**
```python
"""Context building for LLM queries."""

from dataclasses import dataclass
from typing import Optional

from orchestrator.state.db import Database
from orchestrator.state.models import Session, Decision
from orchestrator.terminal.manager import TerminalManager


@dataclass
class SessionContext:
    """Context gathered from a single session."""
    session: Session
    recent_output: str
    
    def format(self, max_output_chars: int = 2000) -> str:
        output = self.recent_output[-max_output_chars:] if self.recent_output else "(no output)"
        return f"""### Session: {self.session.name}
- Host: {self.session.host}
- Status: {self.session.status.value}
- Path: {self.session.mp_path or "~"}

Recent output:
```
{output}
```
"""


class ContextBuilder:
    """Builds context for LLM from current state."""
    
    def __init__(self, db: Database, terminal_manager: TerminalManager):
        self.db = db
        self.terminal_manager = terminal_manager
    
    def build_full_context(
        self,
        include_outputs: bool = True,
        output_lines: int = 50,
        max_output_chars: int = 2000,
    ) -> str:
        """Build complete context from all sessions."""
        sections = []
        
        # Header
        sessions = self.db.get_all_sessions()
        decisions = self.db.get_pending_decisions()
        
        sections.append(f"## Current State")
        sections.append(f"- Active sessions: {len(sessions)}")
        sections.append(f"- Pending decisions: {len(decisions)}")
        sections.append("")
        
        # Sessions
        if sessions:
            sections.append("## Sessions")
            for session in sessions:
                output = ""
                if include_outputs and session.tmux_window:
                    try:
                        output = self.terminal_manager.capture_output(
                            session.tmux_window.split(":")[-1],
                            output_lines
                        )
                    except Exception:
                        output = "(failed to capture output)"
                
                ctx = SessionContext(session, output)
                sections.append(ctx.format(max_output_chars))
        else:
            sections.append("## Sessions\n(no active sessions)")
        
        # Pending decisions
        if decisions:
            sections.append("## Pending Decisions")
            for d in decisions:
                sections.append(f"- **[{d.urgency.value.upper()}]** {d.session_id}: {d.question}")
                if d.context:
                    sections.append(f"  Context: {d.context[:200]}...")
            sections.append("")
        
        return "\n".join(sections)
    
    def build_session_context(
        self,
        session_name: str,
        output_lines: int = 100,
    ) -> Optional[str]:
        """Build context for a specific session."""
        session = self.db.get_session(session_name)
        if not session:
            return None
        
        output = ""
        if session.tmux_window:
            try:
                output = self.terminal_manager.capture_output(
                    session.tmux_window.split(":")[-1],
                    output_lines
                )
            except Exception:
                output = "(failed to capture output)"
        
        return SessionContext(session, output).format(max_output_chars=5000)
```

#### 5.2.4 Passive Monitor (Day 5 - 3 hours)

```bash
# Task: Background monitoring of terminal outputs
- [ ] Create terminal/monitor.py
- [ ] Poll all sessions every 5 seconds
- [ ] Detect state changes (working → idle, errors)
- [ ] Broadcast updates via WebSocket
- [ ] Detect when Claude is waiting for user input
- [ ] Run as background thread
```

**Code: terminal/monitor.py**
```python
"""Passive terminal monitoring."""

import threading
import time
import re
from typing import Callable, Optional

from orchestrator.state.db import Database
from orchestrator.state.models import SessionStatus
from orchestrator.terminal.manager import TerminalManager


class PassiveMonitor:
    """Background monitor that polls terminal outputs."""
    
    POLL_INTERVAL = 5  # seconds
    
    # Patterns to detect state changes
    PATTERNS = {
        'idle': [
            r'claude>?\s*$',           # Claude prompt waiting
            r'\$\s*$',                  # Shell prompt
            r'waiting for input',
        ],
        'error': [
            r'error:',
            r'Error:',
            r'failed',
            r'FAILED',
            r'exception',
            r'Traceback',
        ],
        'pr_created': [
            r'Created pull request.*#(\d+)',
            r'PR #(\d+) created',
            r'https://github\.com/.*/pull/(\d+)',
        ],
        'task_complete': [
            r'Task completed',
            r'All tests passed',
            r'Done!',
        ],
        'waiting_decision': [
            r'Should I',
            r'Which option',
            r'Do you want me to',
            r'Please choose',
        ],
    }
    
    def __init__(
        self, 
        db: Database, 
        terminal: TerminalManager,
        on_update: Optional[Callable] = None,
    ):
        self.db = db
        self.terminal = terminal
        self.on_update = on_update  # Callback for WebSocket broadcast
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_outputs: dict[str, str] = {}  # Track changes
    
    def start(self):
        """Start the monitoring thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
    
    def stop(self):
        """Stop the monitoring thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
    
    def _monitor_loop(self):
        """Main monitoring loop."""
        while self._running:
            try:
                self._check_all_sessions()
            except Exception as e:
                print(f"Monitor error: {e}")
            time.sleep(self.POLL_INTERVAL)
    
    def _check_all_sessions(self):
        """Check all active sessions."""
        sessions = self.db.get_all_sessions()
        
        for session in sessions:
            if session.status == SessionStatus.DISCONNECTED:
                continue
            if session.takeover_mode:
                continue  # Don't monitor during takeover
            
            self._check_session(session)
    
    def _check_session(self, session):
        """Check a single session for state changes."""
        try:
            window = self.terminal.get_window(session.tmux_window)
            if not window:
                self.db.update_session_status(session.name, SessionStatus.DISCONNECTED)
                self._emit_update(session.name, 'disconnected')
                return
            
            output = self.terminal.capture_output(window, lines=30)
            last_output = self._last_outputs.get(session.name, "")
            
            # Check if output changed
            if output == last_output:
                return
            
            self._last_outputs[session.name] = output
            new_output = output[len(last_output):] if output.startswith(last_output) else output
            
            # Detect state from new output
            new_status = self._detect_status(new_output, session.status)
            
            if new_status != session.status:
                self.db.update_session_status(session.name, new_status)
                self._emit_update(session.name, new_status.value, new_output)
            
            # Check for specific events
            self._check_for_events(session.name, new_output)
            
        except Exception as e:
            print(f"Error checking session {session.name}: {e}")
    
    def _detect_status(self, output: str, current: SessionStatus) -> SessionStatus:
        """Detect session status from output."""
        # Check for errors
        for pattern in self.PATTERNS['error']:
            if re.search(pattern, output, re.IGNORECASE):
                return SessionStatus.ERROR
        
        # Check for idle (prompt visible, no activity)
        for pattern in self.PATTERNS['idle']:
            if re.search(pattern, output.strip()[-100:]):
                return SessionStatus.IDLE
        
        # Check for waiting on decision
        for pattern in self.PATTERNS['waiting_decision']:
            if re.search(pattern, output, re.IGNORECASE):
                return SessionStatus.WAITING
        
        # If there's new output and not idle, probably working
        if output.strip():
            return SessionStatus.WORKING
        
        return current
    
    def _check_for_events(self, session_name: str, output: str):
        """Check for specific events like PR creation."""
        # Check for PR creation
        for pattern in self.PATTERNS['pr_created']:
            match = re.search(pattern, output)
            if match:
                pr_number = match.group(1)
                self._emit_update(session_name, 'pr_detected', {'pr': pr_number})
                break
        
        # Check for task completion
        for pattern in self.PATTERNS['task_complete']:
            if re.search(pattern, output, re.IGNORECASE):
                self._emit_update(session_name, 'task_complete')
                break
    
    def _emit_update(self, session_name: str, event: str, data: any = None):
        """Emit update via callback (for WebSocket)."""
        if self.on_update:
            self.on_update({
                'session': session_name,
                'event': event,
                'data': data,
                'timestamp': time.time()
            })
```

#### 5.2.5 Action Parser (Day 5 - 3 hours)

```bash
# Task: Parse actions from LLM responses
- [ ] Create llm/actions.py
- [ ] Define action types: SEND, CHECK, APPROVE, WAIT
- [ ] Parse structured commands from text
- [ ] Validate actions before execution
- [ ] Handle malformed actions gracefully
```

**Code: llm/actions.py**
```python
"""Parse and execute actions from LLM responses."""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ActionType(Enum):
    SEND = "SEND"       # Send message to session
    CHECK = "CHECK"     # Get output from session
    APPROVE = "APPROVE" # Approve a PR
    WAIT = "WAIT"       # Pause a session
    RESUME = "RESUME"   # Resume a session


@dataclass
class Action:
    """A parsed action from LLM response."""
    type: ActionType
    target: str
    payload: Optional[str] = None
    
    def __str__(self):
        if self.payload:
            return f"[{self.type.value}:{self.target}] {self.payload}"
        return f"[{self.type.value}:{self.target}]"


class ActionParser:
    """Parses structured actions from LLM responses."""
    
    # Pattern: [ACTION:target] optional_payload
    PATTERN = re.compile(
        r'\[(' + '|'.join(a.value for a in ActionType) + r'):(\w+)\]\s*(.*?)(?=\[|$)',
        re.DOTALL | re.IGNORECASE
    )
    
    def parse(self, text: str) -> list[Action]:
        """Extract all actions from text."""
        actions = []
        
        for match in self.PATTERN.finditer(text):
            action_type = match.group(1).upper()
            target = match.group(2)
            payload = match.group(3).strip() if match.group(3) else None
            
            try:
                actions.append(Action(
                    type=ActionType(action_type),
                    target=target,
                    payload=payload if payload else None,
                ))
            except ValueError:
                continue  # Skip invalid action types
        
        return actions
    
    def has_actions(self, text: str) -> bool:
        """Check if text contains any actions."""
        return bool(self.PATTERN.search(text))
```

#### 5.2.5 Interactive Chat Loop (Day 5-6 - 4 hours)

```bash
# Task: Complete interactive chat implementation
- [ ] Create cli/chat.py with main loop
- [ ] Integrate context builder
- [ ] Integrate action parser
- [ ] Implement action confirmation
- [ ] Handle slash commands
- [ ] Add conversation history
```

**Code: cli/chat.py**
```python
"""Interactive chat loop."""

from rich.console import Console
from rich.prompt import Prompt
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown

from orchestrator.auth.manager import AuthManager
from orchestrator.terminal.manager import TerminalManager
from orchestrator.terminal.session import RemoteSession
from orchestrator.state.db import Database
from orchestrator.llm.client import LLMClient, Message
from orchestrator.llm.context import ContextBuilder
from orchestrator.llm.actions import ActionParser, Action, ActionType


SYSTEM_PROMPT = """You are an orchestrator managing multiple Claude Code sessions 
across different remote development environments. You help the user:

1. Check status of ongoing work across all sessions
2. Route messages and tasks to specific workers
3. Make decisions about which PRs to approve/merge
4. Coordinate complex multi-repo changes

When you need to take action on a session, output structured commands:
- [SEND:session_name] message to send to that session
- [CHECK:session_name] to get latest output from a session
- [WAIT:session_name] to pause a session
- [RESUME:session_name] to resume a paused session

Always explain your reasoning before suggesting actions.
Be concise but informative in your status summaries."""


class ChatLoop:
    """Main interactive chat loop."""
    
    def __init__(self, config: dict):
        self.config = config
        self.console = Console()
        
        # Initialize components
        db_path = config.get("storage", {}).get("db_path", "~/.claude-orchestrator/state.db")
        self.db = Database(db_path)
        self.terminal_manager = TerminalManager(
            config.get("tmux", {}).get("session_name", "orchestrator")
        )
        self.context_builder = ContextBuilder(self.db, self.terminal_manager)
        self.action_parser = ActionParser()
        
        # LLM setup
        llm_config = config.get("llm", {})
        self.llm = LLMClient(
            model=llm_config.get("model", "claude-sonnet-4-20250514"),
            max_tokens=llm_config.get("max_tokens", 4096),
        )
        
        # Conversation state
        self.messages: list[Message] = []
        self.sessions: dict[str, RemoteSession] = {}
    
    def run(self):
        """Main loop."""
        self._print_welcome()
        
        while True:
            try:
                user_input = Prompt.ask("\n[bold blue]You[/]")
                
                if not user_input.strip():
                    continue
                
                # Handle exit
                if user_input.lower() in ["exit", "quit", "/exit", "/quit"]:
                    self.console.print("[dim]Goodbye![/]")
                    break
                
                # Handle slash commands
                if user_input.startswith("/"):
                    self._handle_command(user_input)
                    continue
                
                # Regular chat
                self._chat(user_input)
                
            except KeyboardInterrupt:
                self.console.print("\n[dim]Use /exit to quit[/]")
            except Exception as e:
                self.console.print(f"[red]Error: {e}[/]")
    
    def _print_welcome(self):
        """Print welcome message."""
        sessions = self.db.get_all_sessions()
        decisions = self.db.get_pending_decisions()
        
        self.console.print(Panel(
            f"[bold]Claude Orchestrator[/]\n\n"
            f"📊 Sessions: {len(sessions)}\n"
            f"⚠️  Pending decisions: {len(decisions)}\n\n"
            f"[dim]Type /help for commands, or just chat![/]",
            title="Welcome",
            border_style="blue"
        ))
    
    def _chat(self, user_input: str):
        """Process a chat message."""
        # Build context
        context = self.context_builder.build_full_context()
        
        # Augment user message with context
        augmented = f"{context}\n\n---\n\nUser request: {user_input}"
        self.messages.append(Message("user", augmented))
        
        # Call LLM
        self.console.print("[dim]Thinking...[/]")
        response = self.llm.chat(self.messages, system=SYSTEM_PROMPT)
        
        # Store assistant response (without context for history)
        self.messages.append(Message("assistant", response.content))
        
        # Display response
        self.console.print(f"\n[bold green]Orchestrator[/]:\n")
        self.console.print(Markdown(response.content))
        
        # Check for actions
        actions = self.action_parser.parse(response.content)
        if actions:
            self._handle_actions(actions)
    
    def _handle_actions(self, actions: list[Action]):
        """Execute parsed actions with confirmation."""
        self.console.print(f"\n[yellow]Actions detected ({len(actions)}):[/]")
        
        for action in actions:
            self.console.print(f"  • {action}")
        
        if self.config.get("ui", {}).get("confirm_actions", True):
            confirm = Prompt.ask(
                "\n[yellow]Execute these actions?[/]",
                choices=["y", "n", "s"],  # yes, no, skip
                default="y"
            )
            
            if confirm == "n":
                self.console.print("[dim]Actions cancelled[/]")
                return
            elif confirm == "s":
                # Skip - let user select which to execute
                self._selective_execute(actions)
                return
        
        self._execute_actions(actions)
    
    def _execute_actions(self, actions: list[Action]):
        """Execute a list of actions."""
        for action in actions:
            try:
                if action.type == ActionType.SEND:
                    session = self.db.get_session(action.target)
                    if session and session.tmux_window:
                        self.terminal_manager.send_keys(
                            session.tmux_window.split(":")[-1],
                            action.payload or ""
                        )
                        self.console.print(f"[green]✓[/] Sent to {action.target}")
                    else:
                        self.console.print(f"[red]✗[/] Session {action.target} not found")
                
                elif action.type == ActionType.CHECK:
                    ctx = self.context_builder.build_session_context(action.target)
                    if ctx:
                        self.console.print(Panel(ctx, title=f"Output: {action.target}"))
                    else:
                        self.console.print(f"[red]✗[/] Session {action.target} not found")
                        
            except Exception as e:
                self.console.print(f"[red]✗[/] Action failed: {e}")
    
    def _selective_execute(self, actions: list[Action]):
        """Let user select which actions to execute."""
        for i, action in enumerate(actions):
            confirm = Prompt.ask(
                f"  [{i+1}] {action}",
                choices=["y", "n"],
                default="y"
            )
            if confirm == "y":
                self._execute_actions([action])
    
    def _handle_command(self, cmd: str):
        """Handle slash commands."""
        parts = cmd.split()
        command = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []
        
        if command == "/help":
            self._show_help()
        elif command == "/list":
            self._list_sessions()
        elif command == "/add" and len(args) >= 2:
            self._add_session(args[0], args[1], args[2] if len(args) > 2 else None)
        elif command == "/remove" and args:
            self._remove_session(args[0])
        elif command == "/status":
            self._show_status(args[0] if args else None)
        elif command == "/output" and args:
            lines = int(args[1]) if len(args) > 1 else 50
            self._show_output(args[0], lines)
        elif command == "/attach" and args:
            self._attach_session(args[0])
        elif command == "/decisions":
            self._show_decisions()
        else:
            self.console.print(f"[red]Unknown command: {command}[/]")
            self._show_help()
    
    def _show_help(self):
        """Show available commands."""
        help_text = """
**Available Commands:**

| Command | Description |
|---------|-------------|
| `/list` | List all sessions |
| `/add <name> <host> [path]` | Add new session |
| `/remove <name>` | Remove session |
| `/status [name]` | Show status |
| `/output <name> [lines]` | Show terminal output |
| `/attach <name>` | Attach to tmux session |
| `/decisions` | Show pending decisions |
| `/help` | Show this help |
| `/exit` | Exit orchestrator |
"""
        self.console.print(Markdown(help_text))
    
    def _list_sessions(self):
        """List all sessions in a table."""
        sessions = self.db.get_all_sessions()
        
        if not sessions:
            self.console.print("[dim]No sessions. Use /add to create one.[/]")
            return
        
        table = Table(title="Sessions")
        table.add_column("Name", style="cyan")
        table.add_column("Host")
        table.add_column("Status")
        table.add_column("Path")
        
        for s in sessions:
            status_style = {
                "idle": "blue",
                "working": "green",
                "waiting": "yellow",
                "error": "red",
                "disconnected": "dim",
            }.get(s.status.value, "white")
            
            table.add_row(
                s.name,
                s.host,
                f"[{status_style}]{s.status.value}[/]",
                s.mp_path or "~"
            )
        
        self.console.print(table)
    
    def _add_session(self, name: str, host: str, path: str | None):
        """Add a new remote session."""
        try:
            session = RemoteSession.create(
                name=name,
                host=host,
                mp_path=path,
                manager=self.terminal_manager,
                db=self.db,
            )
            self.sessions[name] = session
            self.console.print(f"[green]✓[/] Session '{name}' created")
            
            # Ask about starting Claude
            if Prompt.ask("Start Claude Code?", choices=["y", "n"], default="y") == "y":
                session.start_claude()
                self.console.print(f"[green]✓[/] Claude Code started")
                
        except Exception as e:
            self.console.print(f"[red]✗[/] Failed to create session: {e}")
    
    def _remove_session(self, name: str):
        """Remove a session."""
        session = self.db.get_session(name)
        if not session:
            self.console.print(f"[red]Session '{name}' not found[/]")
            return
        
        if name in self.sessions:
            self.sessions[name].destroy()
            del self.sessions[name]
        else:
            self.db.delete_session(name)
            if session.tmux_window:
                self.terminal_manager.kill_window(session.tmux_window.split(":")[-1])
        
        self.console.print(f"[green]✓[/] Session '{name}' removed")
    
    def _show_status(self, name: str | None):
        """Show status of one or all sessions."""
        if name:
            ctx = self.context_builder.build_session_context(name)
            if ctx:
                self.console.print(Markdown(ctx))
            else:
                self.console.print(f"[red]Session '{name}' not found[/]")
        else:
            ctx = self.context_builder.build_full_context(include_outputs=False)
            self.console.print(Markdown(ctx))
    
    def _show_output(self, name: str, lines: int):
        """Show terminal output for a session."""
        session = self.db.get_session(name)
        if not session or not session.tmux_window:
            self.console.print(f"[red]Session '{name}' not found[/]")
            return
        
        output = self.terminal_manager.capture_output(
            session.tmux_window.split(":")[-1],
            lines
        )
        self.console.print(Panel(output, title=f"Output: {name}"))
    
    def _attach_session(self, name: str):
        """Attach to a tmux session."""
        session = self.db.get_session(name)
        if not session or not session.tmux_window:
            self.console.print(f"[red]Session '{name}' not found[/]")
            return
        
        self.console.print("[dim]Attaching to tmux... (Ctrl+B D to detach)[/]")
        self.terminal_manager.attach(session.tmux_window.split(":")[-1])
    
    def _show_decisions(self):
        """Show pending decisions."""
        decisions = self.db.get_pending_decisions()
        
        if not decisions:
            self.console.print("[dim]No pending decisions.[/]")
            return
        
        table = Table(title="Pending Decisions")
        table.add_column("Session", style="cyan")
        table.add_column("Urgency")
        table.add_column("Question")
        
        for d in decisions:
            urgency_style = {
                "low": "dim",
                "normal": "white",
                "high": "red bold",
            }.get(d.urgency.value, "white")
            
            table.add_row(
                d.session_id,
                f"[{urgency_style}]{d.urgency.value}[/]",
                d.question[:60] + "..." if len(d.question) > 60 else d.question
            )
        
        self.console.print(table)


def chat_loop():
    """Entry point for chat loop."""
    # Load config
    import yaml
    from pathlib import Path
    
    config_path = Path("config.yaml")
    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}
    
    loop = ChatLoop(config)
    loop.run()
```

### 5.3 Phase 2 Deliverables

| Deliverable | Verification |
|-------------|--------------|
| SSH sessions work | Can connect to rdev |
| Claude starts | Claude Code runs in remote session |
| LLM works | Orchestrator responds intelligently |
| Context building | Status includes terminal output |
| Actions work | Can send commands to sessions |

---

## 6. Phase 3: Intelligence (3-4 Days)

### 6.1 Goals

- Learning from user decisions
- Vector storage for RAG
- Improved context relevance
- Pattern recognition

### 6.2 Tasks

#### 6.2.1 Vector Store Setup (Day 7 - 3 hours)

```bash
# Task: Set up ChromaDB for embeddings
- [ ] Create knowledge/vectors.py
- [ ] Implement document storage
- [ ] Implement similarity search
- [ ] Test with sample decisions
```

#### 6.2.2 Learning Engine (Day 7-8 - 4 hours)

```bash
# Task: Track and learn from decisions
- [ ] Create knowledge/learning.py
- [ ] Store decision context + outcome
- [ ] Compute embeddings for decisions
- [ ] Retrieve similar past decisions
- [ ] Include in LLM context
```

#### 6.2.3 Enhanced Context (Day 8 - 3 hours)

```bash
# Task: Improve context quality
- [ ] Add relevant past decisions to context
- [ ] Summarize long terminal outputs
- [ ] Prioritize recent/important information
- [ ] Handle context size limits
```

### 6.3 Phase 3 Deliverables

| Deliverable | Verification |
|-------------|--------------|
| Decisions stored | Decision history in DB |
| Embeddings work | Can query similar decisions |
| Learning visible | Past decisions influence responses |

---

## 7. Phase 4: Polish & Visualization (3-4 Days)

### 7.1 Goals

- Beautiful CLI output
- Optional web dashboard
- Error handling improvements
- Documentation

### 7.2 Tasks

#### 7.2.1 CLI Polish (Day 9 - 4 hours)

```bash
# Task: Improve CLI experience
- [ ] Add progress indicators
- [ ] Improve table formatting
- [ ] Add color coding by status
- [ ] Implement status bar
- [ ] Add command completion
```

#### 7.2.2 Web Dashboard (Day 9-10 - 6 hours) [OPTIONAL]

```bash
# Task: Build simple web dashboard
- [ ] Set up FastAPI server
- [ ] Create REST endpoints
- [ ] Build React frontend (or use HTMX)
- [ ] Implement real-time updates
```

#### 7.2.3 Documentation (Day 10 - 3 hours)

```bash
# Task: Write documentation
- [ ] Complete README.md
- [ ] Write usage guide
- [ ] Document configuration options
- [ ] Add troubleshooting guide
```

### 7.3 Phase 4 Deliverables

| Deliverable | Verification |
|-------------|--------------|
| CLI polished | Beautiful output, status bar |
| Dashboard (opt) | Web UI works |
| Documentation | README complete |

---

## 8. Detailed Module Specifications

### 8.1 Module: auth

| File | Purpose | Key Functions |
|------|---------|---------------|
| `keychain.py` | macOS Keychain access | `get_api_token()`, `store_api_token()` |
| `manager.py` | Auth orchestration | `get_token()`, `validate_token()`, `trigger_oauth()` |

### 8.2 Module: terminal

| File | Purpose | Key Functions |
|------|---------|---------------|
| `manager.py` | tmux operations | `create_window()`, `send_keys()`, `capture_output()` |
| `session.py` | Remote session lifecycle | `create()`, `connect()`, `start_claude()`, `destroy()` |
| `output.py` | Output parsing | `parse_pr_url()`, `parse_error()`, `summarize()` |

### 8.3 Module: state

| File | Purpose | Key Functions |
|------|---------|---------------|
| `db.py` | SQLite operations | `add_session()`, `get_pending_decisions()` |
| `models.py` | Data models | `Session`, `Decision`, `PullRequest` |
| `migrations.py` | Schema management | `migrate()` |

### 8.4 Module: llm

| File | Purpose | Key Functions |
|------|---------|---------------|
| `client.py` | API client | `chat()`, `simple_query()` |
| `context.py` | Context building | `build_full_context()`, `build_session_context()` |
| `actions.py` | Action parsing | `parse()`, `has_actions()` |

### 8.5 Module: cli

| File | Purpose | Key Functions |
|------|---------|---------------|
| `app.py` | CLI entry point | `cli()` (Click group) |
| `chat.py` | Interactive loop | `ChatLoop.run()` |
| `commands.py` | Command handlers | `/add`, `/list`, `/status` |
| `display.py` | Formatting | `format_table()`, `format_status()` |

### 8.6 Module: knowledge

| File | Purpose | Key Functions |
|------|---------|---------------|
| `vectors.py` | ChromaDB wrapper | `store()`, `search()` |
| `learning.py` | Pattern learning | `record_decision()`, `get_similar()` |

---

## 9. Testing Strategy

### 9.1 Test Categories

| Category | Coverage Target | Tools |
|----------|-----------------|-------|
| Unit tests | 70% | pytest |
| Integration tests | Key flows | pytest + fixtures |
| Manual testing | Full flows | Checklist |

### 9.2 Key Test Cases

```python
# tests/test_terminal.py

def test_create_window():
    """Test tmux window creation."""
    manager = TerminalManager("test-session")
    window = manager.create_window("test-window")
    assert window.name == "test-window"
    manager.kill_window(window)


def test_send_and_capture():
    """Test sending keys and capturing output."""
    manager = TerminalManager("test-session")
    window = manager.create_window("test-echo")
    
    manager.send_keys(window, "echo 'hello orchestrator'")
    time.sleep(0.5)
    
    output = manager.capture_output(window, 10)
    assert "hello orchestrator" in output
    
    manager.kill_window(window)
```

### 9.3 Test Fixtures

```python
# tests/conftest.py

import pytest
from orchestrator.terminal.manager import TerminalManager
from orchestrator.state.db import Database

@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary database."""
    db_path = tmp_path / "test.db"
    return Database(db_path)


@pytest.fixture
def terminal_manager():
    """Create a test tmux session."""
    manager = TerminalManager("test-orchestrator")
    yield manager
    # Cleanup
    import subprocess
    subprocess.run(["tmux", "kill-session", "-t", "test-orchestrator"], 
                   capture_output=True)
```

---

## 10. Deployment

### 10.1 Installation

```bash
# Clone or create project
cd ~/projects
mkdir claude-orchestrator && cd claude-orchestrator

# Create virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e .

# Create config
cp config.example.yaml config.yaml
# Edit config.yaml as needed

# Run
python -m orchestrator
```

### 10.2 Configuration

```bash
# Location: ~/projects/claude-orchestrator/config.yaml
# Or: ~/.claude-orchestrator/config.yaml

# Minimum required configuration:
tmux:
  session_name: orchestrator

storage:
  db_path: ~/.claude-orchestrator/state.db
```

### 10.3 Running

```bash
# Start orchestrator
orchestrator

# Or with Python
python -m orchestrator

# Specific commands
orchestrator add voyager rdev-voyager.host /src/voyager-web
orchestrator list
orchestrator status
```

---

## 11. Timeline Summary

| Phase | Duration | Focus | Key Deliverables |
|-------|----------|-------|------------------|
| **Phase 1** | 3-4 days | Foundation | Auth, tmux, state, CLI shell |
| **Phase 2** | 4-5 days | Core | SSH sessions, LLM, actions |
| **Phase 3** | 3-4 days | Intelligence | Learning, vectors, RAG |
| **Phase 4** | 3-4 days | Polish | UI, dashboard, docs |

**Total: 13-17 days** for full implementation

### Minimum Viable Product (MVP)

For a working MVP, focus on:
- Phase 1: Full
- Phase 2: Full
- Phase 3: Skip (no learning)
- Phase 4: CLI polish only

**MVP Timeline: 7-9 days**

---

## Appendix A: Quick Start Commands

```bash
# Day 1: Setup
mkdir -p orchestrator/{auth,terminal,state,llm,cli,knowledge}
touch orchestrator/__init__.py orchestrator/__main__.py

# Test existing auth
python scripts/test_auth.py

# Day 2: Test tmux
tmux new-session -d -s test
tmux send-keys -t test "echo hello" Enter
tmux capture-pane -t test -p

# Day 3: Test SSH
ssh rdev-host.example.com "echo connected"
```

---

## Appendix B: Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| No token found | Not logged into Claude Code | Run `claude` and `/login` |
| tmux not found | Not installed | `brew install tmux` |
| SSH timeout | Network/VPN issue | Check VPN connection |
| Permission denied | SSH key not loaded | `ssh-add ~/.ssh/id_rsa` |

---

*Document Version History*

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-07 | Yu Qiu | Initial draft |

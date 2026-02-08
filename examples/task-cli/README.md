# Task CLI — Orchestrator Dogfooding Project

Simple Python CLI task manager used for testing the Claude Orchestrator
with real multi-session workflows.

## Dogfooding Scenario

1. Start the orchestrator (`python -m orchestrator.main`)
2. Create 2 sessions (worker-1, worker-2) via the dashboard
3. Assign tasks:
   - Worker 1: Add `--priority` flag and `task priority <id> <level>` command
   - Worker 2: Add `task stats` command showing counts by status
4. Monitor progress in the dashboard terminal views
5. Approve decisions, send guidance through the UI
6. Verify both workers' changes work together

## Usage

```bash
python task_cli.py add "Buy groceries"
python task_cli.py list
python task_cli.py done 1
python task_cli.py search "groceries"
```

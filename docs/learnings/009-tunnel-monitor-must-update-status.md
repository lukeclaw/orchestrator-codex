# Tunnel Monitor Must Update Session Status

**Date**: 2026-03-16
**Commit**: ec9c45e
**Area**: `session/tunnel_monitor.py` -- `_check_all_tunnels`

## Symptom

After VPN disconnect, remote workers stay in their original status (working/waiting/idle) on the UI for up to 5 minutes, even though the server logs show tunnel failures within 60 seconds.

## Root Cause

The tunnel monitor runs every 60s and detected dead tunnels promptly. It tried to restart them, and when restart failed, it logged a warning but **did not update the session status**. The comment said "tunnel health != worker health" and deferred to the health check.

The health check (the only thing that updated status) ran every 5 minutes via `setInterval(healthCheck, 300000)` in the frontend.

Result: the server knew the worker was unreachable within 60s, but the UI didn't reflect it for up to 5 minutes.

## Fix

When `_restart_tunnel` returns 0 (failure), `_check_all_tunnels` now:
1. Sets session status to "disconnected" in the DB
2. Publishes `session.status_changed` events so the UI updates via WebSocket

The health check's auto-reconnect picks up the "disconnected" status and handles recovery when connectivity returns.

## Rule

**The component that detects a failure should propagate it.** Don't defer status updates to a slower polling loop when you already have the signal. The tunnel monitor had the real-time information but was suppressing it.

The original reasoning ("tunnel health != worker health") was technically correct -- a dead tunnel doesn't mean the worker is dead. But it does mean the worker is **unreachable**, which is what "disconnected" means. The distinction between "worker process died" and "worker is unreachable" doesn't matter to the user -- both should show "disconnected".

## Related

- The health check interval (5 min) is intentionally long to avoid overloading remote hosts with SSH probes. Making the tunnel monitor propagate status is better than shortening the health check interval.
- The `_check_all_tunnels` loop already skips sessions in "disconnected"/"connecting"/"error" status, so marking disconnected here doesn't create a fight with the tunnel monitor's own loop.

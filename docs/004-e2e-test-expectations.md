---
title: "E2E Test Screenshot Expectations"
author: Yudong Qiu
created: 2026-02-07
last_modified: 2026-02-07
status: Active
---

# E2E Test Screenshot Expectations

This document describes the expected visual state of each screenshot captured during E2E testing.

## Seed Data

| Entity | Details |
|--------|---------|
| Sessions | worker-alpha (working, localhost, /src/project-a), worker-beta (idle, localhost, /src/project-b), worker-gamma (disconnected, rdev1.example.com) |
| Decisions | d1: "Should we refactor the auth module..." (HIGH, options: Yes/No), d2: "PR #42 has merge conflicts..." (CRITICAL, context about conflicts) |
| Activities | task.started (OAuth flow), pr.created (#42), session.connected (localhost) |
| Cost Events | worker-alpha: $0.0475 (2 events), worker-beta: $0.0225 (1 event), total $0.0700 |

---

## 01_dashboard_loads.png

**Layout:**
- Dark background (#0d1117) fills the entire viewport
- Header bar at top: "Claude Orchestrator" title (white, bold), `tmux attach -t orchestrator` code block (monospace, dark bg), "Connected" badge (green bg, right-aligned)
- Stats bar: 4 equal-width boxes in a row with border (#30363d)
- Two 2-column grid rows below stats
- Full-width Cost & Budget panel at bottom

**Data:**
- Stats: "2" Active Sessions, "2" Pending Decisions, "0" Open PRs, "$0.07" Today's Cost
- Stats values in accent blue (#58a6ff), labels in uppercase dim gray

**Absence:**
- No loading spinners
- No "Loading..." placeholder text
- No console errors

---

## 02_session_cards.png

**Layout:**
- Sessions panel on the left with "Sessions" header and "+ Add Session" button (blue outline)
- 3 session cards stacked vertically, each with left border color coding

**Colors (left borders):**
- worker-alpha: green (#3fb950) — status "working"
- worker-beta: blue (#58a6ff) — status "idle"
- worker-gamma: gray (#484f58) — status "disconnected"

**Data per card:**
- worker-alpha: name bold, "localhost — /src/project-a", "No task assigned" (or "Task assigned" if task exists), status badge "Working" (green bg tint)
- worker-beta: "localhost — /src/project-b", badge "Idle" (blue bg tint)
- worker-gamma: "rdev1.example.com", badge "Disconnected" (gray bg tint)

**Interactions:**
- Each card has a "View" button on the right

---

## 03_quick_stats.png

**Data validation:**
- Active Sessions = "2" (working + idle; disconnected excluded)
- Pending Decisions = "2"
- Open PRs = "0"
- Today's Cost = "$0.07"

---

## 04a_add_session_modal.png

**Layout:**
- Modal overlay: semi-transparent dark backdrop covering the dashboard
- Centered modal card (~500px wide) with light-dark background
- Header: "Add New Session" + X close button
- 3 form fields stacked vertically: Session Name, Host, Working Directory
- Bottom row: Cancel (secondary) + Create Session (primary blue) buttons

**Form fields:**
- All inputs have dark backgrounds, lighter border, placeholder text
- Session Name placeholder: "e.g. worker-alpha"
- Host placeholder: "e.g. rdev1.example.com or localhost"
- Working Directory placeholder: "e.g. /src/my-project (optional)"

---

## 04b_add_session_filled.png

**Data:**
- Session Name field: "worker-delta"
- Host field: "localhost"
- Working Directory field: "/src/project-d"

---

## 04c_after_add_session.png

**Layout:**
- Modal dismissed (hidden)
- 4 session cards now visible

**Data:**
- New card "worker-delta" appears with blue left border (idle status)
- "localhost — /src/project-d"
- Stats bar updates: "3" Active Sessions

---

## 05_session_detail.png

**Layout:**
- Wide modal overlay with session detail content
- Header: "worker-alpha" + X close button
- Sections stacked: Session Info, Tasks, Pull Requests, Recent Activity, Action buttons

**Session Info section:**
- Host: "localhost"
- Path: "/src/project-a"
- Status: "Working" badge (green)
- Created: relative time (e.g. "just now")
- Last Activity: "Never" or relative time

**Tasks section:**
- "Tasks (0)" header
- "No tasks assigned" empty state

**Pull Requests section:**
- "Pull Requests (0)" header
- "No pull requests" empty state

**Recent Activity section:**
- 2 activity entries for session s1: session.connected, task.started
- Each with time and event type badge

**Actions:**
- "Send Message" button (blue primary)
- "Remove Session" button (gray secondary)

---

## 06_decisions.png

**Layout:**
- Decisions panel on the right with "Decisions" header and count badge "2"
- 2 decision cards stacked vertically

**Decision 1 (HIGH urgency):**
- Left border: yellow (#d29922)
- Question: "Should we refactor the auth module before adding OAuth?"
- Urgency tag: "HIGH" in yellow
- Time: relative time
- 2 option buttons: "Yes, refactor first", "No, add OAuth directly" (green outline)
- "Dismiss" button below options

**Decision 2 (CRITICAL urgency):**
- Left border: red (#f85149)
- Question: "PR #42 has merge conflicts. How should we resolve?"
- Urgency tag: "CRITICAL" in red
- Context text: "Conflicts in src/auth.py and src/config.py"
- 1 "Approve" button (green, since no custom options)
- "Dismiss" button

---

## 07_after_approve.png

**Data:**
- 1 decision card remaining (the one not approved)
- Decision count badge: "1"
- Stats bar: "1" Pending Decisions

---

## 08_after_dismiss.png

**Data:**
- 0 decision cards remaining (or "No pending decisions" empty state)
- Decision count badge: "0"
- Stats bar: "0" Pending Decisions

---

## 09_activity_timeline.png

**Layout:**
- Activity panel on the left
- 3+ activity items, each in a horizontal row

**Per item:**
- Time column: HH:MM format (e.g. "07:57 PM")
- Event type badge: colored tag (e.g. "session.connected" in green, "pr.created" in blue, "task.started" in green)
- Detail text: extracted from event_data (e.g. "localhost", "#42 Add user auth", "Implement OAuth flow")

**Ordering:**
- Most recent first (session.connected at top if it was inserted last)

---

## 10_chat.png

**Layout:**
- Chat panel on the right with "Chat" header
- Message area with scrollable content
- Input bar at bottom: text input + "Send" button (blue)

**Messages:**
- User bubble (right-aligned, blue background): "What is the status?"
- Assistant bubble (left-aligned, bordered): Contains session status summary text (fallback mode since no API key)

**Content of assistant response (fallback mode):**
- Lists sessions with their statuses
- Shows pending decisions count
- Echoes user message
- Notes that LLM brain is unavailable

---

## 11_cost_display.png

**Layout:**
- Full-width panel with "Cost & Budget" header
- Left column: large total cost display + budget bar
- Right column: per-session breakdown rows

**Cost total:**
- "$0.0700" in large green (#3fb950) text
- "TOTAL SPEND" label below

**Budget bar:**
- Thin progress bar, very small fill (0.1%)
- Text: "0.1% of $50 daily budget"
- Bar color: blue/green (ok class, under 80%)

**Breakdown rows:**
- Row 1: "worker-alpha" | "22000 tokens" | "$0.0475" (green)
- Row 2: "worker-beta" | "4500 tokens" | "$0.0225" (green)

---

## 12_responsive.png

**Layout (600px viewport):**
- Single-column layout — all panels stack vertically
- Stats bar wraps (2x2 grid or stacks)
- Session cards and decision cards full-width
- All panels visible, no horizontal overflow
- No broken layouts or overlapping elements

**Absence:**
- No horizontal scrollbar
- No console errors throughout entire test run

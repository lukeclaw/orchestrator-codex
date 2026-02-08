# Demo Walkthrough

## The project

You're building **Tiny Zoo** — a small browser game for toddlers. The screen shows cartoon animals. Tap an animal, it makes its sound and does a little bounce animation. That's it. Simple, colorful, no reading required.

Three pieces need to be built:

1. **The animal grid** — an HTML/CSS page with a 3x2 grid of big, bright animal buttons (cow, duck, cat, dog, frog, sheep)
2. **The sound engine** — a JavaScript module that plays the correct animal sound when tapped, using the Web Audio API
3. **The animations** — CSS animations for each animal: a bounce on tap, a wiggle on hover, a fade-in on page load

Each piece is independent. Three workers can build them in parallel.

All work happens inside `orchestrator/tmp/demo/` — a throwaway folder.

The orchestrator is already running at `http://localhost:8080`. The dashboard is empty. Auto-approve rules (Tool Calls, Continue Prompts, Generic Y/N) were turned on earlier in Settings.

---

## Step 1. Create the project and describe what you want

Sidebar > **Projects** > **+ New Project**

- **Name**: `Tiny Zoo`
- **Description**:

```
A tap-the-animal web game for toddlers. Single index.html page with a 3x2
grid of cartoon animals (cow, duck, cat, dog, frog, sheep). Each animal is
a large colorful button (~200x200px). Tapping plays the animal's sound and
triggers a bounce animation. Tech: plain HTML, CSS, vanilla JS. No
frameworks, no build tools. Target: modern mobile browsers. All files go in
orchestrator/tmp/demo/tiny-zoo/.
```

Click **Create**.

**Expected**: The project card appears on the Dashboard under "Active Projects" showing `Tiny Zoo` with status `active`. The stats bar shows `1` under Projects.

---

## Step 2. Launch the project

Click **Launch** on the Tiny Zoo project card. A confirmation dialog asks how many workers to spin up.

The orchestrator reads the project description and proposes a plan:

> **Proposed tasks (3):**
>
> 1. **Build the animal grid page** — Create `index.html` with a 3x2 CSS grid of animal buttons. Each button ~200x200px with a bright background color and a large emoji as the animal icon. Colors: cow (#8B4513), duck (#FFD700), cat (#FF8C00), dog (#D2B48C), frog (#32CD32), sheep (#D3D3D3). Page background sky blue (#87CEEB). Centered grid, responsive, mobile viewport meta tag. Link to `styles.css` and `app.js`.
>
> 2. **Build the sound engine** — Create `app.js`. Use Web Audio API to synthesize sounds (no audio files): cow = low 150Hz moo, duck = 800Hz quack, cat = 600Hz meow with vibrato, dog = 200Hz woof, frog = 400Hz double blip, sheep = 350Hz baa with tremolo. Click listener on each `.animal-btn` reads `data-animal` and calls the right function. Include AudioContext resume for autoplay policy.
>
> 3. **Build the animations** — Create `styles.css`. Fade-in on load with staggered delay per button. Wiggle on hover (rotate -5deg to 5deg). Bouncy scale-up on tap (1.0 → 1.3 → 1.0 with overshoot). Pulsing glow on hover via box-shadow. Use `.tapped` class added on click and removed after animation ends.
>
> **Workers needed:** 3

You review the tasks. They look right. Click **Confirm**.

**Expected**: The orchestrator does the following automatically:
- Creates 3 tasks in the database, linked to the Tiny Zoo project
- Spins up 3 worker sessions (`tiny-zoo-1`, `tiny-zoo-2`, `tiny-zoo-3`) — each gets a tmux window
- Sends `mkdir -p .../tmp/demo/tiny-zoo && cd .../tmp/demo/tiny-zoo && claude` to each tmux window
- Waits for Claude Code to start in each session (monitors for the `>` prompt)
- Assigns one task per worker and sends the task context to each terminal

The dashboard updates in real-time as this happens. Within ~15 seconds you see:
- 3 session cards, all blue (working) with pulsing indicators
- Stats bar: `3` Active Sessions, `3` In-Progress Tasks
- Activity feed: `session.created` x3, `task.assigned` x3

You did one click. The orchestrator did 6 things.

---

## Step 3. Watch workers execute

Stay on the dashboard. Three workers are running in parallel:

- **tiny-zoo-1** (blue, working) — writing `index.html`, setting up the grid layout, adding emoji buttons with colors
- **tiny-zoo-2** (blue, working) — writing `app.js`, creating oscillator functions (`playMoo()`, `playQuack()`, ...)
- **tiny-zoo-3** (blue, working) — writing `styles.css`, defining `@keyframes bounce`, `@keyframes wiggle`, staggered fade-in delays

Click on the **tiny-zoo-2** card. The session detail page shows its live terminal on the right. You can see Claude Code writing the Web Audio synthesis functions in real-time.

Workers hit permission prompts along the way:

```
Allow Write tool for .../tmp/demo/tiny-zoo/app.js?
```

The auto-approve engine catches each one and sends `y` automatically. The activity feed shows `auto_approve.sent` events accumulating. No session card ever turns yellow — workers never block.

Click back to the dashboard to see all 3 running.

**Expected**: All 3 workers are visible from one screen. You can drill into any one to watch it work. Auto-approve handles all routine prompts in the background. The activity feed is a live stream of what's happening across all workers.

---

## Step 4. Answer one real decision

After a few minutes, tiny-zoo-2 finishes `app.js` and asks a design question:

```
I've created the sound synthesis functions. Should I also add a volume
control slider to the page, or keep it simple with just the tap-to-play
interaction?
```

This doesn't match any auto-approve pattern. The orchestrator creates a Decision.

The tiny-zoo-2 session card turns yellow (waiting) with "Needs attention" in the footer. The stats bar shows `1` Pending Decisions. The sidebar shows a "Waiting" badge with count `1`.

Sidebar > **Decisions**. You see:

> **Session 'tiny-zoo-2' is waiting for input**
>
> Context: I've created the sound synthesis functions. Should I also add a volume control slider to the page, or keep it simple with just the tap-to-play interaction?

Type your response: `Keep it simple. No volume control. Toddlers just tap.`

Click **Respond**. The orchestrator sends your response to tiny-zoo-2's terminal. Claude Code acknowledges and wraps up.

**Expected**: The session card turns blue again. The decision shows status `resolved`. This was the only time you typed anything since clicking Launch.

---

## Step 5. Review completed work

Workers finish one by one. Each session card turns green (idle) as its worker completes.

Sidebar > **Tasks**. All three show status `completed`:
- "Build the animal grid page" — completed by tiny-zoo-1
- "Build the sound engine" — completed by tiny-zoo-2
- "Build the animations" — completed by tiny-zoo-3

Sidebar > **Activity**. The full timeline:
- 3x `session.created` — orchestrator spun up workers
- 3x `task.assigned` — orchestrator sent tasks to workers
- ~8x `auto_approve.sent` — routine prompts handled automatically
- 1x decision created + resolved — the volume control question you answered
- 3x task completed

Open a terminal and check the output:
```
ls orchestrator/tmp/demo/tiny-zoo/
```

You should see:
```
index.html    app.js    styles.css
```

Open `index.html` in a browser. A sky-blue page with 6 big colorful animal buttons. The buttons fade in on load with a stagger. Hover one — it wiggles. Tap one — it bounces and plays a synthesized animal sound.

**Expected**: Three files, one complete game. Built by 3 parallel workers. You typed two things: a project description and one design decision.

---

## What the orchestrator handled automatically

| What | How |
|------|-----|
| Broke project into 3 tasks | LLM read the project description and decomposed it |
| Created 3 worker sessions | One tmux window per task |
| Started Claude Code in each | Sent shell commands to each tmux window |
| Assigned tasks to workers | Matched tasks to idle workers, sent context |
| Approved ~8 routine prompts | Auto-approve rules matched tool calls and continue prompts |
| Detected a real question | No rule matched → created a Decision for you |
| Tracked everything | Activity feed logged every event automatically |

## What you did

1. Described the project
2. Clicked Launch
3. Answered one design question
4. Opened the game in a browser

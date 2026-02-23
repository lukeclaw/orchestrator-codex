---
name: pr-workflow
description: Full PR lifecycle — checking PR status, reconciling state, creating PRs, handling reviews, addressing CI failures, and merging. Use this skill for ANY pull request activity.
---

# PR Workflow

Full PR lifecycle: creation, review handling, and merge.

---

## First: Verify Environment

Before any PR work, confirm you're in the right repo:
```bash
pwd
git remote -v
```
The remote URL must match the assigned MP. If it does NOT match: **STOP immediately and wait for help.**

## First: Check Existing PRs and Reconcile State

Before creating new PRs, check what already exists:
```bash
gh pr list --author @me --state open
gh pr list --author @me --state merged --limit 10
gh pr list --author @me --state closed --limit 5
```

If `gh` commands fail with auth errors: **STOP and wait for help.**

**Batch-check multiple PRs:** When reconciling several open PRs, use `orch-prs` to check them all at once:
```bash
# Batch-check all open PRs at once (auto-detects repo from cwd)
orch-prs 101 102 103
```
The output includes an `action` field per PR (e.g., `merged`, `ci_failing`, `changes_requested`, `ready_to_merge`, `review_pending`) so you know exactly what to do for each.

**Reconcile stale subtask state:** For each `in_progress` subtask with a PR link, check if its PR is already merged. If so, mark the subtask `done` immediately — this prevents redundant work from stale state left by previous sessions.

---

## Handle Existing Draft PRs

Before creating new PRs, handle any open drafts first. Check each one:
```bash
gh pr view <PR_NUMBER>
gh pr checks <PR_NUMBER>
gh pr view <PR_NUMBER> --json reviews,reviewRequests
```

**Case A — CI checks are failing:**
Fix the code, commit, push (see rebase pattern below).

**Case B — There are review comments:**
Use `gh api` to read comments:
```bash
# Inline review comments
gh api repos/OWNER/REPO/pulls/N/comments
# PR-level comments
gh api repos/OWNER/REPO/issues/N/comments
```
Address each comment in code, then push.

**Case C — PR is clean (checks pass, no unresolved comments):**
Mark as ready for review — but **only during working hours: 9 AM – 6 PM local time, Mon–Fri.**
```bash
current_hour=$(date +%H)
current_day=$(date +%u)  # 1=Monday, 7=Sunday
if [ "$current_day" -le 5 ] && [ "$current_hour" -ge 9 ] && [ "$current_hour" -lt 18 ]; then
  gh pr ready <PR_NUMBER>
else
  echo "Outside working hours. PR stays as draft."
fi
```
If outside working hours: leave as draft and update subtask notes so the brain can schedule it later.

**Case D — PR says "too stale to merge":**
```bash
git fetch origin master && git rebase origin/master && git push --force-with-lease
```
If conflicts arise that you're unsure about: **STOP and wait for help.**

**Case E — PR has merge conflicts you're unsure about:**
**STOP and wait for help.**

---

## Create New PRs

### Branch Naming

**Always prefix branches with your GitHub username.** Derive it once at the start:
```bash
GH_USER=$(gh api user --jq '.login | split("_")[0]')
git checkout master && git pull origin master
git checkout -b "$GH_USER/your-branch-name"
```
Never use `feature/`, `fix/`, or other generic prefixes.

### Do the Work, Then Push

```bash
git add -A && git commit -m "Your commit message"
git fetch origin $BRANCH && git rebase origin/$BRANCH && git push -u origin $BRANCH
```

**Always rebase before pushing.** Bots (e.g., Freshness Guardian) auto-merge master into PR branches, so the remote will have commits your local doesn't. A plain `git push` will fail.

### Create a Draft PR

```bash
# Check if a PR template exists
cat .github/PULL_REQUEST_TEMPLATE.md 2>/dev/null || cat .github/pull_request_template.md 2>/dev/null || echo "No template found"

# Create draft PR — use single-quoted heredoc to avoid shell escaping issues with ! in markdown
gh pr create --draft --title "Your PR title" --body "$(cat <<'EOF'
## Summary
...

## Testing Done
...
EOF
)"
```

**Immediately after creating**, attach the PR link to your subtask. Keep subtask as `in_progress` — it's not done until merged.

### Testing Done Section

First, check the MP's PR template for a testing checklist. If the template has its own testing requirements, **follow those first** — then supplement with our guidelines below where they don't conflict.

For verifiable evidence, choose the approach that fits the change:

1. **API/behavior changes** (preferred): Deploy locally on qprod/qei and include `curli`/`grpcurli` call + response showing the change works
2. **UI changes**: E2E test with screenshots (use `/screenshot-gh-upload` skill)
3. **Internal/non-API changes**: Unit test results and coverage stats are sufficient

Include the actual output — not just "tests pass". Reviewers should be able to verify the change from the PR description without checking out the branch.

---

## Handling PR Reviews

1. **Read the review** — Use `gh api` (not `gh pr view --comments` which mixes in GraphQL warnings):
   ```bash
   gh api repos/OWNER/REPO/pulls/N/comments
   gh api repos/OWNER/REPO/issues/N/comments
   ```
2. **Address each comment** — Evaluate whether the feedback is valid, then make changes if needed
3. **Respond to comments** — Use `gh` CLI to reply to review threads. Adjust tone based on the reviewer:
   - **Human reviewers** (priority): Write conversationally — explain your reasoning, acknowledge their point, invite follow-up. They will read and may respond. Always address human reviews first.
   - **Bot accounts** (`Copilot`, `github-actions[bot]`, `linkedin-svc`, `copilot-pull-request-reviewer[bot]`): Take with a grain of salt — bot suggestions are often not relevant to the PR or not applicable. Only act on feedback that is clearly valid. Keep replies short and factual. Bots won't read your reply.
4. **Push fixes** — Commit and push (use the rebase pattern)
5. **Notify the user** — Send a mandatory human interaction notification with the exact comment URL and your full reply text

### `gh api` Usage

**Never pipe `gh api` output to external `jq` or `python3` — it breaks on shell escaping.** Use `--jq` for simple field extraction. For complex filtering, read the raw JSON directly.
```bash
# Good: built-in --jq flag
gh api repos/OWNER/REPO/pulls/N/comments --jq '.[].user.login'

# Bad: piping to external jq or python3
gh api ... | jq '.[] | select(...)'   # breaks on special chars
```

**Shell safety for PR bodies:** Bash history expansion corrupts the ! character inside double-quoted strings, breaking markdown image syntax. Never pass PR bodies containing images via double-quoted -f or --body arguments. Instead:
```bash
# For gh pr create: single-quoted heredoc delimiter prevents all shell expansion
gh pr create --draft --title "Title" --body "$(cat <<'EOF'
## Testing Done
![Screenshot](https://...)
EOF
)"

# For gh api PATCH: write JSON to a temp file
cat > /tmp/pr-body.json <<'ENDJSON'
{"body": "## Testing Done\n![Screenshot](https://...)"}
ENDJSON
gh api --method PATCH repos/OWNER/REPO/pulls/N --input /tmp/pr-body.json
```

---

## Before Merging

Check for open questions or unresolved threads from all reviewers (human and bot).

- **Bot comments**: Address valid feedback in code. If already handled or not applicable, leave a brief reply noting why and resolve the thread.
- **Human comments**: If unaddressed questions remain, do NOT merge. Send a notification linked to the subtask so the user can follow up, and keep the subtask as `in_progress`.

## Merge

For PRs that are approved, all checks pass, and no unaddressed reviewer questions remain:
```bash
gh pr merge <PR_NUMBER> --merge
```

After merging, mark the subtask as `done`. This is the only time a subtask should be marked done.

---

## PR State → Subtask Status

| PR State | Subtask Status |
|---|---|
| No PR yet | `todo` |
| Draft PR created | `in_progress` |
| PR open, blocked by CI/review | `in_progress` (note the blocker) |
| PR marked ready for review | `in_progress` |
| PR approved and merged | `done` |
| PR closed without merge | `todo` (re-evaluate) |

## Subtask Descriptions Should Include

- PR link (from actual command output)
- Current PR state (draft/open/merged/closed)
- What is blocking (if anything)
- Review comment status when addressed (e.g., "Review: @user's naming concern — addressed in commit abc123")

**Update task and subtask notes after each significant action (PR created, review addressed, merged, blocked, etc.).**

---

## Log Timestamp After Each Action

After completing any significant action (PR created, review addressed, CI fix pushed, PR merged, etc.), print the current datetime so the user can see when the action was taken:
```bash
echo "Action completed at: $(date '+%Y-%m-%d %H:%M:%S %Z')"
```

---

## When Anything Is Unclear

**STOP and wait.** The brain or user will come to help. Do NOT proceed with guesses or workarounds.

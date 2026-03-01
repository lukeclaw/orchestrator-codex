---
name: pr-workflow
description: Full PR lifecycle — checking PR status, reconciling state, creating PRs, handling reviews, addressing CI failures, and merging. Use this skill for ANY pull request activity.
---

## Step 0: Verify Environment and Reconcile State

Before any PR work:
```bash
git remote -v                                    # Confirm correct repo — STOP if wrong
gh pr list --author @me --state open
gh pr list --author @me --state merged --limit 10
gh pr list --author @me --state closed --limit 5
```
If `gh` fails with auth errors: **STOP and wait for help.**

**Batch-check:** Use `orch-prs 101 102 103` to check multiple PRs at once (auto-detects repo). Output includes an `action` field per PR (`merged`, `ci_failing`, `changes_requested`, `ready_to_merge`, `review_pending`).

**Reconcile stale state:** For each `in_progress` subtask with a PR link, check if its PR is already merged. If so, mark subtask `done` immediately.

---

## Handle Existing Draft PRs

Check each open draft (`gh pr view`, `gh pr checks`, `gh pr view --json reviews,reviewRequests`):

- **CI failing** — Fix code, commit, push (always rebase before pushing).
- **Has review comments** — Read with `gh api repos/OWNER/REPO/pulls/N/comments` and `issues/N/comments`. Address in code, then push.
- **Clean (checks pass, no comments)** — Mark ready with `gh pr ready` but **only during working hours (9 AM–6 PM Mon–Fri)**. Otherwise leave as draft and note in subtask.
- **Too stale / conflicts** — `git fetch origin master && git rebase origin/master && git push --force-with-lease`. If conflicts are unclear: **STOP and wait for help.**
- **Chained PR (targets another PR's branch)** — Keep as draft. After upstream merges, GitHub retargets to `master`; then treat as clean.

---

## Create New PRs

### Branch Naming
**Always prefix with your GitHub username** (never `feature/`, `fix/`, etc.):
```bash
GH_USER=$(gh api user --jq '.login | split("_")[0]')
git checkout master && git pull origin master
git checkout -b "$GH_USER/your-branch-name"
```

### Push Pattern
**Always rebase before pushing** — bots auto-merge master into PR branches, so remote has commits you don't:
```bash
git add -A && git commit -m "Your commit message"
git fetch origin $BRANCH && git rebase origin/$BRANCH && git push -u origin $BRANCH
```

### Create Draft PR
Check for PR template first (`cat .github/PULL_REQUEST_TEMPLATE.md`). Use **single-quoted heredoc** to avoid shell escaping issues with `!` in markdown:
```bash
gh pr create --draft --title "Title" --body "$(cat <<'EOF'
## Summary
...
## Testing Done
...
EOF
)"
```
**Immediately attach** the PR link to your subtask. Keep subtask `in_progress` until merged.

### Testing Done — Verifiable Evidence
Follow the MP's PR template checklist, plus:
- **API/backend**: Full request + response (success + error case)
- **UI**: Screenshots (before/after) or recordings
- **Internal** (refactors, config): Test commands + output

---

## Handling PR Reviews

**Handle PRs one at a time.** Complete all steps for one PR (read → reply → push → notify) before moving to the next. Do NOT batch.

**For each PR with review comments:**

1. **Read** — `gh api repos/OWNER/REPO/pulls/N/comments` and `issues/N/comments` (not `gh pr view --comments`)
2. **Check if already replied** — Only post if there's genuinely new information. Don't repeat yourself.
3. **Address comments** — Evaluate feedback, make code changes if needed
4. **Reply** — Adjust tone:
   - **Human reviewers** (priority): Conversational — explain reasoning, invite follow-up.
   - **Bots** (`Copilot`, `github-actions[bot]`, `linkedin-svc`, etc.): Only act on clearly valid feedback. Keep short. Bots won't read replies.
5. **Push fixes** — Commit and push (rebase pattern)
6. **Notify immediately** — before moving to next PR. Include PR number/title, exact comment URL(s), and **full reply text** (not a summary):
   ```bash
   orch-notify "Replied to review on PR #123 (Add rate limiting):\n\nComment by @reviewer on src/api.py:\n> Should we add a retry here?\n\nMy reply:\nGood point — added exponential backoff in commit abc1234." \
     --type pr_comment --link "https://github.com/OWNER/REPO/pull/123#discussion_r456"
   ```

**Then repeat for the next PR.**

### `gh api` Notes
- **Never pipe to external `jq` or `python3`** — breaks on shell escaping. Use `--jq` flag instead.
- **Shell safety for PR bodies**: Use single-quoted heredoc (`<<'EOF'`) to prevent bash history expansion corrupting `!` in markdown.

---

## Merging

Check for unresolved threads first. If **human** questions remain unanswered: do NOT merge — notify the user instead.

```bash
gh pr merge <PR_NUMBER> --merge
```

After merging:
1. Notify: `orch-notify "PR #N merged: <summary>" --type info --link "PR_URL"`
2. Mark subtask `done` — this is the only time a subtask should be marked done.

---

## PR State → Subtask Status

| PR State | Subtask Status |
|---|---|
| No PR yet | `todo` |
| Draft / open / blocked | `in_progress` (note blocker) |
| Approved and merged | `done` |
| Closed without merge | `todo` (re-evaluate) |

**Update task and subtask notes after each significant action.**

**Log a timestamp after every significant action** (PR created, review addressed, CI fix pushed, PR merged, etc.):
```bash
echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') — <action description> ==="
```

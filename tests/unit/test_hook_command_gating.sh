#!/bin/bash
# Test suite for the PreToolUse command gating in check-command.sh
#
# Tests the hook script directly by feeding it JSON payloads on stdin
# and checking whether it outputs an "ask" decision (requiring user approval)
# or stays silent (allowing the command).

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="$SCRIPT_DIR/../../agents/shared/hooks/check-command.sh"

if [ ! -f "$HOOK" ]; then
    echo "ERROR: check-command.sh not found at $HOOK"
    exit 1
fi

PASS=0
FAIL=0

# Helper: run the hook with a Bash PreToolUse payload, return stdout
# Uses jq to properly JSON-encode the command (handles quotes, special chars)
run_hook() {
    local cmd="$1"
    local payload
    payload=$(jq -n --arg cmd "$cmd" '{
        hook_event_name: "PreToolUse",
        tool_name: "Bash",
        tool_input: { command: $cmd }
    }')
    echo "$payload" | bash "$HOOK" 2>/dev/null
}

# Assert the command triggers an "ask" approval prompt
assert_asks() {
    local cmd="$1"
    local label="$2"
    local output
    output=$(run_hook "$cmd")
    if echo "$output" | jq -e '.hookSpecificOutput.permissionDecision == "ask"' >/dev/null 2>&1; then
        PASS=$((PASS + 1))
        echo "  ✓ ASKS: $label"
    else
        FAIL=$((FAIL + 1))
        echo "  ✗ EXPECTED ASK: $label"
        echo "    command: $cmd"
        echo "    output:  $output"
    fi
}

# Assert the command is allowed (no output from hook)
assert_allowed() {
    local cmd="$1"
    local label="$2"
    local output
    output=$(run_hook "$cmd")
    if echo "$output" | jq -e '.hookSpecificOutput.permissionDecision == "ask"' >/dev/null 2>&1; then
        FAIL=$((FAIL + 1))
        echo "  ✗ EXPECTED ALLOW: $label"
        echo "    command: $cmd"
        echo "    output:  $output"
    else
        PASS=$((PASS + 1))
        echo "  ✓ ALLOWED: $label"
    fi
}

echo "=== Tier 1 Command Gating Tests ==="
echo ""

# --- rm tests ---
echo "-- Broad recursive delete --"
assert_asks 'rm -rf /'            "rm -rf /"
assert_asks 'rm -rf /*'           "rm -rf /*"
assert_asks 'rm -rf ~'            "rm -rf ~"
assert_asks 'rm -rf ~/'           "rm -rf ~/"
assert_asks 'rm -rf ~/*'          "rm -rf ~/*"
assert_asks 'rm -rf ..'           "rm -rf .."
assert_asks 'rm -Rf /'            "rm -Rf /"
assert_asks 'rm -fr /'            "rm -fr /"
assert_asks 'rm -rfv /'           "rm -rfv /"
assert_asks 'rm -rf / && echo done'  "rm -rf / && echo done"
assert_asks 'rm -rf /; echo done'    "rm -rf /; echo done"

assert_allowed 'rm -rf /tmp/build'       "rm -rf /tmp/build (specific path)"
assert_allowed 'rm -rf ./dist'           "rm -rf ./dist (relative path)"
assert_allowed 'rm -rf ~/projects/foo'   "rm -rf ~/projects/foo (specific subdir)"
assert_allowed 'rm file.txt'             "rm file.txt (no recursive)"
assert_allowed 'rm -f file.txt'          "rm -f file.txt (no recursive)"
echo ""

# --- SQL tests ---
echo "-- SQL destructive operations --"
assert_asks 'sqlite3 db.sqlite "DROP TABLE users"'           "DROP TABLE"
assert_asks 'psql -c "DROP DATABASE mydb"'                    "DROP DATABASE"
assert_asks 'mysql -e "DROP SCHEMA test"'                     "DROP SCHEMA"
assert_asks 'sqlite3 db.sqlite "DELETE FROM users"'           "DELETE FROM without WHERE"
assert_asks 'psql -c "delete from orders"'                    "DELETE FROM (lowercase) without WHERE"

assert_allowed 'sqlite3 db.sqlite "DELETE FROM users WHERE id=5"'  "DELETE FROM with WHERE"
assert_allowed 'sqlite3 db.sqlite "SELECT * FROM users"'          "SELECT (read-only)"
assert_asks 'echo "DROP TABLE" > notes.txt'   "DROP TABLE in echo (conservative — blocks SQL keywords in any context)"
echo ""

# --- Disk destruction ---
echo "-- Disk/filesystem destruction --"
assert_asks 'mkfs.ext4 /dev/sda1'         "mkfs"
assert_asks 'dd if=/dev/zero of=/dev/sda'  "dd to block device"
assert_asks '> /dev/sda'                    "redirect to block device"

assert_allowed 'dd if=/dev/zero of=./test.img bs=1M count=100'  "dd to regular file"
echo ""

# --- Fork bomb ---
echo "-- Fork bomb --"
assert_asks ':(){ :|:& };:'  "classic fork bomb"

assert_allowed 'echo "not a fork bomb"'  "normal echo"
echo ""

# --- chmod 777 ---
echo "-- chmod 777 on system paths --"
assert_asks 'chmod 777 /etc/passwd'         "chmod 777 /etc/passwd"
assert_asks 'chmod -R 777 /var'             "chmod -R 777 /var"
assert_asks 'chmod 777 /usr/local/bin/foo'  "chmod 777 /usr/local/bin/foo"

assert_allowed 'chmod 755 /tmp/script.sh'   "chmod 755 (not 777)"
assert_allowed 'chmod 777 myfile.txt'        "chmod 777 on local file"
echo ""

# --- kubectl prod ---
echo "-- kubectl production operations --"
assert_asks 'kubectl delete pod my-pod -n prod'              "kubectl delete -n prod"
assert_asks 'kubectl apply -f deploy.yaml --namespace prod'  "kubectl apply --namespace prod"
assert_asks 'kubectl delete -n prod deployment/app'          "kubectl delete -n prod (alt order)"

assert_allowed 'kubectl get pods -n prod'                      "kubectl get (read-only)"
assert_allowed 'kubectl apply -f deploy.yaml -n staging'       "kubectl apply to staging"
assert_allowed 'kubectl delete pod my-pod -n dev'              "kubectl delete in dev"
echo ""

# --- PR creation without --draft ---
echo "-- PR creation without --draft --"
assert_asks 'gh pr create --title "My PR" --body "description"'       "gh pr create without --draft"
assert_asks 'gh pr create --title "fix" --body "body" --base main'    "gh pr create with other flags but no --draft"
assert_asks 'gh pr create'                                             "bare gh pr create"
assert_asks 'gh pr create --title "test" --body "$(cat <<EOF
multi-line body
EOF
)"'                                                                    "gh pr create with heredoc body, no --draft"
assert_asks 'cd /tmp && gh pr create --title "test"'                   "gh pr create after cd, no --draft"

assert_allowed 'gh pr create --draft --title "My PR" --body "desc"'   "gh pr create --draft (before title)"
assert_allowed 'gh pr create --title "My PR" --draft --body "desc"'   "gh pr create --draft (mid-flags)"
assert_allowed 'gh pr create --title "My PR" --body "desc" --draft'   "gh pr create --draft (at end)"
assert_allowed 'gh pr view'                                            "gh pr view (not create)"
assert_allowed 'gh pr list'                                            "gh pr list (not create)"
assert_allowed 'gh pr merge --squash'                                  "gh pr merge (not create)"
assert_asks 'echo "gh pr create"'                                      "gh pr create in echo (conservative — blocks keyword in any context)"
echo ""

# --- Non-Bash tools should pass through ---
echo "-- Non-Bash tools --"
NON_BASH_PAYLOAD='{"hook_event_name": "PreToolUse", "tool_name": "Write", "tool_input": {"file_path": "/etc/passwd", "content": "hacked"}}'
NON_BASH_OUTPUT=$(echo "$NON_BASH_PAYLOAD" | bash "$HOOK" 2>/dev/null)
if echo "$NON_BASH_OUTPUT" | jq -e '.hookSpecificOutput.permissionDecision == "ask"' >/dev/null 2>&1; then
    FAIL=$((FAIL + 1))
    echo "  ✗ EXPECTED ALLOW: Write tool should not be gated"
else
    PASS=$((PASS + 1))
    echo "  ✓ ALLOWED: Write tool passes through (not Bash)"
fi
echo ""

# --- Summary ---
echo "=== Results: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0

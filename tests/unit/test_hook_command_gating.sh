#!/bin/bash
# Test suite for the PreToolUse command gating in update-status.sh
#
# Tests the hook script directly by feeding it JSON payloads on stdin
# and checking whether it outputs a block decision or not.

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

# Assert the command IS blocked
assert_blocked() {
    local cmd="$1"
    local label="$2"
    local output
    output=$(run_hook "$cmd")
    if echo "$output" | grep -q '"decision".*"block"'; then
        PASS=$((PASS + 1))
        echo "  ✓ BLOCKED: $label"
    else
        FAIL=$((FAIL + 1))
        echo "  ✗ EXPECTED BLOCK: $label"
        echo "    command: $cmd"
        echo "    output:  $output"
    fi
}

# Assert the command is NOT blocked (allowed)
assert_allowed() {
    local cmd="$1"
    local label="$2"
    local output
    output=$(run_hook "$cmd")
    if echo "$output" | grep -q '"decision".*"block"'; then
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
assert_blocked 'rm -rf /'            "rm -rf /"
assert_blocked 'rm -rf /*'           "rm -rf /*"
assert_blocked 'rm -rf ~'            "rm -rf ~"
assert_blocked 'rm -rf ~/'           "rm -rf ~/"
assert_blocked 'rm -rf ~/*'          "rm -rf ~/*"
assert_blocked 'rm -rf ..'           "rm -rf .."
assert_blocked 'rm -Rf /'            "rm -Rf /"
assert_blocked 'rm -fr /'            "rm -fr /"
assert_blocked 'rm -rfv /'           "rm -rfv /"
assert_blocked 'rm -rf / && echo done'  "rm -rf / && echo done"
assert_blocked 'rm -rf /; echo done'    "rm -rf /; echo done"

assert_allowed 'rm -rf /tmp/build'       "rm -rf /tmp/build (specific path)"
assert_allowed 'rm -rf ./dist'           "rm -rf ./dist (relative path)"
assert_allowed 'rm -rf ~/projects/foo'   "rm -rf ~/projects/foo (specific subdir)"
assert_allowed 'rm file.txt'             "rm file.txt (no recursive)"
assert_allowed 'rm -f file.txt'          "rm -f file.txt (no recursive)"
echo ""

# --- SQL tests ---
echo "-- SQL destructive operations --"
assert_blocked 'sqlite3 db.sqlite "DROP TABLE users"'           "DROP TABLE"
assert_blocked 'psql -c "DROP DATABASE mydb"'                    "DROP DATABASE"
assert_blocked 'mysql -e "DROP SCHEMA test"'                     "DROP SCHEMA"
assert_blocked 'sqlite3 db.sqlite "DELETE FROM users"'           "DELETE FROM without WHERE"
assert_blocked 'psql -c "delete from orders"'                    "DELETE FROM (lowercase) without WHERE"

assert_allowed 'sqlite3 db.sqlite "DELETE FROM users WHERE id=5"'  "DELETE FROM with WHERE"
assert_allowed 'sqlite3 db.sqlite "SELECT * FROM users"'          "SELECT (read-only)"
assert_blocked 'echo "DROP TABLE" > notes.txt'   "DROP TABLE in echo (conservative — blocks SQL keywords in any context)"
echo ""

# --- Disk destruction ---
echo "-- Disk/filesystem destruction --"
assert_blocked 'mkfs.ext4 /dev/sda1'         "mkfs"
assert_blocked 'dd if=/dev/zero of=/dev/sda'  "dd to block device"
assert_blocked '> /dev/sda'                    "redirect to block device"

assert_allowed 'dd if=/dev/zero of=./test.img bs=1M count=100'  "dd to regular file"
echo ""

# --- Fork bomb ---
echo "-- Fork bomb --"
assert_blocked ':(){ :|:& };:'  "classic fork bomb"

assert_allowed 'echo "not a fork bomb"'  "normal echo"
echo ""

# --- chmod 777 ---
echo "-- chmod 777 on system paths --"
assert_blocked 'chmod 777 /etc/passwd'         "chmod 777 /etc/passwd"
assert_blocked 'chmod -R 777 /var'             "chmod -R 777 /var"
assert_blocked 'chmod 777 /usr/local/bin/foo'  "chmod 777 /usr/local/bin/foo"

assert_allowed 'chmod 755 /tmp/script.sh'   "chmod 755 (not 777)"
assert_allowed 'chmod 777 myfile.txt'        "chmod 777 on local file"
echo ""

# --- kubectl prod ---
echo "-- kubectl production operations --"
assert_blocked 'kubectl delete pod my-pod -n prod'              "kubectl delete -n prod"
assert_blocked 'kubectl apply -f deploy.yaml --namespace prod'  "kubectl apply --namespace prod"
assert_blocked 'kubectl delete -n prod deployment/app'          "kubectl delete -n prod (alt order)"

assert_allowed 'kubectl get pods -n prod'                      "kubectl get (read-only)"
assert_allowed 'kubectl apply -f deploy.yaml -n staging'       "kubectl apply to staging"
assert_allowed 'kubectl delete pod my-pod -n dev'              "kubectl delete in dev"
echo ""

# --- Non-Bash tools should pass through ---
echo "-- Non-Bash tools --"
NON_BASH_PAYLOAD='{"hook_event_name": "PreToolUse", "tool_name": "Write", "tool_input": {"file_path": "/etc/passwd", "content": "hacked"}}'
NON_BASH_OUTPUT=$(echo "$NON_BASH_PAYLOAD" | bash "$HOOK" 2>/dev/null)
if echo "$NON_BASH_OUTPUT" | grep -q '"decision".*"block"'; then
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

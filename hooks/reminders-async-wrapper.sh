#!/usr/bin/env bash
# Reminders Async Wrapper
#
# Launches a reminders hook in the background and returns immediately.
# This prevents reminders sync from blocking Claude Code sessions.
#
# Usage (from .claude/settings.json):
#   reminders-async-wrapper.sh <script.py> [extra-args...]
#
# The wrapper:
# 1. Reads stdin (hook input) and saves it to a temp file
# 2. Launches the Python script with --async in the background
# 3. Returns exit 0 immediately so the hook chain continues

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$1"
shift

# Resolve script path (relative to hooks dir if not absolute)
if [[ "$SCRIPT" != /* ]]; then
    SCRIPT="$SCRIPT_DIR/$SCRIPT"
fi

# Read stdin (hook JSON input) - we parse file_path for PostToolUse hooks
HOOK_INPUT=$(cat)
TMPFILE=$(mktemp /tmp/reminders-hook-XXXXXX)
echo "$HOOK_INPUT" > "$TMPFILE"

# For PostToolUse hooks, extract file_path to pass as argument
FILE_PATH=""
if echo "$HOOK_INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('file_path',''))" 2>/dev/null; then
    FILE_PATH=$(echo "$HOOK_INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('file_path',''))" 2>/dev/null)
fi

# Launch the script in the background with --async flag
# nohup + disown ensures it survives after this shell exits
if [ -n "$FILE_PATH" ]; then
    nohup python3 "$SCRIPT" --async "$FILE_PATH" "$@" < "$TMPFILE" > /dev/null 2>&1 &
else
    nohup python3 "$SCRIPT" --async "$@" < "$TMPFILE" > /dev/null 2>&1 &
fi
disown

# Clean up temp file after a delay (background process should have read it by then)
(sleep 5 && rm -f "$TMPFILE") &
disown

exit 0

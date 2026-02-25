#!/usr/bin/env python3
"""
Reminders Sync Daemon

Optional background daemon that polls macOS Reminders every 60 seconds
and syncs completion state back to your Obsidian vault.

Use this if you want real-time bidirectional sync without waiting for a
Claude Code session to start. For most users, the SessionStart hook
(reminders-session-sync.py) is sufficient.

What it does:
  1. Reads state file (.claude/state/reminders-state.json) to get known mappings
  2. Polls both Reminders lists (Work, Personal) for completion changes
  3. When a reminder is completed in Reminders (phone/watch/mac):
     - Finds the source file + line in the vault
     - Marks the task as complete in the markdown ([ ] → [x])
     - Updates the state file

Usage:
  python3 reminders-sync-daemon.py           # Run in foreground (Ctrl+C to stop)
  python3 reminders-sync-daemon.py --once    # Single poll cycle, then exit
  python3 reminders-sync-daemon.py --dry-run # Poll and log but don't write vault

To run as a launchd service on macOS, see the README.
"""

import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Constants ──────────────────────────────────────────────────────────────────

VAULT_ROOT = os.environ.get("OBSIDIAN_VAULT_ROOT") or str(
    Path(__file__).resolve().parent.parent.parent
)

STATE_FILE = os.path.join(VAULT_ROOT, ".claude", "state", "reminders-state.json")
STATE_TMP = STATE_FILE + ".tmp"
REMINDERS_SCRIPT = os.path.join(VAULT_ROOT, ".claude", "scripts", "reminders_manager.py")
PID_FILE = os.path.join(VAULT_ROOT, ".claude", "state", "reminders-sync.pid")
LOG_FILE = os.path.join(VAULT_ROOT, ".claude", "state", "reminders-sync.log")

POLL_INTERVAL = 60  # seconds

# --- CONFIGURE THIS ---
# Must match REMINDER_LISTS in reminders_manager.py
REMINDER_LISTS = ["Work", "Personal"]
# --- END CONFIG ---

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("reminders-sync")

# ── Signal Handling ───────────────────────────────────────────────────────────

_running = True


def _handle_signal(signum, frame):
    global _running
    logger.info(f"Received signal {signum}, shutting down gracefully...")
    _running = False


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)

# ── PID File ──────────────────────────────────────────────────────────────────


def write_pid():
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def remove_pid():
    try:
        os.remove(PID_FILE)
    except FileNotFoundError:
        pass


# ── State I/O ─────────────────────────────────────────────────────────────────


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"last_refresh": None, "daily_note_date": None, "mappings": []}
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to load state: {e}")
        return {"last_refresh": None, "daily_note_date": None, "mappings": []}


def save_state(state: dict):
    """Atomic write via temp file + rename to prevent corruption."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    try:
        with open(STATE_TMP, "w") as f:
            json.dump(state, f, indent=2)
        os.rename(STATE_TMP, STATE_FILE)
    except Exception as e:
        logger.error(f"Failed to save state: {e}")


# ── Reminders API ─────────────────────────────────────────────────────────────


def run_reminders_command(args: list) -> dict:
    """Call reminders_manager.py via bash (inherits FDA for AppleScript). Returns parsed JSON.

    Note: Routing through /bin/bash ensures osascript inherits Full Disk Access / TCC context.
    Direct subprocess.run([python, script]) causes osascript to hang under launchd's restricted
    session. Use /bin/bash as the launcher when running as a daemon.
    """
    python = None
    for candidate in ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]:
        if os.path.isfile(candidate):
            python = candidate
            break
    if not python:
        logger.error("python3 not found")
        return {"success": False, "error": "python3 not found"}

    quoted_args = " ".join(f'"{a}"' for a in args)
    bash_cmd = f'exec "{python}" "{REMINDERS_SCRIPT}" {quoted_args}'
    try:
        result = subprocess.run(
            ["/bin/bash", "-c", bash_cmd],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
        else:
            return {"success": False, "error": result.stderr.strip()}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "AppleScript timeout"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def fetch_list_reminders(list_name: str) -> list:
    result = run_reminders_command(["list-reminders", list_name, "--include-completed"])
    if result.get("success"):
        return result.get("data", [])
    logger.warning(f"Failed to fetch {list_name}: {result.get('error')}")
    return []


# ── Vault Write ───────────────────────────────────────────────────────────────

TASK_CHECKBOX_RE = re.compile(r"^(\s*-\s*)\[ \](.*)$")


def mark_task_complete_in_vault(source_file: str, line_number: int, task_text: str, dry_run: bool = False) -> bool:
    """
    Mark a task as complete in the vault markdown file.

    Finds the task by line_number (1-indexed) and flips [ ] → [x].
    Falls back to text search if line content doesn't match (file shifted).
    """
    abs_path = os.path.join(VAULT_ROOT, source_file)

    if not os.path.exists(abs_path):
        logger.warning(f"Source file not found: {abs_path}")
        return False

    with open(abs_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    target_line_idx = None

    idx = line_number - 1
    if 0 <= idx < len(lines):
        line = lines[idx]
        if TASK_CHECKBOX_RE.match(line) and "[ ]" in line:
            clean = re.sub(r"[🔴🟡🟢🔵✅📅\[\]\-\s]", " ", line).strip().lower()
            task_clean = re.sub(r"[🔴🟡🟢🔵✅📅\[\]\-\s]", " ", task_text).strip().lower()
            if task_clean[:30] in clean or clean[:30] in task_clean:
                target_line_idx = idx

    # Fallback: text search
    if target_line_idx is None:
        task_clean = re.sub(r"[🔴🟡🟢🔵✅📅]", "", task_text).strip()
        search_key = task_clean[:40].lower()
        for i, line in enumerate(lines):
            if "[ ]" in line:
                line_clean = re.sub(r"[🔴🟡🟢🔵✅📅]", "", line).strip().lower()
                if search_key and search_key in line_clean:
                    target_line_idx = i
                    break

    if target_line_idx is None:
        logger.warning(f"Could not find task in {source_file} (line {line_number}): {task_text[:50]}")
        return False

    old_line = lines[target_line_idx]
    new_line = old_line.replace("[ ]", "[x]", 1)

    if old_line == new_line:
        logger.debug(f"Task already complete at line {target_line_idx + 1}")
        return True

    if dry_run:
        logger.info(f"[DRY-RUN] Would complete line {target_line_idx + 1} in {source_file}: {old_line.strip()}")
        return True

    lines[target_line_idx] = new_line

    # Atomic write
    tmp_path = abs_path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        os.rename(tmp_path, abs_path)
        logger.info(f"Completed task in vault: {source_file}:{target_line_idx + 1} — {old_line.strip()[:60]}")
        return True
    except Exception as e:
        logger.error(f"Failed to write vault file {abs_path}: {e}")
        try:
            os.remove(tmp_path)
        except FileNotFoundError:
            pass
        return False


# ── Sync Logic ────────────────────────────────────────────────────────────────


def build_reminder_lookup(reminders: list) -> dict:
    lookup = {}
    for r in reminders:
        rid = r.get("id", "")
        if rid:
            lookup[rid] = r
    return lookup


def poll_and_sync(dry_run: bool = False) -> dict:
    """
    One poll cycle:
    1. Load state
    2. Fetch all reminders from all lists
    3. For each mapping with a reminder_id:
       - If Reminders says completed=True and state says False → write vault
    4. Save updated state
    """
    summary = {"completed": 0, "already_done": 0, "not_found": 0, "errors": 0, "skipped": 0}

    state = load_state()
    mappings = state.get("mappings", [])

    if not mappings:
        logger.debug("No mappings in state, nothing to sync")
        return summary

    # Fetch all lists upfront (one AppleScript call per list)
    all_reminders: dict = {}
    for list_name in REMINDER_LISTS:
        reminders = fetch_list_reminders(list_name)
        all_reminders.update(build_reminder_lookup(reminders))

    logger.debug(f"Fetched {len(all_reminders)} reminders across {len(REMINDER_LISTS)} lists")

    state_changed = False

    for mapping in mappings:
        reminder_id = mapping.get("reminder_id", "")

        if not reminder_id:
            summary["skipped"] += 1
            continue

        if mapping.get("completed", False):
            summary["already_done"] += 1
            continue

        reminder = all_reminders.get(reminder_id)
        if reminder is None:
            # Try direct get (may have been completed+archived by macOS)
            logger.debug(f"Reminder {reminder_id} not in batch fetch, trying direct get")
            result = run_reminders_command(["get", reminder_id])
            if result.get("success"):
                reminder = result.get("data", {})
            else:
                summary["not_found"] += 1
                continue

        if not reminder.get("completed", False):
            continue

        # Reminders says done, vault says not done → sync to vault
        source_file = mapping.get("source_file", "")
        line_number = mapping.get("line_number", 0)
        task_text = mapping.get("task_text", "")

        if not source_file:
            logger.warning(f"No source_file in mapping for reminder {reminder_id}")
            summary["errors"] += 1
            continue

        success = mark_task_complete_in_vault(source_file, line_number, task_text, dry_run=dry_run)

        if success:
            if not dry_run:
                mapping["completed"] = True
                state_changed = True
            summary["completed"] += 1
        else:
            summary["errors"] += 1

    if state_changed:
        save_state(state)

    return summary


# ── Main Loop ─────────────────────────────────────────────────────────────────


def main():
    dry_run = "--dry-run" in sys.argv
    once = "--once" in sys.argv

    if dry_run:
        logger.info("Running in DRY-RUN mode — vault will not be modified")

    if not once:
        write_pid()
        logger.info(f"Reminders sync daemon started (PID {os.getpid()}, poll every {POLL_INTERVAL}s)")
        logger.info(f"Vault: {VAULT_ROOT}")
        logger.info(f"State: {STATE_FILE}")

    try:
        while _running:
            cycle_start = time.time()
            try:
                summary = poll_and_sync(dry_run=dry_run)
                if summary["completed"] > 0:
                    logger.info(
                        f"Sync cycle: {summary['completed']} completed from Reminders → vault "
                        f"| skipped={summary['skipped']} not_found={summary['not_found']} errors={summary['errors']}"
                    )
                else:
                    logger.debug(
                        f"Sync cycle: no changes "
                        f"(already_done={summary['already_done']} skipped={summary['skipped']})"
                    )
            except Exception as e:
                logger.error(f"Sync cycle error: {e}", exc_info=True)

            if once:
                break

            # Sleep in small chunks so SIGTERM is handled promptly
            elapsed = time.time() - cycle_start
            remaining = max(0, POLL_INTERVAL - elapsed)
            sleep_chunk = 5
            slept = 0
            while _running and slept < remaining:
                time.sleep(min(sleep_chunk, remaining - slept))
                slept += sleep_chunk

    finally:
        if not once:
            logger.info("Reminders sync daemon stopped")
            remove_pid()


if __name__ == "__main__":
    main()

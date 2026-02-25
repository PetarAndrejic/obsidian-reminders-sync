#!/usr/bin/env python3
"""
Reminders Task Detector — PostToolUse Hook

When Claude Code writes or edits a vault file, syncs its tasks to macOS
Reminders by reading from the pre-built task-index.json.

Direction: Obsidian → Reminders (push on file save)

How it works:
  1. Triggered on every Write/Edit tool call in Claude Code
  2. Filters to today's daily note only (avoids syncing archived notes)
  3. Reads tasks from task-index.json (built by task-index-builder.py)
  4. Creates/completes/deletes reminders to match vault state
  5. Tracks state in reminders-state.json to avoid duplicates

Run-time behavior:
  - Non-blocking: launched via reminders-async-wrapper.sh
  - Deduplication: pre-fetches Reminders list contents once per list
  - Race condition guard: skips sync if index is empty but file has tasks
    (prevents orphan-deletion when task-index-builder hasn't run yet)
"""

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from typing import Optional, List, Dict

# ── Constants ──────────────────────────────────────────────────────────────────

VAULT_ROOT = os.environ.get("OBSIDIAN_VAULT_ROOT") or str(Path(__file__).resolve().parent.parent.parent)

STATE_FILE = os.path.join(VAULT_ROOT, ".claude", "state", "reminders-state.json")
INDEX_PATH = os.path.join(VAULT_ROOT, ".claude", "state", "task-index.json")
REMINDERS_SCRIPT = os.path.join(VAULT_ROOT, ".claude", "scripts", "reminders_manager.py")

# --- CONFIGURE THIS ---
# Map task section names (### headers in your daily note) to Reminders list names.
# Keys should match your daily note section headers exactly.
# Values must match list names in REMINDER_LISTS in reminders_manager.py.
CONTEXT_TO_LIST = {
    "Work": "Work",
    "Personal": "Personal",
    "Family": "Personal",
    "Health": "Personal",
}
# --- END CONFIG ---

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


def get_today_date() -> str:
    return date.today().isoformat()


def extract_blocker(task_text: str) -> Optional[str]:
    """Extract 'Waiting for: ...' reason from task text."""
    match = re.search(r'Waiting for:\s*(.+)', task_text)
    if match:
        return match.group(1).strip()
    return None


def extract_due_date(task_text: str) -> Optional[str]:
    """Extract '📅 YYYY-MM-DD' due date from task text. Returns YYYY-MM-DD string or None."""
    match = re.search(r'📅\s*(\d{4}-\d{2}-\d{2})', task_text)
    if match:
        return match.group(1)
    return None


def load_tasks_for_file(file_path: str) -> List[Dict]:
    """
    Load tasks from task-index.json filtered to a specific source file.
    Returns task dicts in the shape expected by sync_tasks_to_reminders().
    """
    if not os.path.exists(INDEX_PATH):
        return []
    try:
        with open(INDEX_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return []

    rel_path = file_path.replace(VAULT_ROOT, "").lstrip("/")
    index_tasks = [t for t in data.get("tasks", []) if t["source_file"] == rel_path]

    REMINDERS_PRIORITY = {"A": 1, "B": 5, "C": 9, "blocked": 5}

    tasks = []
    for t in index_tasks:
        priority = t.get("priority")
        priority_num = t.get("priority_number")
        list_name = CONTEXT_TO_LIST.get(t.get("context", "Work"), "Work")
        tasks.append({
            "hash": t["id"],
            "raw_text": t["text"],
            "clean_name": t["clean_name"],
            "completed": t["completed"],
            "priority": priority,
            "priority_number": priority_num,
            "reminders_priority": REMINDERS_PRIORITY.get(priority, 9),
            "list": list_name,
            "section": None,
            "blocked": priority == "blocked",
            "blocker_reason": extract_blocker(t["text"]) if priority == "blocked" else None,
            "due_date": extract_due_date(t["text"]),
            "line_number": t.get("source_line"),
            "source_file": t["source_file"],
            "subtasks": [],
        })
    return tasks


def load_state() -> Dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"last_refresh": None, "daily_note_date": None, "mappings": []}


def save_state(state: Dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)


def run_reminders_command(args: List) -> Dict:
    """Call reminders_manager.py with given args. Returns parsed JSON result."""
    cmd = ["python3", REMINDERS_SCRIPT] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return json.loads(result.stdout)
        else:
            return {"success": False, "error": result.stderr}
    except Exception as e:
        return {"success": False, "error": str(e)}


def normalize_reminder_name(name: str) -> str:
    """Normalize a reminder name for fuzzy comparison."""
    normalized = re.sub(r'^BLOCKED:\s*', '', name, flags=re.IGNORECASE)
    normalized = normalized.replace('✅', '').strip()
    normalized = ' '.join(normalized.split()).lower()
    return normalized


def find_existing_reminder(list_name: str, task_name: str, cached_reminders: Optional[List] = None) -> Optional[Dict]:
    """
    Find an existing reminder matching task_name in list_name.
    If cached_reminders is provided, search that list directly (avoids extra API call).
    """
    if cached_reminders is None:
        result = run_reminders_command(["list-reminders", list_name])
        if not result.get('success'):
            return None
        cached_reminders = result.get('data', [])

    normalized_task = normalize_reminder_name(task_name)
    for reminder in cached_reminders:
        normalized_reminder = normalize_reminder_name(reminder.get('name', ''))
        if normalized_task == normalized_reminder:
            return reminder
    return None


def build_reminder_body(task: Dict) -> str:
    """Build the notes body for a Reminders task. Minimal — readable on mobile."""
    lines = []
    if task['blocked'] and task['blocker_reason']:
        lines.append(f"Waiting for: {task['blocker_reason']}")
    if task.get('subtasks'):
        lines.append("Subtasks:")
        for subtask in task['subtasks']:
            check = "[x]" if subtask['completed'] else "[ ]"
            lines.append(f"- {check} {subtask['text']}")
    return "\n".join(lines)


def sync_tasks_to_reminders(tasks: List[Dict], state: Dict) -> Dict:
    """
    Sync a list of tasks to macOS Reminders.

    Optimizations:
    - Pre-fetches each Reminders list once and caches results
    - Passes cached list data to find_existing_reminder (no per-task list reads)
    - Skips fetch for tasks with known mappings (trusts state over live check)
    """
    summary = {"created": 0, "updated": 0, "completed": 0, "deleted": 0, "errors": []}

    mappings_by_hash = {m["task_hash"]: m for m in state.get("mappings", [])}

    # Pre-fetch Reminders list contents only for tasks that need duplicate detection.
    # list_cache defaults to [] per list — empty means "skip live duplicate check".
    # fetch_failed tracks lists where the API errored — skip creation for those lists
    # to avoid silent duplicates on error.
    known_hashes = set(mappings_by_hash.keys())
    unique_lists = {t["list"] for t in tasks}
    list_cache: Dict[str, List] = {l: [] for l in unique_lists}
    fetch_failed: set = set()
    lists_needing_fetch = {t["list"] for t in tasks if t["hash"] not in known_hashes and not t["completed"]}
    for list_name in lists_needing_fetch:
        result = run_reminders_command(["list-names", list_name])
        if result.get("success"):
            list_cache[list_name] = result.get("data", [])
        else:
            fetch_failed.add(list_name)

    for task in tasks:
        task_hash = task["hash"]
        task_name = task["clean_name"]
        list_name = task["list"]
        existing_mapping = mappings_by_hash.get(task_hash)

        # Skip tasks with no priority (daily habits that don't belong in Reminders)
        if task["priority"] is None:
            if not existing_mapping:
                mappings_by_hash[task_hash] = {
                    "task_hash": task_hash,
                    "reminder_id": "",
                    "task_text": task["raw_text"],
                    "list": list_name,
                    "priority": None,
                    "priority_number": None,
                    "completed": task["completed"],
                    "blocked": False,
                    "blocker_reason": None,
                    "source_file": task["source_file"],
                    "line_number": task["line_number"],
                    "subtasks": [],
                }
            continue

        if task["completed"]:
            # Task completed in vault → mark reminder done if we have a mapping
            if existing_mapping and existing_mapping.get("reminder_id"):
                result = run_reminders_command([
                    "complete", existing_mapping["reminder_id"]
                ])
                if result.get("success"):
                    existing_mapping["completed"] = True
                    summary["completed"] += 1
                else:
                    summary["errors"].append(f"complete failed: {task_name}")
            continue

        # Task is incomplete
        if existing_mapping and existing_mapping.get("reminder_id"):
            # Check if reminder still exists (only if we fetched real data)
            cached = list_cache.get(list_name)
            if cached:
                existing = find_existing_reminder(list_name, task_name, cached)
            else:
                existing = True  # Trust existing mapping
            if not existing:
                # Orphaned mapping — reminder was deleted externally; recreate
                display_name = f"BLOCKED: {task_name}" if task["blocked"] else task_name
                create_args = [
                    "create",
                    "--list", list_name,
                    "--name", display_name,
                    "--priority", str(task["reminders_priority"]),
                    "--body", build_reminder_body(task),
                ]
                if task.get("due_date"):
                    create_args += ["--due-date", task["due_date"]]
                result = run_reminders_command(create_args)
                if result.get("success"):
                    reminder_id = result.get("data", {}).get("id") or result.get("id", "")
                    existing_mapping["reminder_id"] = reminder_id
                    existing_mapping["completed"] = False
                    if list_name in list_cache and reminder_id:
                        list_cache[list_name].append({"id": reminder_id, "name": display_name})
                    summary["updated"] += 1
                else:
                    summary["errors"].append(f"recreate failed: {task_name}")
        else:
            # No existing mapping — skip if list fetch failed (avoid silent duplicates)
            if list_name in fetch_failed:
                summary["errors"].append(f"skipped (list fetch failed): {task_name}")
                continue
            # Check if reminder already exists (e.g., from a previous run without state)
            existing = find_existing_reminder(list_name, task_name, list_cache.get(list_name))
            if existing:
                mapping_entry = {
                    "task_hash": task_hash,
                    "reminder_id": existing.get("id", ""),
                    "task_text": task["raw_text"],
                    "list": list_name,
                    "priority": task["priority"],
                    "priority_number": task["priority_number"],
                    "completed": existing.get("completed", False),
                    "blocked": task["blocked"],
                    "blocker_reason": task["blocker_reason"],
                    "source_file": task["source_file"],
                    "line_number": task["line_number"],
                    "subtasks": task["subtasks"],
                }
                mappings_by_hash[task_hash] = mapping_entry
            else:
                # Create new reminder
                display_name = f"BLOCKED: {task_name}" if task["blocked"] else task_name
                create_args = [
                    "create",
                    "--list", list_name,
                    "--name", display_name,
                    "--priority", str(task["reminders_priority"]),
                    "--body", build_reminder_body(task),
                ]
                if task.get("due_date"):
                    create_args += ["--due-date", task["due_date"]]
                result = run_reminders_command(create_args)
                if result.get("success"):
                    reminder_id = result.get("data", {}).get("id") or result.get("id", "")
                    mapping_entry = {
                        "task_hash": task_hash,
                        "reminder_id": reminder_id,
                        "task_text": task["raw_text"],
                        "list": list_name,
                        "priority": task["priority"],
                        "priority_number": task["priority_number"],
                        "completed": False,
                        "blocked": task["blocked"],
                        "blocker_reason": task["blocker_reason"],
                        "source_file": task["source_file"],
                        "line_number": task["line_number"],
                        "subtasks": task["subtasks"],
                    }
                    mappings_by_hash[task_hash] = mapping_entry
                    if list_name in list_cache and reminder_id:
                        list_cache[list_name].append({"id": reminder_id, "name": display_name})
                    summary["created"] += 1
                else:
                    summary["errors"].append(f"create failed: {task_name}")

    # Cleanup: delete reminders for orphaned mappings (tasks that no longer exist,
    # e.g. because task text was edited → new hash). Without this, renamed tasks
    # leave stale reminders in the app forever.
    current_task_hashes = {task["hash"] for task in tasks}
    orphaned_hashes = [h for h in mappings_by_hash if h not in current_task_hashes]
    for old_hash in orphaned_hashes:
        old_mapping = mappings_by_hash.pop(old_hash)
        reminder_id = old_mapping.get("reminder_id", "")
        if reminder_id:  # Empty string = was a priority:None task, never created in app
            run_reminders_command(["delete", reminder_id])
            summary["deleted"] += 1

    state["mappings"] = list(mappings_by_hash.values())
    return summary


def run_sync_for_file(file_path: str):
    """Load tasks from index for file_path and sync to Reminders."""
    start = time.time()

    tasks = load_tasks_for_file(file_path)

    # Safety guard: if index returned 0 tasks but file has task markers,
    # the index is likely mid-write (race with task-index-builder).
    # Bail out rather than treating all existing mappings as orphaned.
    if not tasks and os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            if '- [ ]' in content:
                return  # File has incomplete tasks but index is empty — race condition, skip
        except IOError:
            pass

    state = load_state()

    # New day → clear mappings (will be rebuilt from task-index)
    if state.get('daily_note_date') != get_today_date():
        state['mappings'] = []
        state['daily_note_date'] = get_today_date()

    summary = sync_tasks_to_reminders(tasks, state)
    save_state(state)


def main():
    """Main hook execution."""

    # ── Async mode (called by reminders-async-wrapper.sh in background) ────────
    if "--async" in sys.argv:
        remaining = [a for a in sys.argv[1:] if a != "--async"]
        file_path = next((a for a in remaining if not a.startswith("--")), None)

        try:
            sys.stdin.read()
        except Exception:
            pass

        if file_path:
            if not os.path.isabs(file_path):
                file_path = os.path.join(VAULT_ROOT, file_path)
            # Only sync today's daily note — avoids duplicate spam from carried-over tasks
            today_note = os.path.join(VAULT_ROOT, "4-Daily", f"{get_today_date()}.md")
            if os.path.exists(file_path) and file_path == today_note:
                run_sync_for_file(file_path)
        return

    # ── Synchronous mode (direct PostToolUse hook call) ────────────────────────
    start = time.time()

    try:
        hook_input = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        print(json.dumps({"status": "success"}))
        return

    tool_name = hook_input.get('tool_name', '')
    tool_input = hook_input.get('tool_input', {})

    if tool_name not in ['Write', 'Edit']:
        print(json.dumps({"status": "success"}))
        return

    file_path = tool_input.get('file_path', '')

    if not file_path or not file_path.endswith(".md"):
        print(json.dumps({"status": "success"}))
        return

    # Only sync today's daily note
    today_note = os.path.join(VAULT_ROOT, "4-Daily", f"{get_today_date()}.md")
    if file_path != today_note:
        print(json.dumps({"status": "success"}))
        return

    tasks = load_tasks_for_file(file_path)
    state = load_state()

    if state.get('daily_note_date') != get_today_date():
        state['mappings'] = []
        state['daily_note_date'] = get_today_date()

    summary = sync_tasks_to_reminders(tasks, state)
    save_state(state)

    total_changes = summary['created'] + summary['completed'] + summary['deleted'] + summary['updated']
    if total_changes > 0:
        parts = []
        if summary['created'] > 0:
            parts.append(f"{summary['created']} created")
        if summary['updated'] > 0:
            parts.append(f"{summary['updated']} recovered")
        if summary['completed'] > 0:
            parts.append(f"{summary['completed']} completed")
        if summary['deleted'] > 0:
            parts.append(f"{summary['deleted']} deleted")

        message = f"Reminders synced: {', '.join(parts)}"
        if summary['errors']:
            message += f" ({len(summary['errors'])} errors)"
        print(json.dumps({"status": "success", "message": message}))
    else:
        print(json.dumps({"status": "success"}))


if __name__ == "__main__":
    main()

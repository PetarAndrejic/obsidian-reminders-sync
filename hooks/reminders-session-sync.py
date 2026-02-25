#!/usr/bin/env python3
"""
Reminders Session Sync - SessionStart Hook

Pulls completion status from macOS Reminders back to your Obsidian daily note
at the start of each Claude Code session.

Also auto-ingests NEW reminders added via Siri or mobile that aren't yet
mapped to the daily note.

Direction: Reminders → Obsidian (pull on session start)

Together with reminders-task-detector.py (PostToolUse), this creates full
bidirectional sync:
  Obsidian → Reminders: Automatic on every Write/Edit (task-detector)
  Reminders → Obsidian: Automatic on every session start (this hook)
    - Completion syncs: checks mapped reminders for status changes
    - New Siri/mobile items: auto-written to today's daily note

Hook Event: SessionStart
"""

import json
import os
import re
import sys
import subprocess
import time
from datetime import datetime
from pathlib import Path

# Vault root — set OBSIDIAN_VAULT_ROOT env var or it auto-detects from script location
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
VAULT_ROOT = os.environ.get("OBSIDIAN_VAULT_ROOT") or PROJECT_ROOT

# --- CONFIGURE THIS ---
# Path to your daily notes folder within the vault
DAILY_DIR = os.path.join(VAULT_ROOT, "4-Daily")  # e.g. "Daily Notes" or "Journal"
# --- END CONFIG ---

STATE_FILE = os.path.join(PROJECT_ROOT, ".claude", "state", "reminders-state.json")
REMINDERS_SCRIPT = os.path.join(PROJECT_ROOT, ".claude", "scripts", "reminders_manager.py")

# macOS Reminders priority int → daily note emoji
PRIORITY_TO_EMOJI = {
    1: "🔴",   # High
    5: "🟡",   # Medium
    9: "🟢",   # Low
    0: "🟡",   # None (default to B-priority)
}

# --- CONFIGURE THIS ---
# Map Reminders list names → daily note section headers.
# Keys must match your Reminders list names; values must match your daily note ### headers.
LIST_TO_SECTION = {
    "Work": "Work",
    "Personal": "Personal",
}
# --- END CONFIG ---


def get_today_date():
    return datetime.now().strftime("%Y-%m-%d")


def get_daily_note_path():
    return os.path.join(DAILY_DIR, f"{get_today_date()}.md")


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"last_refresh": None, "daily_note_date": None, "mappings": []}


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)


def run_reminders_command(args: list) -> dict:
    cmd = ["python3", REMINDERS_SCRIPT] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return json.loads(result.stdout)
        else:
            return {"success": False, "error": result.stderr}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_reminder_status(reminder_id: str) -> dict:
    result = run_reminders_command(["get", reminder_id])
    if result.get("success"):
        return result.get("data", {})
    return {}


def update_daily_note_task(file_path: str, task_text: str, mark_complete: bool) -> bool:
    """Update a task's completion status in the daily note."""
    if not os.path.exists(file_path):
        return False

    try:
        with open(file_path, 'r') as f:
            content = f.read()
    except IOError:
        return False

    escaped_text = re.escape(task_text)

    if mark_complete:
        pattern = rf'^(\s*)- \[ \] ({escaped_text})'
        replacement = r'\1- [x] \2 ✅'
    else:
        pattern = rf'^(\s*)- \[x\] ({escaped_text})(\s*✅)?'
        replacement = r'\1- [ ] \2'

    new_content, count = re.subn(pattern, replacement, content, flags=re.MULTILINE)

    if count > 0:
        try:
            with open(file_path, 'w') as f:
                f.write(new_content)
            return True
        except IOError:
            return False

    return False


def get_mapped_ids(state: dict) -> set:
    return {m.get("reminder_id") for m in state.get("mappings", []) if m.get("reminder_id")}


def fetch_all_incomplete_reminders() -> list:
    result = run_reminders_command(["get-incomplete"])
    if result.get("success") and isinstance(result.get("data"), list):
        return result["data"]
    return []


def reminder_priority_to_emoji(priority: int) -> str:
    return PRIORITY_TO_EMOJI.get(priority, "🟡")


def reminder_list_to_section(list_name: str) -> str:
    return LIST_TO_SECTION.get(list_name, "Personal")


def normalize_task_name(name: str) -> str:
    name = re.sub(r'^BLOCKED:\s*', '', name, flags=re.IGNORECASE)
    return ' '.join(name.split())


def find_new_siri_reminders(state: dict, all_incomplete: list) -> list:
    """
    Find reminders that exist in the app but are NOT in state mappings.
    These are items added via Siri, iPhone, or Apple Watch.

    Guard: if state has no mappings (cleared/reset state), skip entirely.
    Without this, a post-recovery state reset causes all current reminders
    to appear as "new Siri items" and get duplicated into the daily note.
    """
    if not state.get("mappings"):
        return []
    mapped_ids = get_mapped_ids(state)
    new_reminders = []
    for reminder in all_incomplete:
        rid = reminder.get("id")
        if rid and rid not in mapped_ids:
            new_reminders.append(reminder)
    return new_reminders


def find_section_insertion_point(content: str, section_name: str) -> int:
    """
    Find the best position to insert a new task under a given section.
    Returns the character index where the task line should be inserted.
    Returns -1 if section not found.
    """
    patterns = [
        rf'^#{2,4}\s+{re.escape(section_name)}\b',
    ]
    for pat in patterns:
        match = re.search(pat, content, re.MULTILINE | re.IGNORECASE)
        if match:
            last_task_pos = match.end()
            running_pos = match.end()
            after_section = content[match.end():]
            lines_after = after_section.split('\n')
            in_task_block = False
            for line in lines_after:
                running_pos += len(line) + 1
                stripped = line.strip()
                if stripped.startswith('- [ ]') or stripped.startswith('- [x]'):
                    in_task_block = True
                    last_task_pos = running_pos
                elif in_task_block and stripped == '':
                    return last_task_pos - 1
                elif stripped.startswith('#'):
                    return last_task_pos
            return last_task_pos
    return -1


def append_new_reminders_to_daily_note(
    file_path: str,
    new_reminders: list,
    state: dict,
    today: str
) -> list:
    """
    Write new Siri/mobile reminders into today's daily note.
    Appends under the correct section or creates a 'New from Reminders' section.
    """
    if not os.path.exists(file_path):
        return []

    try:
        with open(file_path, 'r') as f:
            content = f.read()
    except IOError:
        return []

    added_mappings = []

    for reminder in new_reminders:
        name = reminder.get("name", "").strip()
        if not name:
            continue

        # Skip BLOCKED tasks (they're pushed from Obsidian, not created in Reminders)
        if name.upper().startswith("BLOCKED:"):
            continue

        priority = reminder.get("priority", 0)
        list_name = reminder.get("list", "Personal")
        reminder_id = reminder.get("id", "")

        emoji = reminder_priority_to_emoji(priority)
        section = reminder_list_to_section(list_name)
        task_line = f"- [ ] {emoji} {name}"

        # Check if already present (avoid duplicates)
        normalized = normalize_task_name(name)
        if normalized.lower() in content.lower():
            mapping = {
                "task_hash": hash(normalized),
                "reminder_id": reminder_id,
                "task_text": f"{emoji} {name}",
                "company": list_name,
                "priority": "A" if priority == 1 else ("B" if priority in (5, 0) else "C"),
                "completed": False,
                "last_synced": datetime.now().isoformat(),
                "added_from_siri": True,
                "skipped_duplicate": True,
            }
            state["mappings"].append(mapping)
            added_mappings.append(mapping)
            continue

        insert_pos = find_section_insertion_point(content, section)

        if insert_pos == -1:
            # Section doesn't exist — append under Work section or end of file
            work_match = re.search(r'^## Work\b', content, re.MULTILINE)
            if work_match:
                after_work = content[work_match.end():]
                next_h2 = re.search(r'^## ', after_work, re.MULTILINE)
                if next_h2:
                    insert_pos = work_match.end() + next_h2.start()
                    task_line = f"\n### {section}\n\n{task_line}\n"
                else:
                    insert_pos = len(content)
                    task_line = f"\n\n### {section}\n\n{task_line}\n"
            else:
                insert_pos = len(content)
                task_line = f"\n\n### New from Reminders\n\n{task_line}\n"

            content = content[:insert_pos] + task_line + content[insert_pos:]
        else:
            content = content[:insert_pos] + task_line + "\n" + content[insert_pos:]

        mapping = {
            "task_hash": hash(normalize_task_name(name)),
            "reminder_id": reminder_id,
            "task_text": f"{emoji} {name}",
            "company": list_name,
            "priority": "A" if priority == 1 else ("B" if priority in (5, 0) else "C"),
            "completed": False,
            "last_synced": datetime.now().isoformat(),
            "added_from_siri": True,
        }
        state["mappings"].append(mapping)
        added_mappings.append(mapping)

    if added_mappings:
        try:
            with open(file_path, 'w') as f:
                f.write(content)
        except IOError:
            return []

    return added_mappings


def sync_completions_from_reminders(state: dict) -> dict:
    """Check Reminders for completions and sync back to Obsidian."""
    summary = {
        "synced_completions": 0,
        "synced_uncomletions": 0,
        "errors": [],
        "tasks_synced": []
    }

    mappings = state.get("mappings", [])
    if not mappings:
        return summary

    daily_note_path = get_daily_note_path()
    today = get_today_date()

    if state.get("daily_note_date") != today:
        return summary

    for mapping in mappings:
        reminder_id = mapping.get("reminder_id")
        if not reminder_id:
            continue

        reminder = get_reminder_status(reminder_id)
        if not reminder:
            continue

        reminder_completed = reminder.get("completed", False)
        state_completed = mapping.get("completed", False)

        if reminder_completed and not state_completed:
            task_text = mapping.get("task_text", "")
            if update_daily_note_task(daily_note_path, task_text, mark_complete=True):
                mapping["completed"] = True
                mapping["last_modified"] = datetime.now().isoformat()
                mapping["synced_from_reminders"] = datetime.now().isoformat()
                summary["synced_completions"] += 1
                summary["tasks_synced"].append(mapping.get("task_text", "")[:50])
            else:
                summary["errors"].append(f"Failed to mark complete: {task_text[:30]}...")

        elif not reminder_completed and state_completed:
            task_text = mapping.get("task_text", "")
            if update_daily_note_task(daily_note_path, task_text, mark_complete=False):
                mapping["completed"] = False
                mapping["last_modified"] = datetime.now().isoformat()
                mapping["synced_from_reminders"] = datetime.now().isoformat()
                summary["synced_uncomletions"] += 1
                summary["tasks_synced"].append(f"(uncompleted) {mapping.get('task_text', '')[:40]}")
            else:
                summary["errors"].append(f"Failed to mark incomplete: {task_text[:30]}...")

    return summary


def run_sync():
    """Run the actual sync logic (called directly or via --async)."""
    state = load_state()
    today = get_today_date()
    daily_note_path = get_daily_note_path()
    state_changed = False

    # Pull new Siri/mobile items into daily note
    try:
        all_incomplete = fetch_all_incomplete_reminders()
        new_reminders = find_new_siri_reminders(state, all_incomplete)
        if new_reminders and os.path.exists(daily_note_path):
            added = append_new_reminders_to_daily_note(daily_note_path, new_reminders, state, today)
            if added:
                state["last_refresh"] = datetime.now().isoformat()
                state["daily_note_date"] = today
                state_changed = True
    except Exception:
        pass  # Don't let Siri sync errors block completion sync

    # Sync completions from Reminders → Obsidian
    completion_summary = {"synced_completions": 0, "synced_uncomletions": 0, "tasks_synced": []}
    if state.get("daily_note_date") == today and state.get("mappings"):
        completion_summary = sync_completions_from_reminders(state)
        if completion_summary["synced_completions"] > 0 or completion_summary["synced_uncomletions"] > 0:
            state["last_refresh"] = datetime.now().isoformat()
            state_changed = True

    if state_changed:
        save_state(state)


def main():
    """Main hook execution."""
    if "--async" in sys.argv:
        try:
            sys.stdin.read()
        except Exception:
            pass
        run_sync()
        return

    start_time = time.time()

    try:
        hook_input = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        hook_input = {}

    state = load_state()
    today = get_today_date()
    daily_note_path = get_daily_note_path()
    state_changed = False
    siri_added = []

    # Pull new Siri/mobile items into daily note
    try:
        all_incomplete = fetch_all_incomplete_reminders()
        new_reminders = find_new_siri_reminders(state, all_incomplete)
        if new_reminders and os.path.exists(daily_note_path):
            siri_added = append_new_reminders_to_daily_note(daily_note_path, new_reminders, state, today)
            if siri_added:
                state["last_refresh"] = datetime.now().isoformat()
                state["daily_note_date"] = today
                state_changed = True
    except Exception:
        pass

    # Sync completions
    completion_summary = {"synced_completions": 0, "synced_uncomletions": 0, "tasks_synced": [], "errors": []}
    if state.get("daily_note_date") == today and state.get("mappings"):
        completion_summary = sync_completions_from_reminders(state)
        if completion_summary["synced_completions"] > 0 or completion_summary["synced_uncomletions"] > 0:
            state["last_refresh"] = datetime.now().isoformat()
            state_changed = True

    if state_changed:
        save_state(state)

    elapsed = time.time() - start_time
    messages = []

    if siri_added:
        real_adds = [m for m in siri_added if not m.get("skipped_duplicate")]
        if real_adds:
            names = [m.get("task_text", "")[:40] for m in real_adds[:3]]
            extra = f" (+{len(real_adds) - 3} more)" if len(real_adds) > 3 else ""
            messages.append(f"[Reminders] Added {len(real_adds)} new Siri/mobile task(s): {', '.join(names)}{extra}")

    total_synced = completion_summary["synced_completions"] + completion_summary["synced_uncomletions"]
    if total_synced > 0:
        tasks_list = ", ".join(completion_summary["tasks_synced"][:3])
        if len(completion_summary["tasks_synced"]) > 3:
            tasks_list += f" (+{len(completion_summary['tasks_synced']) - 3} more)"
        messages.append(f"[Reminders] Synced {total_synced} completion(s): {tasks_list}")

    if messages:
        output = {"systemMessage": f"{' | '.join(messages)} ({elapsed:.2f}s)"}
    else:
        output = {"status": "success", "timing": f"{elapsed:.2f}s"}

    print(json.dumps(output))


if __name__ == "__main__":
    main()

# obsidian-reminders-sync

Bidirectional sync between Obsidian daily notes and macOS Reminders.app — built as a Claude Code hook system.

Write a task in Obsidian and it appears in Reminders within seconds. Complete it on your iPhone or Apple Watch and it syncs back to your vault on the next session. Add a task via Siri and it lands in today's daily note automatically.

## How It Works

| Direction | Trigger | File |
|-----------|---------|------|
| Obsidian → Reminders | Every Write/Edit in Claude Code | `hooks/reminders-task-detector.py` |
| Reminders → Obsidian | Every Claude Code session start | `hooks/reminders-session-sync.py` |
| Reminders → Obsidian (live) | Every 60s (optional daemon) | `scripts/reminders-sync-daemon.py` |

All three share a single state file (`.claude/state/reminders-state.json`) for dedup and race condition protection.

## Task Priority Convention

Tasks must use emoji priority markers in your daily note:

```markdown
- [ ] 🔴 Urgent thing that must happen today
- [ ] 🟡 Should happen today
- [ ] 🟢 Nice to have
- [ ] 🔵 Blocked — Waiting for: someone to respond
```

These map to Reminders.app priority: 🔴 = High, 🟡 = Medium, 🟢 = Low.

Blocked tasks (🔵) appear in Reminders with a `BLOCKED:` prefix and include the blocker reason in the notes field.

Tasks with no priority emoji are ignored by the sync (useful for habits/rituals).

## Setup

### 1. Install

Clone or copy these files into your vault's `.claude/` directory:

```
your-vault/
└── .claude/
    ├── hooks/
    │   ├── reminders-async-wrapper.sh
    │   ├── reminders-task-detector.py
    │   ├── reminders-session-sync.py
    │   └── task_parser.py
    └── scripts/
        ├── reminders_manager.py
        └── reminders-sync-daemon.py  (optional)
```

### 2. Configure

Edit the `# --- CONFIGURE THIS ---` sections in each file:

**`scripts/reminders_manager.py`**
```python
REMINDER_LISTS = ["Work", "Personal"]  # Your Reminders list names
```

**`hooks/reminders-task-detector.py`**
```python
CONTEXT_TO_LIST = {
    "Work": "Work",       # ### Work section → Work list
    "Personal": "Personal",
    "Family": "Personal",
    "Health": "Personal",
}
```

**`hooks/reminders-session-sync.py`**
```python
DAILY_DIR = os.path.join(VAULT_ROOT, "4-Daily")  # Your daily notes folder

LIST_TO_SECTION = {
    "Work": "Work",       # Work list → ### Work section
    "Personal": "Personal",
}
```

**`hooks/task_parser.py`**
```python
DAILY_DIR = os.path.join(VAULT_ROOT, "4-Daily")      # Your daily notes folder
PROJECTS_DIR = os.path.join(VAULT_ROOT, "1-Projects", "Current")
```

### 3. Create Reminders lists

```bash
python3 .claude/scripts/reminders_manager.py setup-lists
```

### 4. Wire up Claude Code hooks

Add to your `.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/reminders-async-wrapper.sh\" reminders-session-sync.py"
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/reminders-async-wrapper.sh\" reminders-task-detector.py"
      }
    ]
  }
}
```

### 5. Set vault root (optional)

By default, the scripts auto-detect the vault root from their file path. If you need to override:

```bash
export OBSIDIAN_VAULT_ROOT=/path/to/your/vault
```

## Architecture

### Deduplication

The task detector pre-fetches each Reminders list **once** per sync cycle and caches results. Tasks with known state mappings skip the live fetch entirely — the cache is only used for new tasks that need duplicate detection.

### Race condition protection

If a file has incomplete tasks (`- [ ]`) but the task index returns empty (the index builder hasn't run yet), the sync bails out rather than treating all existing mappings as orphaned. This prevents mass deletion on a slow index write.

### State file

`.claude/state/reminders-state.json` tracks:
- `daily_note_date`: clears mappings on day rollover (fresh start each day)
- `mappings`: task hash → Reminder ID mapping for all known tasks

Recovery if state gets corrupted:
```bash
python3 .claude/scripts/reminders_manager.py clear-all
# Then reset state file to: {"last_refresh": null, "daily_note_date": null, "mappings": []}
```

### Async wrapper

`reminders-async-wrapper.sh` runs sync hooks in the background so they never block Claude Code. It:
1. Reads stdin (hook JSON) and saves to a temp file
2. Launches the Python script with `--async` in the background via `nohup + disown`
3. Returns `exit 0` immediately

### Optional: sync daemon

For real-time Reminders → Obsidian sync (without waiting for a session start), run the daemon:

```bash
python3 .claude/scripts/reminders-sync-daemon.py
```

Or add as a launchd service (`com.your-name.reminders-sync`). The daemon routes AppleScript through `/bin/bash` to inherit Full Disk Access — required when running under launchd's restricted session.

## Requirements

- macOS (uses AppleScript to talk to Reminders.app)
- Python 3.8+
- Claude Code with hooks support
- Obsidian vault with daily notes

## Limitations

- macOS only — Reminders.app is macOS/iOS native
- Only syncs today's daily note (not archived notes), to avoid duplicate spam from carried-over tasks
- Task matching uses content hashing — editing task text creates a new hash and a new Reminder (old one is cleaned up)
- Due dates use `📅 YYYY-MM-DD` format (Obsidian Tasks plugin convention)

## Depends On

This hook reads from a `task-index.json` file built by a separate `task-index-builder.py` hook (not included here). The index builder runs on every Write/Edit and produces a structured JSON of all tasks in the vault. If you don't have an index builder, you can swap `load_tasks_for_file()` in `reminders-task-detector.py` to parse the file directly using `extract_tasks_full()` from `task_parser.py`.

## License

MIT

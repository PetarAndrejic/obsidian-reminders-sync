#!/usr/bin/env python3
"""
Shared Task Parser Module

Centralized task parsing logic used by the PostToolUse and SessionStart hooks.
Provides consistent task extraction, priority detection, section mapping,
and file type identification.

Priority emoji convention (configurable in PRIORITY_PATTERNS below):
  🔴 = A (high / must do today)
  🟡 = B (should do today)
  🟢 = C (nice to have)
  🔵 = Blocked (waiting on something external)
"""

import hashlib
import os
import re
from pathlib import Path
from typing import Optional

# Vault root — set OBSIDIAN_VAULT_ROOT env var or it auto-detects from script location
VAULT_ROOT = os.environ.get("OBSIDIAN_VAULT_ROOT") or str(Path(__file__).resolve().parent.parent.parent)

# --- CONFIGURE THIS ---
# Paths within your vault where daily notes and project files live.
# These should match your Obsidian folder structure.
DAILY_DIR = os.path.join(VAULT_ROOT, "4-Daily")       # e.g. "Daily Notes" or "Journal"
PROJECTS_DIR = os.path.join(VAULT_ROOT, "1-Projects", "Current")  # e.g. "Projects/Active"
# --- END CONFIG ---

# Compiled regex patterns
TASK_PATTERN = re.compile(r'^(\s*)- \[([ x])\] (.+)$', re.MULTILINE)
PROJECT_SECTION_PATTERN = re.compile(r'^####\s+\[\[([^\]]+)\]\]', re.MULTILINE)
H3_SECTION_PATTERN = re.compile(r'^###\s+(.+)$', re.MULTILINE)
PRIORITY_EMOJI_PATTERN = re.compile(r'[🔴🟡🟢🔵]')

# --- CONFIGURE THIS ---
# Map priority emoji prefixes to priority letters.
# Modify if you use different emoji or a different priority system.
PRIORITY_PATTERNS = {
    'A': re.compile(r'^🔴(\d)?\.?\s*'),
    'B': re.compile(r'^🟡\s*'),
    'C': re.compile(r'^🟢\s*'),
    'blocked': re.compile(r'^🔵\s*'),
}
# --- END CONFIG ---


def is_daily_note(file_path: str) -> bool:
    """Check if file is a daily note."""
    return DAILY_DIR in file_path and file_path.endswith('.md')


def is_project_file(file_path: str) -> bool:
    """Check if file is a project file."""
    return PROJECTS_DIR in file_path and file_path.endswith('.md')


def is_todays_daily_note(file_path: str, today_date: str) -> bool:
    """Check if the file is today's daily note."""
    today_path = os.path.join(DAILY_DIR, f"{today_date}.md")
    return os.path.normpath(file_path) == os.path.normpath(today_path)


def detect_priority(task_text: str) -> dict:
    """
    Detect priority from task text.

    Returns dict with:
        letter: 'A', 'B', 'C', 'blocked', or None
        number: Priority number (for A-tasks), or None
        reminders_priority: macOS Reminders priority value (1=high, 5=medium, 9=low)
    """
    for priority, pattern in PRIORITY_PATTERNS.items():
        match = pattern.match(task_text)
        if match:
            if priority == 'A':
                num = match.group(1) if match.group(1) else None
                return {'letter': 'A', 'number': num, 'reminders_priority': 1}
            elif priority == 'B':
                return {'letter': 'B', 'number': None, 'reminders_priority': 5}
            elif priority == 'C':
                return {'letter': 'C', 'number': None, 'reminders_priority': 9}
            elif priority == 'blocked':
                return {'letter': 'blocked', 'number': None, 'reminders_priority': 5}
    return {'letter': None, 'number': None, 'reminders_priority': 9}


def clean_task_text(task_text: str) -> str:
    """
    Strip priority emoji, numbering, and metadata from task text.
    Used for matching tasks across files.
    """
    cleaned = PRIORITY_EMOJI_PATTERN.sub('', task_text).strip()
    cleaned = re.sub(r'^\d+\.\s*', '', cleaned)
    return cleaned


def clean_task_name(task_text: str) -> str:
    """
    Clean task text for display (e.g., Reminders name).
    Removes priority emoji, due dates, wiki links, and trailing metadata.
    """
    cleaned = re.sub(r'^[🔴🟡🟢🔵📅]\d*\.?\s*', '', task_text)
    cleaned = re.sub(r'\s*-?\s*Company:\s*[\w\s]+$', '', cleaned)
    cleaned = re.sub(r'\s*-?\s*Waiting for:\s*.+$', '', cleaned)
    cleaned = re.sub(r'\s*📅\s*\d{4}-\d{2}-\d{2}', '', cleaned)
    # [[Page|Alias]] → Alias, [[Page]] → Page
    cleaned = re.sub(r'\[\[([^\]|]+)\|([^\]]+)\]\]', r'\2', cleaned)
    cleaned = re.sub(r'\[\[([^\]]+)\]\]', r'\1', cleaned)
    return cleaned.strip()


def generate_task_hash(task_text: str) -> str:
    """Generate a stable hash for task matching across files."""
    normalized = re.sub(r'^[🔴🟡🟢🔵📅]\d*\.?\s*', '', task_text)
    normalized = re.sub(r'\s*-?\s*Company:\s*[\w\s]+$', '', normalized)
    normalized = re.sub(r'\s*-?\s*Waiting for:\s*.+$', '', normalized)
    normalized = ' '.join(normalized.split()).lower()
    return hashlib.md5(normalized.encode()).hexdigest()[:12]


def extract_blocker(task_text: str) -> Optional[str]:
    """Extract blocker reason from task text."""
    match = re.search(r'Waiting for:\s*(.+)$', task_text)
    return match.group(1).strip() if match else None


def find_project_section(content: str, position: int) -> Optional[str]:
    """
    Find which #### [[Project]] section a task belongs to (for daily notes).
    Returns the project name from the wiki-link.
    """
    sections = list(PROJECT_SECTION_PATTERN.finditer(content))
    if not sections:
        return None

    for i, section in enumerate(sections):
        section_start = section.start()
        if i + 1 < len(sections):
            next_start = sections[i + 1].start()
            if section_start <= position < next_start:
                return section.group(1)
        else:
            if position >= section_start:
                header_end = content.find('\n', section_start)
                if header_end == -1:
                    header_end = section_start
                between = content[header_end:position]
                if re.search(r'^#{1,3}\s', between, re.MULTILINE):
                    return None
                return section.group(1)

    return None


def find_h3_section(content: str, position: int) -> Optional[str]:
    """
    Find which ### Section a task belongs to (for context grouping).
    Returns the section header text.
    """
    sections = list(H3_SECTION_PATTERN.finditer(content))
    current_section = None

    for section in sections:
        if section.start() <= position:
            current_section = section.group(1)
        else:
            break

    return current_section


def extract_tasks_simple(content: str) -> list:
    """
    Simple task extraction for sync detection.
    Returns list of dicts with: raw, text, clean_text, completed, position.
    """
    tasks = []
    for match in TASK_PATTERN.finditer(content):
        indent = len(match.group(1))
        if indent >= 2:
            continue  # Skip subtasks
        completed = match.group(2) == 'x'
        text = match.group(3).strip()
        tasks.append({
            'raw': match.group(0),
            'text': text,
            'clean_text': clean_task_text(text),
            'completed': completed,
            'position': match.start()
        })
    return tasks


def extract_tasks_full(content: str, file_path: str) -> list:
    """
    Full task extraction with subtasks, priority, section, blockers.
    Used by reminders-task-detector for rich sync.
    Returns list of dicts with all task metadata.
    """
    tasks = []
    parent_task = None
    parent_section = None
    parent_line = None

    for match in TASK_PATTERN.finditer(content):
        indent = len(match.group(1))
        completed = match.group(2) == 'x'
        task_text = match.group(3).strip()

        current_line = content[:match.start()].count('\n') + 1
        current_section = find_h3_section(content, match.start())

        if indent >= 2:
            if (parent_task and
                parent_section == current_section and
                parent_line is not None and
                current_line - parent_line <= 10):
                parent_task['subtasks'].append({
                    'text': task_text,
                    'completed': completed
                })
            continue

        priority_info = detect_priority(task_text)

        task = {
            'hash': generate_task_hash(task_text),
            'raw_text': task_text,
            'clean_name': clean_task_name(task_text),
            'clean_text': clean_task_text(task_text),
            'completed': completed,
            'priority': priority_info['letter'],
            'priority_number': priority_info['number'],
            'reminders_priority': priority_info['reminders_priority'],
            'section': current_section,
            'blocked': priority_info['letter'] == 'blocked',
            'blocker_reason': extract_blocker(task_text) if priority_info['letter'] == 'blocked' else None,
            'line_number': current_line,
            'position': match.start(),
            'source_file': file_path,
            'subtasks': []
        }

        parent_task = task
        parent_section = current_section
        parent_line = current_line
        tasks.append(task)

    return tasks

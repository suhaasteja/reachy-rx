"""Medication schedule and reminder system.

Reads the medicine plan from a Google Sheet (single source of truth) via the
public gviz JSON endpoint (no auth required). Tracks which medications have
been taken today in a local JSON file (medication_taken.json).

The Google Sheet is expected to have columns:
    Medication | Dosage | Form | Frequency | Times | Instructions | Condition

Usage:
    from medication_reminder import MedicationReminder

    reminder = MedicationReminder(sheet_id="19DZLGsry...")
    due = reminder.check_and_remind()         # meds due now, not yet reminded
    reminder.mark_taken("Lisinopril", "08:00") # record that med was taken
"""

import json
import logging
import re
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_SHEET_URL = "https://docs.google.com/spreadsheets/d/19DZLGsryVJVpGW-Vg1SRLFY2nNmMhFYPomM8s0RYOhE/edit?usp=sharing"
TAKEN_LOG_PATH = Path(__file__).parent / "medication_taken.json"


def _extract_sheet_id(url_or_id: str) -> str:
    """Extract the spreadsheet ID from a Google Sheets URL or pass through a bare ID."""
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url_or_id)
    if m:
        return m.group(1)
    # Assume it's already a bare ID
    return url_or_id.strip()


# ---------------------------------------------------------------------------
# Google Sheet reader (public, no auth)
# ---------------------------------------------------------------------------


def _parse_gviz_date(val: str) -> Optional[str]:
    """Convert gviz Date(y,m,d,H,M,S) to 'HH:MM'. Months are 0-indexed."""
    m = re.match(r"Date\(\d+,\d+,\d+,(\d+),(\d+),\d+\)", str(val))
    if m:
        return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
    return None


def fetch_schedule_from_sheet(sheet_id: str) -> list[dict]:
    """Fetch the medication schedule from a public Google Sheet.

    Uses the gviz JSON endpoint — works without any auth on sheets that
    are shared as 'anyone with the link can view/edit'.
    """
    url = (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}"
        f"/gviz/tq?tqx=out:json"
    )
    try:
        resp = urllib.request.urlopen(url, timeout=15)
        raw = resp.read().decode("utf-8")
    except Exception as e:
        logger.error(f"Failed to fetch sheet {sheet_id}: {e}")
        return []

    # Strip the google.visualization.Query.setResponse(...); wrapper
    try:
        json_str = raw.split("setResponse(", 1)[1].rsplit(");", 1)[0]
        data = json.loads(json_str)
    except (IndexError, json.JSONDecodeError) as e:
        logger.error(f"Failed to parse gviz response: {e}")
        return []
    table = data.get("table", {})

    cols = [c.get("label", f"Col{i}") for i, c in enumerate(table.get("cols", []))]
    rows = table.get("rows", [])

    schedule = []
    for row in rows:
        cells = row.get("c", [])
        entry = {}
        for i, col_name in enumerate(cols):
            if i < len(cells) and cells[i] is not None:
                val = cells[i].get("v")
                # gviz also provides a formatted display value ("f") which
                # can be useful when the raw value is a Date() object
                fval = cells[i].get("f")
                # Google Sheets stores times as Date(...) objects via gviz
                if col_name == "Times" and val and str(val).startswith("Date("):
                    val = _parse_gviz_date(val)
                elif col_name == "Times" and fval:
                    # Prefer the formatted display value (e.g. "08:00" or "8:00:00 AM")
                    val = fval
                elif col_name == "Times" and val and isinstance(val, str):
                    # Plain text times like "08:00,18:00" — keep as-is
                    pass
                entry[col_name] = val
            else:
                entry[col_name] = None
        schedule.append(entry)

    logger.info(f"Fetched {len(schedule)} medications from Google Sheet")
    return schedule


# ---------------------------------------------------------------------------
# Local taken-tracking
# ---------------------------------------------------------------------------


def _load_taken_log() -> dict:
    """Load the taken log. Structure: {"YYYY-MM-DD": {"Med@HH:MM": "ISO timestamp"}}"""
    if TAKEN_LOG_PATH.exists():
        try:
            return json.loads(TAKEN_LOG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_taken_log(log: dict) -> None:
    TAKEN_LOG_PATH.write_text(json.dumps(log, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _parse_times(times_val) -> list[str]:
    """Parse times field into a list of 'HH:MM' strings."""
    if times_val is None:
        return []
    times_str = str(times_val)
    return [t.strip() for t in times_str.split(",") if t.strip()]


def _time_to_minutes(time_str: str) -> int:
    """Convert 'HH:MM' to minutes since midnight."""
    parts = time_str.split(":")
    return int(parts[0]) * 60 + int(parts[1])


# ---------------------------------------------------------------------------
# Reminder class
# ---------------------------------------------------------------------------


class MedicationReminder:
    """Reads schedule from Google Sheet, checks what's due, tracks taken status."""

    def __init__(
        self,
        sheet_url: str = DEFAULT_SHEET_URL,
        window_minutes: int = 15,
    ):
        self.sheet_id = _extract_sheet_id(sheet_url)
        self.sheet_url = sheet_url
        self.window_minutes = window_minutes
        self._schedule: list[dict] = []
        self._last_fetch: Optional[datetime] = None
        self._reminded: dict[str, int] = {}  # key → nag count

    def _load_schedule(self) -> list[dict]:
        """Fetch schedule from Google Sheet. Caches for 30 seconds."""
        now = datetime.now()
        if self._last_fetch and (now - self._last_fetch).total_seconds() < 30:
            return self._schedule

        self._schedule = fetch_schedule_from_sheet(self.sheet_id)
        self._last_fetch = now
        return self._schedule

    def get_due_medications(self, now: Optional[datetime] = None) -> list[dict]:
        """Return medications due right now (within ±window_minutes).

        Excludes medications already marked as taken today.
        """
        schedule = self._load_schedule()
        if now is None:
            now = datetime.now()

        today = now.strftime("%Y-%m-%d")
        taken_log = _load_taken_log()
        taken_today = taken_log.get(today, {})

        now_minutes = now.hour * 60 + now.minute
        due = []

        for med in schedule:
            med_name = med.get("Medication", "unknown")
            times = _parse_times(med.get("Times"))
            for t in times:
                try:
                    target_minutes = _time_to_minutes(t)
                except (ValueError, IndexError):
                    continue

                key = f"{med_name}@{t}"
                if key in taken_today:
                    continue  # already taken today

                diff = target_minutes - now_minutes
                if abs(diff) <= self.window_minutes:
                    entry = dict(med)
                    entry["due_time"] = t
                    entry["minutes_until"] = diff
                    due.append(entry)

        return due

    def check_and_remind(self, now: Optional[datetime] = None) -> list[dict]:
        """Return meds due NOW that haven't been taken yet.

        Unlike the old version, this returns due meds EVERY call — the robot
        keeps nagging until mark_taken() is called. A 'nag_count' field
        tracks how many times each med has been reminded this session.
        """
        due = self.get_due_medications(now=now)

        for med in due:
            key = f"{med['Medication']}@{med['due_time']}"
            if key not in self._reminded:
                self._reminded[key] = 0
            self._reminded[key] += 1
            med["nag_count"] = self._reminded[key]

        return due

    def mark_taken(self, medication_name: str, due_time: str) -> None:
        """Mark a medication as taken. Persists to medication_taken.json and stops nagging."""
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        key = f"{medication_name}@{due_time}"

        log = _load_taken_log()
        if today not in log:
            log[today] = {}
        log[today][key] = now.isoformat()
        _save_taken_log(log)

        # Stop nagging for this med
        self._reminded.pop(key, None)

        print(f"  ✅ Marked {medication_name} ({due_time}) as taken at {now.strftime('%H:%M')}")

    def get_taken_today(self) -> dict[str, str]:
        """Return all meds marked as taken today. {key: timestamp}"""
        today = datetime.now().strftime("%Y-%m-%d")
        log = _load_taken_log()
        return log.get(today, {})

    def get_schedule_with_status(self) -> list[dict]:
        """Return the full schedule with a 'taken' boolean for each time slot today."""
        schedule = self._load_schedule()
        taken_today = self.get_taken_today()
        result = []

        for med in schedule:
            med_name = med.get("Medication", "unknown")
            times = _parse_times(med.get("Times"))
            for t in times:
                entry = dict(med)
                entry["due_time"] = t
                key = f"{med_name}@{t}"
                entry["taken"] = key in taken_today
                entry["taken_at"] = taken_today.get(key)
                result.append(entry)

        return result

    def reset_reminders(self) -> None:
        """Clear the in-memory reminded set (e.g. for testing)."""
        self._reminded.clear()

    def format_reminder(self, med: dict) -> str:
        """Format a medication dict into a spoken reminder string."""
        name = med.get("Medication", "unknown")
        dosage = med.get("Dosage", "")
        form = med.get("Form", "")
        instructions = med.get("Instructions", "")
        condition = med.get("Condition", "")

        parts = [f"Time to take your {name}"]
        if dosage:
            parts[0] += f" {dosage}"
        if form:
            parts[0] += f" {form}"
        parts[0] += "!"

        if condition:
            parts.append(f"This is for your {condition}.")
        if instructions:
            parts.append(f"Remember: {instructions}")

        return " ".join(parts)

#!/usr/bin/env python3
"""Generate a deterministic contribution-history Git branch."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


START = date(2010, 1, 3)
END = date.today()
REAL_HISTORY_START = date(2014, 5, 1)
ASSISTED_ERA_START = date(2025, 6, 1)
DEFAULT_AUTHOR_NAME = "Example Developer"
DEFAULT_AUTHOR_EMAIL = "developer@example.com"
REF = "refs/heads/main"
INITIAL_SEED_FILES = [
    ".gitignore",
]
METADATA_FILES = [
    "work_history.json",
    "travel_history.json",
]
FINAL_TOOLING_FILES = [
    ".github/workflows/daily-contribution.yml",
    "README.md",
    "existing_contributions.json",
    "generate_history.py",
    "scripts/daily_contribution.py",
    "scripts/fetch_existing_contributions.py",
]
FINAL_CURRENT_FILES = [*METADATA_FILES, *FINAL_TOOLING_FILES]
SEED_FILES = [*INITIAL_SEED_FILES, *FINAL_CURRENT_FILES]
COMMIT_TIMES = [
    time(12, 0),
    time(13, 13),
    time(14, 37),
    time(16, 4),
    time(17, 42),
    time(19, 3),
    time(20, 26),
    time(21, 49),
    time(22, 12),
]
ANNUAL_TOTAL_TARGETS = {
    2021: 240,
    2022: 240,
    2023: 240,
    2024: 260,
}


@dataclass(frozen=True)
class PlannedCommit:
    day: date
    sequence: int
    total_for_day: int
    era: str
    category: str
    location: str
    timezone: str
    roles: tuple[dict[str, str], ...]
    existing_contributions: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=START.isoformat())
    parser.add_argument("--end", default=END.isoformat())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--import-history", action="store_true")
    parser.add_argument("--existing-contributions", default="existing_contributions.json")
    parser.add_argument("--work-history", default="work_history.json")
    parser.add_argument("--travel-history", default="travel_history.json")
    return parser.parse_args()


def run_git(args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout


def git_value(args: list[str], fallback: str) -> str:
    try:
        value = run_git(args).strip()
    except subprocess.CalledProcessError:
        return fallback
    return value or fallback


def parse_day(value: str) -> date:
    return date.fromisoformat(value)


def each_day(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def stable_int(key: str, salt: str, modulo: int) -> int:
    digest = hashlib.sha256(f"{salt}:{key}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % modulo


def stable_float(day: date, salt: str) -> float:
    return stable_int(day.isoformat(), salt, 10_000) / 10_000


def choose_count(day: date, salt: str, low: int, high: int) -> int:
    return low + stable_int(day.isoformat(), salt, high - low + 1)


def delayed_note_day(day: date, key: str, low: int = 3, high: int = 9) -> date:
    return day + timedelta(days=choose_count(day, f"{key}:note-delay", low, high))


def is_weekend(day: date) -> bool:
    return day.weekday() >= 5


def is_holiday_gap(day: date) -> bool:
    return (day.month == 12 and day.day >= 20) or (day.month == 1 and day.day <= 5)


def thanksgiving_day(year: int) -> date:
    day = date(year, 11, 1)
    while day.weekday() != 3:
        day += timedelta(days=1)
    return day + timedelta(days=21)


def holiday_slowdown_reason(day: date) -> str | None:
    thanksgiving = thanksgiving_day(day.year)
    if thanksgiving - timedelta(days=3) <= day <= thanksgiving + timedelta(days=3):
        return f"{day.year} Thanksgiving slowdown"
    if day.month == 12 and day.day >= 23:
        return f"{day.year} Christmas/New Year slowdown"
    if day.month == 1 and day.day <= 2:
        return f"{day.year - 1} Christmas/New Year slowdown"
    return None


def apply_holiday_slowdown(day: date, count: int) -> int:
    reason = holiday_slowdown_reason(day)
    if not reason or count == 0:
        return count
    probability = 0.18 if "Thanksgiving" in reason else 0.10
    if stable_float(day, f"{reason}:active") >= probability:
        return 0
    return 1


def era_for(day: date) -> str:
    if day <= date(2014, 4, 30):
        return "college"
    if day <= date(2015, 12, 31):
        return "post_college_weekend"
    if day <= date(2019, 3, 31):
        return "intense_project"
    if day <= date(2024, 6, 30):
        return "management_taper"
    if day <= date(2025, 5, 31):
        return "quiet_gap"
    return "assisted_coding"


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_existing_contributions(path: Path) -> dict[str, int]:
    payload = load_json(path)
    active_days = payload.get("active_days", {})
    return {str(day): int(count) for day, count in active_days.items()}


def load_work_history(path: Path) -> list[dict]:
    entries = []
    for entry in load_json(path)["entries"]:
        item = dict(entry)
        item["_start"] = parse_day(item["start"])
        item["_end"] = parse_day(item["end"]) if item.get("end") else None
        entries.append(item)
    return entries


def active_roles(entries: list[dict], day: date) -> tuple[dict[str, str], ...]:
    matches = []
    for entry in entries:
        end = entry["_end"]
        if entry["_start"] <= day and (end is None or day <= end):
            matches.append(entry)
    matches.sort(
        key=lambda item: (
            0 if item.get("type") == "experience" else 1,
            item["_start"],
            item.get("slug", ""),
        )
    )
    return tuple(
        {
            "location": item.get("location", ""),
            "organization": item.get("organization", ""),
            "slug": item.get("slug", ""),
            "title": item.get("title", ""),
            "type": item.get("type", ""),
        }
        for item in matches
    )


def load_travel(path: Path) -> dict:
    payload = load_json(path)
    exact = {parse_day(value) for value in payload["exact_travel_dates"]}
    no_generation = [
        (parse_day(item["start"]), parse_day(item["end"]), item["label"])
        for item in payload["no_generation_ranges"]
    ]
    vacation = [
        (parse_day(item["start"]), parse_day(item["end"]), item["label"])
        for item in payload.get("vacation_ranges", [])
    ]
    locations = [
        {
            **item,
            "_start": parse_day(item["start"]),
            "_end": parse_day(item["end"]) if item.get("end") else None,
        }
        for item in payload["location_ranges"]
    ]
    return {
        "default_location": payload["default_location"],
        "exact": exact,
        "no_generation": no_generation,
        "vacation": vacation,
        "locations": locations,
    }


def travel_skip_reason(travel: dict, day: date) -> str | None:
    if day in travel["exact"]:
        return "exact_travel_date"
    for start, end, label in travel["no_generation"]:
        if start <= day <= end:
            return label
    return None


def vacation_slowdown_reason(travel: dict, day: date) -> str | None:
    for start, end, label in travel.get("vacation", []):
        if start <= day <= end:
            return label
    return None


def apply_vacation_slowdown(day: date, count: int, label: str | None) -> int:
    if not label or count == 0:
        return count
    probability = 0.10 if any(word in label.lower() for word in ("honeymoon", "vacation")) else 0.14
    if stable_float(day, f"{label}:vacation-active") >= probability:
        return 0
    return 1


def location_for(travel: dict, day: date) -> tuple[str, str]:
    for item in travel["locations"]:
        end = item["_end"] or date.max
        if item["_start"] <= day <= end:
            return item["location"], item["timezone"]
    default = travel["default_location"]
    return default["name"], default["timezone"]


def note_category(day: date, era: str, sequence: int) -> str:
    categories = {
        "college": ["coursework", "lab", "project", "debugging"],
        "post_college_weekend": ["portfolio", "mobile-notes", "maintenance"],
        "intense_project": ["feature-work", "integration", "prototype", "release-notes"],
        "management_taper": ["maintenance", "tooling", "reading-notes"],
        "quiet_gap": ["maintenance", "archive"],
        "assisted_coding": ["agent-workflow", "automation", "prompt-systems", "tooling", "code-assist"],
    }[era]
    return categories[stable_int(f"{day.isoformat()}:{sequence}", "category", len(categories))]


def raw_generated_count(day: date, era: str) -> int:
    if day == START:
        return 1

    weekend = is_weekend(day)
    count = 0
    if era == "college":
        if is_holiday_gap(day):
            probability = 0.12 if not weekend else 0.05
            return 1 if stable_float(day, "college-holiday") < probability else 0
        if day.month in {6, 7, 8}:
            probability = 0.50 if not weekend else 0.22
            if stable_float(day, "college-summer") >= probability:
                return 0
            count = choose_count(day, "college-summer-count", 1, 3)
            return apply_holiday_slowdown(day, count)
        probability = 0.84 if not weekend else 0.34
        if stable_float(day, "college-term") >= probability:
            return 0
        count = choose_count(day, "college-term-count", 1, 4 if not weekend else 3)
        return apply_holiday_slowdown(day, count)

    if era == "post_college_weekend":
        if not weekend or stable_float(day, "post-college-weekend") >= 0.23:
            return 0
        count = choose_count(day, "post-college-count", 1, 3)
        return apply_holiday_slowdown(day, count)

    if era == "intense_project":
        if weekend:
            if stable_float(day, "intense-weekend-occasional") >= 0.18:
                return 0
            count = choose_count(day, "intense-weekend-occasional-count", 1, 3)
            return apply_holiday_slowdown(day, count)
        if stable_float(day, "intense-weekday-primary") >= 0.70:
            return 0
        count = choose_count(day, "intense-weekday-primary-count", 2, 4)
        return apply_holiday_slowdown(day, count)

    if era == "management_taper":
        probabilities = {
            2019: (0.07, 0.24, 1, 3),
            2020: (0.18, 0.13, 1, 2),
            2021: (0.18, 0.02, 1, 2),
            2022: (0.12, 0.01, 1, 1),
            2023: (0.07, 0.00, 1, 1),
            2024: (0.05, 0.00, 1, 1),
        }
        weekend_probability, weekday_probability, low, high = probabilities[day.year]
        probability = weekend_probability if weekend else weekday_probability
        if probability <= 0 or stable_float(day, "management-taper") >= probability:
            return 0
        count = choose_count(day, "management-count", low, high)
        return apply_holiday_slowdown(day, count)

    if era == "quiet_gap":
        if not weekend or stable_float(day, "quiet-gap") >= 0.05:
            return 0
        return apply_holiday_slowdown(day, 1)

    if era == "assisted_coding":
        month_index = (day.year - ASSISTED_ERA_START.year) * 12 + (day.month - ASSISTED_ERA_START.month)
        ramp = max(0, min(month_index, 5))
        quiet_day = day.weekday() in {0, 5, 6}
        weekday_probabilities = [0.55, 0.65, 0.72, 0.80, 0.86, 0.90]
        quiet_probabilities = [0.18, 0.24, 0.30, 0.34, 0.38, 0.40]
        count_ranges = [(2, 5), (3, 6), (3, 7), (4, 8), (5, 9), (5, 9)]
        probability = quiet_probabilities[ramp] if quiet_day else weekday_probabilities[ramp]
        if stable_float(day, "assisted-gradient-active") >= probability:
            return 0
        low, high = count_ranges[ramp]
        if quiet_day:
            low = max(2, low - 1)
            high = max(low, high - 2)
        count = choose_count(day, "assisted-gradient-count", low, high)
        return apply_holiday_slowdown(day, count)

    raise ValueError(f"unknown era: {era}")


def generated_count(day: date, existing_count: int) -> int:
    era = era_for(day)
    target = raw_generated_count(day, era)
    if target == 0:
        return 0

    if day >= REAL_HISTORY_START and era != "assisted_coding" and existing_count > 0:
        return 0

    if era == "assisted_coding" and existing_count > 0:
        return max(0, target - existing_count)

    return target


def quota_day_count(day: date) -> int:
    if day.year == 2022:
        burst = stable_float(day, "annual-quota-2022-burst")
        if burst < 0.12:
            return 4
        if burst < 0.34:
            return 3
        if burst < 0.68:
            return 2
        return 1

    count = 1
    if stable_float(day, "annual-quota-second") < 0.52:
        count += 1
    if stable_float(day, "annual-quota-third") < 0.16:
        count += 1
    return count


def annual_quota_counts(
    start: date,
    end: date,
    existing: dict[str, int],
    travel: dict,
) -> dict[date, int]:
    counts: dict[date, int] = {}
    existing_by_year: Counter = Counter()
    for day_value, count in existing.items():
        day = parse_day(day_value)
        if start <= day <= end and day.year in ANNUAL_TOTAL_TARGETS:
            existing_by_year[day.year] += int(count)

    for year, total_target in ANNUAL_TOTAL_TARGETS.items():
        generated_target = max(0, total_target - existing_by_year[year])
        if generated_target == 0:
            continue

        year_start = max(start, date(year, 1, 1))
        year_end = min(end, date(year, 12, 31))
        candidates = [
            day
            for day in each_day(year_start, year_end)
            if travel_skip_reason(travel, day) is None
            and vacation_slowdown_reason(travel, day) is None
            and holiday_slowdown_reason(day) is None
            and existing.get(day.isoformat(), 0) == 0
        ]
        candidates.sort(key=lambda item: stable_int(item.isoformat(), "annual-quota-order", 1_000_000))

        remaining = generated_target
        for day in candidates:
            if remaining <= 0:
                break
            count = min(quota_day_count(day), remaining)
            counts[day] = count
            remaining -= count

        if remaining > 0:
            raise SystemExit(f"not enough eligible days to hit {year} target")

    return counts


def claude_monthly_vacation_days(
    start: date,
    end: date,
    existing: dict[str, int],
    travel: dict,
) -> dict[date, str]:
    vacation_days: dict[date, str] = {}
    month = date(max(start, ASSISTED_ERA_START).year, max(start, ASSISTED_ERA_START).month, 1)
    final_month = date(end.year, end.month, 1)

    while month <= final_month:
        month_end = date(month.year + (month.month // 12), (month.month % 12) + 1, 1) - timedelta(days=1)
        window_start = max(start, ASSISTED_ERA_START, month)
        window_end = min(end, month_end)
        month_key = f"{month.year:04d}-{month.month:02d}"
        target = 3 + stable_int(month_key, "claude-monthly-vacation-count", 2)
        candidates = []

        for day in each_day(window_start, window_end):
            if day == end:
                continue
            if travel_skip_reason(travel, day) is not None:
                continue
            if vacation_slowdown_reason(travel, day) is not None:
                continue
            if existing.get(day.isoformat(), 0) > 0:
                continue
            if raw_generated_count(day, "assisted_coding") == 0:
                continue
            candidates.append(day)

        candidates.sort(key=lambda item: stable_int(item.isoformat(), "claude-monthly-vacation-day", 1_000_000))
        for day in candidates[:target]:
            vacation_days[day] = f"monthly vacation day {month_key}"

        if month.month == 12:
            month = date(month.year + 1, 1, 1)
        else:
            month = date(month.year, month.month + 1, 1)

    return vacation_days


def plan_commits(
    start: date,
    end: date,
    existing: dict[str, int],
    work_history: list[dict],
    travel: dict,
) -> tuple[list[PlannedCommit], Counter, Counter]:
    planned: list[PlannedCommit] = []
    skip_reasons: Counter = Counter()
    real_active_skips: Counter = Counter()
    annual_counts = annual_quota_counts(start, end, existing, travel)
    monthly_vacation_days = claude_monthly_vacation_days(start, end, existing, travel)

    for day in each_day(start, end):
        era = era_for(day)
        skip_reason = travel_skip_reason(travel, day)
        if skip_reason:
            skip_reasons[skip_reason] += 1
            continue
        if day in monthly_vacation_days:
            skip_reasons[monthly_vacation_days[day]] += 1
            continue

        existing_count = existing.get(day.isoformat(), 0)
        vacation_reason = vacation_slowdown_reason(travel, day)
        if day.year in ANNUAL_TOTAL_TARGETS:
            raw_count = annual_counts.get(day, 0)
            count = raw_count
        else:
            raw_count = raw_generated_count(day, era)
            count = generated_count(day, existing_count)
        slowed_count = apply_vacation_slowdown(day, count, vacation_reason)
        if vacation_reason and count > 0 and slowed_count == 0:
            skip_reasons[f"{vacation_reason} vacation slowdown"] += 1
        count = slowed_count
        if day == end and era == "assisted_coding" and count == 0 and not vacation_reason:
            count = 1
        if day >= REAL_HISTORY_START and era != "assisted_coding" and existing_count > 0 and raw_count > 0:
            real_active_skips[era] += 1
        if count == 0:
            continue

        location, timezone = location_for(travel, day)
        roles = active_roles(work_history, day)
        for sequence in range(1, count + 1):
            planned.append(
                PlannedCommit(
                    day=day,
                    sequence=sequence,
                    total_for_day=count,
                    era=era,
                    category=note_category(day, era, sequence),
                    location=location,
                    timezone=timezone,
                    roles=roles,
                    existing_contributions=existing_count,
                )
            )

    return planned, skip_reasons, real_active_skips


def day_path(day: date) -> str:
    return f"{day.year:04d}/{day.month:02d}/{day.day:02d}.jsonl"


def commit_timestamp(item: PlannedCommit) -> datetime:
    index = min(item.sequence - 1, len(COMMIT_TIMES) - 1)
    return datetime.combine(item.day, COMMIT_TIMES[index], tzinfo=ZoneInfo(item.timezone))


def record_line(item: PlannedCommit) -> bytes:
    stamp = commit_timestamp(item)
    payload = {
        "category": item.category,
        "committed_at": stamp.isoformat(timespec="seconds"),
        "date": item.day.isoformat(),
        "era": item.era,
        "existing_contributions": item.existing_contributions,
        "location": item.location,
        "roles": list(item.roles),
        "sequence": item.sequence,
        "timezone": item.timezone,
        "total_generated_for_day": item.total_for_day,
    }
    return (json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def data_block(payload: bytes) -> bytes:
    return b"data " + str(len(payload)).encode("ascii") + b"\n" + payload + b"\n"


def ident_line(kind: str, name: str, email: str, stamp: datetime) -> bytes:
    epoch = int(stamp.timestamp())
    return f"{kind} {name} <{email}> {epoch} {stamp.strftime('%z')}\n".encode("utf-8")


def file_command(path: str, payload: bytes) -> bytes:
    return b"M 100644 inline " + path.encode("utf-8") + b"\n" + data_block(payload)


def json_bytes(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=False) + "\n").encode("utf-8")


def work_history_snapshot(entries: list[dict], as_of: date) -> bytes:
    snapshot_entries = []
    for entry in entries:
        end = entry.get("_end")
        if end is None:
            release_day = delayed_note_day(entry["_start"], f"work:{entry.get('slug', '')}:start")
        else:
            release_day = delayed_note_day(end, f"work:{entry.get('slug', '')}:end")
        if release_day > as_of:
            continue
        item = {
            "slug": entry.get("slug", ""),
            "type": entry.get("type", ""),
            "organization": entry.get("organization", ""),
            "title": entry.get("title", ""),
            "location": entry.get("location", ""),
            "start": entry["start"],
        }
        if end is not None:
            item["end"] = entry["end"]
        snapshot_entries.append(item)

    return json_bytes(
        {
            "source": "Personal work-history notes",
            "entries": snapshot_entries,
        }
    )


def completed_ranges(ranges: list[tuple[date, date, str]], as_of: date) -> list[dict[str, str]]:
    return [
        {
            "label": label,
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
        for start, end, label in ranges
        if delayed_note_day(end, f"range:{label}") <= as_of
    ]


def travel_history_snapshot(travel: dict, as_of: date) -> bytes:
    locations = []
    for entry in travel["locations"]:
        end = entry["_end"]
        if end is None:
            release_day = delayed_note_day(entry["_start"], f"location:{entry['location']}:start")
        else:
            release_day = delayed_note_day(end, f"location:{entry['location']}:{entry['start']}:end")
        if release_day > as_of:
            continue
        item = {
            "location": entry["location"],
            "timezone": entry["timezone"],
            "start": entry["start"],
        }
        if end is not None:
            item["end"] = entry["end"]
        locations.append(item)

    return json_bytes(
        {
            "source": "Personal travel-history notes",
            "default_location": travel["default_location"],
            "exact_travel_dates": [
                day.isoformat()
                for day in sorted(travel["exact"])
                if delayed_note_day(day, f"exact-travel:{day.isoformat()}", 2, 6) <= as_of
            ],
            "no_generation_ranges": completed_ranges(travel["no_generation"], as_of),
            "vacation_ranges": completed_ranges(travel["vacation"], as_of),
            "location_ranges": locations,
        }
    )


def metadata_snapshots(work_history: list[dict], travel: dict, as_of: date) -> dict[str, bytes]:
    return {
        "work_history.json": work_history_snapshot(work_history, as_of),
        "travel_history.json": travel_history_snapshot(travel, as_of),
    }


def ensure_importable() -> None:
    try:
        run_git(["rev-parse", "--verify", "HEAD"])
    except subprocess.CalledProcessError:
        return
    raise SystemExit("refusing to import over an existing commit history")


def import_history(
    planned: list[PlannedCommit],
    work_history: list[dict] | None = None,
    travel: dict | None = None,
) -> None:
    if not planned:
        raise SystemExit("nothing to import")
    ensure_importable()
    if work_history is None:
        work_history = load_work_history(Path("work_history.json"))
    if travel is None:
        travel = load_travel(Path("travel_history.json"))

    author_name = git_value(["config", "user.name"], DEFAULT_AUTHOR_NAME)
    author_email = git_value(["config", "user.email"], DEFAULT_AUTHOR_EMAIL)
    day_payloads: dict[str, bytes] = defaultdict(bytes)
    last_metadata_payloads: dict[str, bytes] = {}

    missing = [path for path in SEED_FILES if not Path(path).exists()]
    if missing:
        raise SystemExit(f"missing seed files: {', '.join(missing)}")

    process = subprocess.Popen(["git", "fast-import", "--quiet"], stdin=subprocess.PIPE)
    if process.stdin is None:
        raise SystemExit("failed to open git fast-import stdin")
    out = process.stdin

    for index, item in enumerate(planned):
        stamp = commit_timestamp(item)
        current_metadata_payloads = metadata_snapshots(work_history, travel, item.day)
        metadata_changes = [
            metadata_path
            for metadata_path, payload in current_metadata_payloads.items()
            if last_metadata_payloads.get(metadata_path) != payload
        ]
        if index == 0:
            message = f"Initialize contribution notes {item.day.isoformat()}"
        elif set(metadata_changes) == {"work_history.json", "travel_history.json"}:
            message = f"Update work and travel history {item.day.isoformat()}"
        elif metadata_changes == ["work_history.json"]:
            message = f"Update work history {item.day.isoformat()}"
        elif metadata_changes == ["travel_history.json"]:
            message = f"Update travel history {item.day.isoformat()}"
        else:
            message = f"Record activity {item.day.isoformat()}"
            if item.sequence > 1:
                message = f"{message} #{item.sequence}"

        out.write(f"commit {REF}\n".encode("ascii"))
        out.write(ident_line("author", author_name, author_email, stamp))
        out.write(ident_line("committer", author_name, author_email, stamp))
        out.write(data_block(message.encode("utf-8")))

        if index == 0:
            for seed_path in INITIAL_SEED_FILES:
                out.write(file_command(seed_path, Path(seed_path).read_bytes()))
        for metadata_path, payload in current_metadata_payloads.items():
            if last_metadata_payloads.get(metadata_path) != payload:
                out.write(file_command(metadata_path, payload))
                last_metadata_payloads[metadata_path] = payload
        if index == len(planned) - 1:
            for seed_path in FINAL_CURRENT_FILES:
                out.write(file_command(seed_path, Path(seed_path).read_bytes()))

        path = day_path(item.day)
        day_payloads[path] += record_line(item)
        out.write(file_command(path, day_payloads[path]))
        out.write(b"\n")

    out.close()
    return_code = process.wait()
    if return_code != 0:
        raise SystemExit(f"git fast-import failed with exit {return_code}")


def print_summary(planned: list[PlannedCommit], skip_reasons: Counter, real_active_skips: Counter) -> None:
    day_keys = {item.day for item in planned}
    by_year = Counter()
    by_era = Counter()
    active_days_by_era = defaultdict(set)
    for item in planned:
        by_year[item.day.year] += 1
        by_era[item.era] += 1
        active_days_by_era[item.era].add(item.day)

    print(f"generated commits: {len(planned)}")
    print(f"generated active days: {len(day_keys)}")
    if planned:
        print(f"first: {planned[0].day.isoformat()} #{planned[0].sequence}")
        print(f"last:  {planned[-1].day.isoformat()} #{planned[-1].sequence}")
    print("\nby year:")
    for year in sorted(by_year):
        active_days = len({item.day for item in planned if item.day.year == year})
        print(f"  {year}: {by_year[year]} commits across {active_days} days")
    print("\nby era:")
    for era in sorted(by_era):
        print(f"  {era}: {by_era[era]} commits across {len(active_days_by_era[era])} days")
    if real_active_skips:
        print("\nreal active day skips:")
        for era, count in sorted(real_active_skips.items()):
            print(f"  {era}: {count}")
    if skip_reasons:
        print("\ntravel/uncertain date skips:")
        for reason, count in sorted(skip_reasons.items()):
            print(f"  {reason}: {count}")


def main() -> int:
    args = parse_args()
    start = parse_day(args.start)
    end = parse_day(args.end)
    if start < START or end > END:
        raise SystemExit(f"date range must stay within {START}..{END}")
    if end < start:
        raise SystemExit("--end must be on or after --start")

    existing = load_existing_contributions(Path(args.existing_contributions))
    work_history = load_work_history(Path(args.work_history))
    travel = load_travel(Path(args.travel_history))
    planned, skip_reasons, real_active_skips = plan_commits(start, end, existing, work_history, travel)

    print_summary(planned, skip_reasons, real_active_skips)

    if args.import_history:
        import_history(planned, work_history, travel)
        print("\nimport complete")
    elif not args.dry_run:
        print("\nno import requested; pass --import-history to write Git history")

    return 0


if __name__ == "__main__":
    sys.exit(main())

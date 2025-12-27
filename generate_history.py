#!/usr/bin/env python3
"""Generate a deterministic contribution-history Git branch."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


START = date(2010, 1, 3)
END = date.today()
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
    "contribution_rules.json",
    "existing_contributions.json",
    "generate_history.py",
    "scripts/daily_contribution.py",
    "scripts/fetch_existing_contributions.py",
]
FINAL_CURRENT_FILES = [*METADATA_FILES, *FINAL_TOOLING_FILES]
SEED_FILES = [*INITIAL_SEED_FILES, *FINAL_CURRENT_FILES]
OPTIONAL_TOOLING_FILES = ["LICENSE", "CONTRIBUTING.md", "SECURITY.md"]


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
    parser.add_argument("--start")
    parser.add_argument("--end")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--import-history", action="store_true")
    parser.add_argument("--rules", default="contribution_rules.json")
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


def parse_config_day(value: str) -> date:
    if value == "today":
        return date.today()
    return parse_day(value)


def parse_config_time(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


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


def apply_holiday_slowdown(day: date, count: int, rules: dict) -> int:
    reason = holiday_slowdown_reason(day)
    if not reason or count == 0:
        return count
    holiday_rules = rules.get("holiday_slowdown", {})
    probability = (
        holiday_rules.get("thanksgiving_active_probability", 0.18)
        if "Thanksgiving" in reason
        else holiday_rules.get("christmas_new_year_active_probability", 0.10)
    )
    if stable_float(day, f"{reason}:active") >= probability:
        return 0
    return 1


def era_for(day: date, rules: dict) -> str:
    for item in rules["_eras"]:
        end = item["_end"] or date.max
        if item["_start"] <= day <= end:
            return item["name"]
    raise SystemExit(f"no contribution era configured for {day.isoformat()}")


def load_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"cannot load {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    return payload


def require_integer(value: object, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SystemExit(f"{label} must be an integer >= {minimum}")
    return value


def validate_count_range(value: object, label: str) -> None:
    if not isinstance(value, list) or len(value) != 2:
        raise SystemExit(f"{label} must be a [minimum, maximum] pair")
    low = require_integer(value[0], label)
    high = require_integer(value[1], label)
    if high < low:
        raise SystemExit(f"{label} maximum must be >= minimum")


def validate_rule_values(value: object, label: str) -> None:
    """Check shared scalar/range constraints before density evaluation."""
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        field = f"{label}.{key}"
        if key.endswith("probability") or key == "below":
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) or not 0 <= item <= 1:
                raise SystemExit(f"{field} must be a probability between 0 and 1")
        elif key.endswith("probabilities"):
            if not isinstance(item, list) or not item:
                raise SystemExit(f"{field} must be a nonempty probability list")
            for probability in item:
                validate_rule_values({"probability": probability}, field)
        elif key in {"count", "weekday_count", "weekend_count"} and isinstance(item, list):
            validate_count_range(item, field)
        elif key == "count_ranges":
            if not isinstance(item, list) or not item:
                raise SystemExit(f"{field} must be a nonempty list of count pairs")
            for count_range in item:
                validate_count_range(count_range, field)
        elif key in {"base_count", "count", "burst_days", "minimum_generated_total", "minimum_graph_total"}:
            require_integer(item, field)
        if isinstance(item, dict):
            validate_rule_values(item, field)
        elif isinstance(item, list):
            for member in item:
                if isinstance(member, dict):
                    validate_rule_values(member, field)


def validate_timezone(value: str, label: str) -> None:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
        raise SystemExit(f"{label} contains an unavailable IANA timezone: {value!r}") from exc


def load_contribution_rules(path: Path) -> dict:
    rules = load_json(path)
    validate_rule_values(rules, str(path))
    date_range = rules.get("date_range", {})
    rules["_start"] = parse_config_day(date_range.get("start", START.isoformat()))
    rules["_end"] = parse_config_day(date_range.get("end", END.isoformat()))
    if rules["_end"] < rules["_start"]:
        raise SystemExit("contribution_rules.json date_range.end must be on or after date_range.start")

    existing_activity = rules.get("existing_activity", {})
    rules["_existing_skip_after"] = parse_config_day(
        existing_activity.get("skip_generated_after", rules["_start"].isoformat())
    )
    rules["_reduce_generated_in_eras"] = set(existing_activity.get("reduce_generated_in_eras", []))
    try:
        rules["_commit_times"] = [parse_config_time(value) for value in rules["commit_times"]]
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise SystemExit(f"{path} commit_times must contain valid HH:MM times") from exc
    if not rules["_commit_times"]:
        raise SystemExit("contribution_rules.json commit_times must not be empty")
    if rules["_commit_times"] != sorted(rules["_commit_times"]):
        raise SystemExit(f"{path} commit_times must be in chronological order")

    eras = []
    if not isinstance(rules.get("eras"), list) or not rules["eras"]:
        raise SystemExit(f"{path} eras must be a nonempty list")
    names = set()
    for entry in rules["eras"]:
        item = dict(entry)
        name = item.get("name")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise SystemExit(f"{path} era names must be nonempty and unique")
        names.add(name)
        item["_start"] = parse_day(item["start"])
        item["_end"] = parse_day(item["end"]) if item.get("end") else None
        if item["_end"] is not None and item["_end"] < item["_start"]:
            raise SystemExit(f"{path} era {name} ends before it starts")
        item.setdefault("note_categories", [item["name"]])
        categories = item["note_categories"]
        if not isinstance(categories, list) or not categories or any(not isinstance(category, str) or not category.strip() for category in categories):
            raise SystemExit(f"{path} era {name} needs nonempty note_categories")
        density = item.get("density", {})
        if density.get("type") not in {"college_coursework", "weekend_only", "weekday_project", "year_taper", "assisted_gradient"}:
            raise SystemExit(f"{path} era {name} has an unsupported density type")
        if density["type"] == "assisted_gradient":
            arrays = [density.get(key, []) for key in ("count_ranges", "weekday_probabilities", "quiet_probabilities")]
            if not arrays[0] or len({len(values) for values in arrays}) != 1:
                raise SystemExit(f"{path} era {name} gradient arrays must have the same nonzero length")
        eras.append(item)
    eras.sort(key=lambda item: item["_start"])
    previous = None
    for item in eras:
        if previous is not None:
            previous_end = previous["_end"] or date.max
            if item["_start"] <= previous_end:
                raise SystemExit(
                    "contribution_rules.json eras overlap: "
                    f"{previous['name']} and {item['name']}"
                )
        previous = item
    covered_until = rules["_start"]
    for item in eras:
        era_end = item["_end"] or date.max
        if era_end < covered_until:
            continue
        if item["_start"] > covered_until:
            raise SystemExit(f"{path} eras leave a gap at {covered_until}")
        if era_end >= rules["_end"]:
            break
        covered_until = era_end + timedelta(days=1)
    else:
        raise SystemExit(f"{path} eras do not cover date_range.end")
    rules["_eras"] = eras
    rules["_eras_by_name"] = {item["name"]: item for item in eras}
    rules["_annual_total_targets"] = {
        int(year): require_integer(total, f"{path} annual_total_targets.{year}")
        for year, total in rules.get("annual_total_targets", {}).items()
    }
    for year in rules["_annual_total_targets"]:
        if not 1 <= year <= 9999:
            raise SystemExit(f"{path} annual target years must be between 1 and 9999")
    unknown_eras = rules["_reduce_generated_in_eras"] - names
    if unknown_eras:
        raise SystemExit(f"{path} existing_activity references unknown eras: {sorted(unknown_eras)}")
    rest = rules.get("monthly_rest_days", {})
    if rest.get("enabled", True) and rest.get("era") and rest["era"] not in names:
        raise SystemExit(f"{path} monthly_rest_days references an unknown era")
    minimum = require_integer(rest.get("min_days_per_month", 3), "monthly_rest_days.min_days_per_month")
    maximum = require_integer(rest.get("max_days_per_month", minimum), "monthly_rest_days.max_days_per_month")
    if maximum < minimum or maximum > 31:
        raise SystemExit("monthly_rest_days needs 0 <= minimum <= maximum <= 31")
    burst = rules.get("annual_bursts", {})
    target = burst.get("target_count", {})
    validate_count_range([target.get("min", 10), target.get("max", 12)], "annual_bursts.target_count")
    gaps = burst.get("minimum_gap_days", [28, 21, 14, 7, 0])
    if not isinstance(gaps, list) or not gaps:
        raise SystemExit("annual_bursts.minimum_gap_days must be a nonempty list")
    for gap in gaps:
        require_integer(gap, "annual_bursts.minimum_gap_days")
    if "annual_bursts" in rules:
        rules["annual_bursts"]["thresholds"] = sorted(
            rules["annual_bursts"].get("thresholds", []),
            key=lambda item: int(item["minimum_graph_total"]),
            reverse=True,
        )
    rules["_burst_weekday_penalties"] = {
        int(weekday): int(penalty)
        for weekday, penalty in rules.get("annual_bursts", {}).get("weekday_penalties", {}).items()
    }
    return rules


def load_existing_contributions(path: Path) -> dict[str, int]:
    payload = load_json(path)
    active_days = payload.get("active_days", {})
    if not isinstance(active_days, dict):
        raise SystemExit(f"{path} active_days must be an object")
    for day, count in active_days.items():
        parse_day(day)
        require_integer(count, f"{path} active_days.{day}")
    return dict(active_days)


def load_work_history(path: Path) -> list[dict]:
    entries = []
    for entry in load_json(path)["entries"]:
        item = dict(entry)
        item["_start"] = parse_day(item["start"])
        item["_end"] = parse_day(item["end"]) if item.get("end") else None
        if item["_end"] is not None and item["_end"] < item["_start"]:
            raise SystemExit(f"{path} work-history entry ends before it starts")
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
    validate_timezone(payload["default_location"]["timezone"], f"{path} default_location")
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
    for start, end, label in [*no_generation, *vacation]:
        if end < start:
            raise SystemExit(f"{path} range {label!r} ends before it starts")
    locations.sort(key=lambda item: item["_start"])
    previous_end = None
    for item in locations:
        validate_timezone(item["timezone"], f"{path} location {item['location']!r}")
        end = item["_end"] or date.max
        if end < item["_start"]:
            raise SystemExit(f"{path} location range ends before it starts")
        if previous_end is not None and item["_start"] <= previous_end:
            raise SystemExit(f"{path} location_ranges must not overlap")
        previous_end = end
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


def apply_vacation_slowdown(day: date, count: int, label: str | None, rules: dict) -> int:
    if not label or count == 0:
        return count
    vacation_rules = rules.get("vacation_slowdown", {})
    keywords = vacation_rules.get("deep_vacation_keywords", ["honeymoon", "vacation"])
    probability = (
        vacation_rules.get("deep_vacation_active_probability", 0.10)
        if any(word in label.lower() for word in keywords)
        else vacation_rules.get("default_active_probability", 0.14)
    )
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


def note_category(day: date, era: str, sequence: int, rules: dict) -> str:
    categories = rules["_eras_by_name"][era]["note_categories"]
    return categories[stable_int(f"{day.isoformat()}:{sequence}", "category", len(categories))]


def configured_range_count(day: date, count_range: list[int], salt: str) -> int:
    return choose_count(day, salt, int(count_range[0]), int(count_range[1]))


def configured_activity_count(day: date, config: dict, weekend: bool, rules: dict) -> int:
    if "active_probability" in config:
        probability = float(config["active_probability"])
    else:
        probability = float(config["weekend_probability"] if weekend else config["weekday_probability"])
    if probability <= 0 or stable_float(day, config["active_salt"]) >= probability:
        return 0

    if "count" in config:
        count_range = config["count"]
    else:
        count_range = config["weekend_count"] if weekend else config["weekday_count"]
    count = configured_range_count(day, count_range, config["count_salt"])
    return apply_holiday_slowdown(day, count, rules)


def raw_generated_count(day: date, era: str, rules: dict) -> int:
    if day == rules["_start"]:
        return 1

    era_config = rules["_eras_by_name"][era]
    density = era_config["density"]
    density_type = density["type"]
    weekend = is_weekend(day)

    if density_type == "college_coursework":
        if is_holiday_gap(day):
            holiday_gap = density["holiday_gap"]
            probability = (
                holiday_gap["weekend_probability"] if weekend else holiday_gap["weekday_probability"]
            )
            return 1 if stable_float(day, holiday_gap["active_salt"]) < probability else 0
        if day.month in set(density["summer_months"]):
            return configured_activity_count(day, density["summer"], weekend, rules)
        return configured_activity_count(day, density["term"], weekend, rules)

    if density_type == "weekend_only":
        if not weekend:
            return 0
        return configured_activity_count(day, density, weekend, rules)

    if density_type == "weekday_project":
        return configured_activity_count(day, density["weekend" if weekend else "weekday"], weekend, rules)

    if density_type == "year_taper":
        year_config = density["years"].get(str(day.year))
        if not year_config:
            return 0
        probability = (
            year_config["weekend_probability"] if weekend else year_config["weekday_probability"]
        )
        if probability <= 0 or stable_float(day, density["active_salt"]) >= probability:
            return 0
        count = configured_range_count(day, year_config["count"], density["count_salt"])
        return apply_holiday_slowdown(day, count, rules)

    if density_type == "assisted_gradient":
        era_start = era_config["_start"]
        month_index = (day.year - era_start.year) * 12 + (day.month - era_start.month)
        count_ranges = density["count_ranges"]
        ramp = max(0, min(month_index, len(count_ranges) - 1))
        quiet_day = day.weekday() in set(density["quiet_weekdays"])
        weekday_probabilities = density["weekday_probabilities"]
        quiet_probabilities = density["quiet_probabilities"]
        probability = quiet_probabilities[ramp] if quiet_day else weekday_probabilities[ramp]
        if stable_float(day, density["active_salt"]) >= probability:
            return 0
        low, high = count_ranges[ramp]
        if quiet_day:
            adjustment = density["quiet_count_adjustment"]
            low = max(int(adjustment["minimum_low"]), low + int(adjustment["low_delta"]))
            high = max(low, high + int(adjustment["high_delta"]))
        count = choose_count(day, density["count_salt"], low, high)
        return apply_holiday_slowdown(day, count, rules)

    raise ValueError(f"unknown density type for {era}: {density_type}")


def generated_count(day: date, existing_count: int, rules: dict) -> int:
    era = era_for(day, rules)
    target = raw_generated_count(day, era, rules)
    if target == 0:
        return 0

    reduce_eras = rules["_reduce_generated_in_eras"]
    if day >= rules["_existing_skip_after"] and era not in reduce_eras and existing_count > 0:
        return 0

    if era in reduce_eras and existing_count > 0:
        return max(0, target - existing_count)

    return target


def quota_day_count(day: date, rules: dict) -> int:
    annual_quota = rules.get("annual_quota", {})
    override = annual_quota.get("year_overrides", {}).get(str(day.year))
    if override:
        value = stable_float(day, override["salt"])
        for band in override["bands"]:
            if "below" not in band or value < float(band["below"]):
                return int(band["count"])

    default = annual_quota.get("default", {})
    count = int(default.get("base_count", 1))
    if stable_float(day, default.get("second_salt", "annual-quota-second")) < float(
        default.get("second_probability", 0.52)
    ):
        count += 1
    if stable_float(day, default.get("third_salt", "annual-quota-third")) < float(
        default.get("third_probability", 0.16)
    ):
        count += 1
    return count


def annual_quota_counts(
    start: date,
    end: date,
    existing: dict[str, int],
    travel: dict,
    rules: dict,
    excluded_days: set[date] | None = None,
) -> dict[date, int]:
    """Allocate whole configured years before selecting the requested dates."""
    counts: dict[date, int] = {}
    annual_targets = rules["_annual_total_targets"]
    if excluded_days is None:
        excluded_days = set(claude_monthly_vacation_days(
            max(rules["_start"], date(start.year, 1, 1)),
            min(rules["_end"], date(end.year, 12, 31)),
            existing, travel, rules,
        ))
    existing_by_year: Counter = Counter()
    for day_value, count in existing.items():
        day = parse_day(day_value)
        # Targets describe the complete calendar-year graph, even when only
        # part of that year is eligible for generated history.
        if day.year in annual_targets:
            existing_by_year[day.year] += int(count)

    for year, total_target in annual_targets.items():
        year_start = max(rules["_start"], date(year, 1, 1))
        year_end = min(rules["_end"], date(year, 12, 31))
        if year_end < year_start or year_end < start or year_start > end:
            continue

        generated_target = max(0, total_target - existing_by_year[year])
        if generated_target == 0:
            continue

        candidates = [
            day
            for day in each_day(year_start, year_end)
            if travel_skip_reason(travel, day) is None
            and vacation_slowdown_reason(travel, day) is None
            and holiday_slowdown_reason(day) is None
            and existing.get(day.isoformat(), 0) == 0
            and day not in excluded_days
        ]
        candidates.sort(key=lambda item: stable_int(item.isoformat(), "annual-quota-order", 1_000_000))

        remaining = generated_target
        for day in candidates:
            if remaining <= 0:
                break
            count = min(quota_day_count(day, rules), remaining)
            if start <= day <= end and count > 0:
                counts[day] = count
            remaining -= count

        if remaining > 0:
            capacity = generated_target - remaining
            raise SystemExit(
                f"cannot meet {year} annual target {total_target}: "
                f"{existing_by_year[year]} existing contributions require "
                f"{generated_target} generated commits, but {len(candidates)} eligible days "
                f"allow only {capacity} after travel, vacation, holiday, and rest exclusions"
            )

    return counts


def annual_burst_count(year: int, generated_total: int, existing_total: int, rules: dict) -> int:
    burst_rules = rules.get("annual_bursts", {})
    if not burst_rules.get("enabled", True):
        return 0
    if generated_total < int(burst_rules.get("minimum_generated_total", 200)):
        return 0
    graph_total = generated_total + existing_total
    if graph_total < int(burst_rules.get("minimum_graph_total", 220)):
        return 0
    for threshold in burst_rules.get("thresholds", []):
        if graph_total >= int(threshold["minimum_graph_total"]):
            return int(threshold["burst_days"])
    return 0


def burst_candidate_sort_key(day: date, rules: dict) -> tuple[int, int]:
    burst_rules = rules.get("annual_bursts", {})
    weekday_penalty = rules["_burst_weekday_penalties"].get(day.weekday(), 0)
    candidate_salt = burst_rules.get("candidate_salt", "annual-burst-candidate")
    return weekday_penalty, stable_int(day.isoformat(), candidate_salt, 1_000_000)


def select_burst_days(candidates: list[date], target: int, rules: dict) -> list[date]:
    burst_rules = rules.get("annual_bursts", {})
    selected: list[date] = []
    candidates = sorted(candidates, key=lambda day: burst_candidate_sort_key(day, rules))
    for minimum_gap in burst_rules.get("minimum_gap_days", [28, 21, 14, 7, 0]):
        for day in candidates:
            if day in selected:
                continue
            if minimum_gap and any(abs((day - existing).days) < minimum_gap for existing in selected):
                continue
            selected.append(day)
            if len(selected) == target:
                return sorted(selected)
    return sorted(selected)


def redistribute_for_burst(
    counts: dict[date, int],
    burst_day: date,
    target_count: int,
    start: date,
    end: date,
    rules: dict,
) -> bool:
    needed = target_count - counts[burst_day]
    if needed <= 0:
        return True

    year = burst_day.year
    burst_rules = rules.get("annual_bursts", {})
    burst_era = era_for(burst_day, rules)
    donors = [
        day
        for day, count in counts.items()
        if day.year == year
        and era_for(day, rules) == burst_era
        and day != start
        and day != end
        and day != burst_day
        and count > 1
    ]
    donors.sort(
        key=lambda day: (
            abs((day - burst_day).days),
            stable_int(
                day.isoformat(),
                f"{burst_rules.get('donor_salt_prefix', 'annual-burst-donor')}:{burst_day.isoformat()}",
                1_000_000,
            ),
        )
    )

    for donor in donors:
        if needed == 0:
            break
        available = counts[donor] - 1
        moved = min(available, needed)
        counts[donor] -= moved
        counts[burst_day] += moved
        needed -= moved

    if needed == 0:
        return True

    fallback_donors = [
        day
        for day, count in counts.items()
        if day.year == year
        and era_for(day, rules) == burst_era
        and day != start
        and day != end
        and day != burst_day
        and count > 0
    ]
    fallback_donors.sort(
        key=lambda day: (
            abs((day - burst_day).days),
            stable_int(
                day.isoformat(),
                f"{burst_rules.get('fallback_salt_prefix', 'annual-burst-fallback')}:{burst_day.isoformat()}",
                1_000_000,
            ),
        )
    )
    for donor in fallback_donors:
        if needed == 0:
            break
        counts[donor] -= 1
        counts[burst_day] += 1
        needed -= 1

    return needed == 0


def apply_annual_bursts(
    counts: dict[date, int],
    existing: dict[str, int],
    travel: dict,
    start: date,
    end: date,
    rules: dict,
) -> dict[date, int]:
    adjusted = dict(counts)
    years = sorted({day.year for day in adjusted} | {parse_day(day).year for day in existing})

    for year in years:
        generated_total = sum(count for day, count in adjusted.items() if day.year == year)
        existing_total = sum(count for day, count in existing.items() if parse_day(day).year == year)
        burst_target = annual_burst_count(year, generated_total, existing_total, rules)
        if burst_target == 0:
            continue

        burst_rules = rules.get("annual_bursts", {})
        target_config = burst_rules.get("target_count", {})
        year_start = max(start, date(year, 1, 1))
        year_end = min(end, date(year, 12, 31))
        candidates = [
            day
            for day in each_day(year_start, year_end)
            if adjusted.get(day, 0) >= 2
            and adjusted.get(day, 0) < 10
            and day != start
            and day != end
            and existing.get(day.isoformat(), 0) == 0
            and travel_skip_reason(travel, day) is None
            and vacation_slowdown_reason(travel, day) is None
            and holiday_slowdown_reason(day) is None
        ]
        selected = select_burst_days(candidates, burst_target, rules)

        for burst_day in selected:
            target_count = choose_count(
                burst_day,
                target_config.get("salt", "annual-burst-target"),
                int(target_config.get("min", 10)),
                int(target_config.get("max", 12)),
            )
            redistribute_for_burst(adjusted, burst_day, target_count, start, end, rules)

    return {day: count for day, count in adjusted.items() if count > 0}


def claude_monthly_vacation_days(
    start: date,
    end: date,
    existing: dict[str, int],
    travel: dict,
    rules: dict,
) -> dict[date, str]:
    vacation_days: dict[date, str] = {}
    rest_rules = rules.get("monthly_rest_days", {})
    if not rest_rules.get("enabled", True):
        return vacation_days
    rest_era = rest_rules.get("era")
    if rest_era not in rules["_eras_by_name"]:
        return vacation_days

    era = rules["_eras_by_name"][rest_era]
    rest_start = max(rules["_start"], era["_start"])
    rest_end = min(rules["_end"], era["_end"] or date.max)
    first_day = max(start, rest_start)
    last_day = min(end, rest_end)
    if first_day > last_day:
        return vacation_days
    month = date(first_day.year, first_day.month, 1)
    final_month = date(last_day.year, last_day.month, 1)

    while month <= final_month:
        month_end = date(month.year + (month.month // 12), (month.month % 12) + 1, 1) - timedelta(days=1)
        # Select from the full configured month so a shorter preview does not
        # move rest days into its requested window.
        window_start = max(rest_start, month)
        window_end = min(rest_end, month_end)
        month_key = f"{month.year:04d}-{month.month:02d}"
        minimum = int(rest_rules.get("min_days_per_month", 3))
        maximum = int(rest_rules.get("max_days_per_month", minimum))
        target = minimum + stable_int(month_key, rest_rules.get("count_salt", "monthly-rest-count"), maximum - minimum + 1)
        candidates = []

        for day in each_day(window_start, window_end):
            if travel_skip_reason(travel, day) is not None:
                continue
            if vacation_slowdown_reason(travel, day) is not None:
                continue
            if existing.get(day.isoformat(), 0) > 0:
                continue
            if day.year in rules["_annual_total_targets"]:
                if holiday_slowdown_reason(day) is not None or quota_day_count(day, rules) == 0:
                    continue
            elif raw_generated_count(day, rest_era, rules) == 0:
                continue
            candidates.append(day)

        candidates.sort(
            key=lambda item: stable_int(
                item.isoformat(), rest_rules.get("day_salt", "monthly-rest-day"), 1_000_000
            )
        )
        for day in candidates[:target]:
            if start <= day <= end:
                vacation_days[day] = f"{rest_rules.get('label_prefix', 'monthly vacation day')} {month_key}"

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
    rules: dict,
) -> tuple[list[PlannedCommit], Counter, Counter]:
    planned: list[PlannedCommit] = []
    planned_counts: dict[date, int] = {}
    skip_reasons: Counter = Counter()
    real_active_skips: Counter = Counter()
    annual_targets = rules["_annual_total_targets"]
    reduce_eras = rules["_reduce_generated_in_eras"]
    # Plan complete configured years before slicing a preview. Annual quotas,
    # rest days, and burst redistribution must not depend on preview bounds.
    planning_start = max(rules["_start"], date(start.year, 1, 1))
    planning_end = min(rules["_end"], date(end.year, 12, 31))
    monthly_vacation_days = claude_monthly_vacation_days(
        planning_start, planning_end, existing, travel, rules,
    )
    annual_counts = annual_quota_counts(
        planning_start, planning_end, existing, travel, rules,
        excluded_days=set(monthly_vacation_days),
    )

    for day in each_day(planning_start, planning_end):
        era = era_for(day, rules)
        in_requested_range = start <= day <= end
        skip_reason = travel_skip_reason(travel, day)
        if skip_reason:
            if in_requested_range:
                skip_reasons[skip_reason] += 1
            continue
        if day in monthly_vacation_days:
            if in_requested_range:
                skip_reasons[monthly_vacation_days[day]] += 1
            continue

        existing_count = existing.get(day.isoformat(), 0)
        vacation_reason = vacation_slowdown_reason(travel, day)
        if day.year in annual_targets:
            raw_count = annual_counts.get(day, 0)
            count = raw_count
        else:
            raw_count = raw_generated_count(day, era, rules)
            count = generated_count(day, existing_count, rules)
        slowed_count = apply_vacation_slowdown(day, count, vacation_reason, rules)
        if in_requested_range and vacation_reason and count > 0 and slowed_count == 0:
            skip_reasons[f"{vacation_reason} vacation slowdown"] += 1
        count = slowed_count
        if (
            day == rules["_end"]
            and day.year not in annual_targets
            and era in reduce_eras
            and count == 0
            and existing_count == 0
            and not vacation_reason
            and holiday_slowdown_reason(day) is None
        ):
            count = 1
        if in_requested_range and day >= rules["_existing_skip_after"] and era not in reduce_eras and existing_count > 0 and raw_count > 0:
            real_active_skips[era] += 1
        if count == 0:
            continue
        planned_counts[day] = count

    planned_counts = apply_annual_bursts(
        planned_counts, existing, travel, planning_start, planning_end, rules,
    )

    for day in each_day(start, end):
        count = planned_counts.get(day, 0)
        if count == 0:
            continue
        era = era_for(day, rules)
        existing_count = existing.get(day.isoformat(), 0)
        location, timezone = location_for(travel, day)
        roles = active_roles(work_history, day)
        for sequence in range(1, count + 1):
            planned.append(
                PlannedCommit(
                    day=day,
                    sequence=sequence,
                    total_for_day=count,
                    era=era,
                    category=note_category(day, era, sequence, rules),
                    location=location,
                    timezone=timezone,
                    roles=roles,
                    existing_contributions=existing_count,
                )
            )

    return planned, skip_reasons, real_active_skips


def day_path(day: date) -> str:
    return f"{day.year:04d}/{day.month:02d}/{day.day:02d}.jsonl"


def commit_timestamp(item: PlannedCommit, rules: dict) -> datetime:
    if item.sequence < 1 or item.sequence > item.total_for_day:
        raise SystemExit(f"invalid commit sequence for {item.day}")
    commit_times = rules["_commit_times"]
    index = min(item.sequence - 1, len(commit_times) - 1)
    validate_timezone(item.timezone, f"planned commit on {item.day}")
    return datetime.combine(item.day, commit_times[index], tzinfo=ZoneInfo(item.timezone))


def record_line(item: PlannedCommit, rules: dict) -> bytes:
    stamp = commit_timestamp(item, rules)
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
        root = Path(run_git(["rev-parse", "--show-toplevel"]).strip()).resolve()
    except subprocess.CalledProcessError as exc:
        raise SystemExit("run the import from the root of an empty, non-bare Git repository") from exc
    if root != Path.cwd().resolve():
        raise SystemExit("run the import from the repository root")
    if run_git(["for-each-ref", "--format=%(refname)"]).strip():
        raise SystemExit("refusing to import into a repository with existing refs or history")
    try:
        run_git(["rev-parse", "--verify", "HEAD"])
    except subprocess.CalledProcessError:
        pass
    else:
        raise SystemExit("refusing to import over an existing commit history")
    if run_git(["ls-files", "--stage"]).strip():
        raise SystemExit("refusing to import with a nonempty Git index; unstage files first")


def import_identity() -> tuple[str, str]:
    name = git_value(["config", "user.name"], "")
    email = git_value(["config", "user.email"], "")
    if not name or not email:
        raise SystemExit("configure git user.name and user.email before importing history")
    if any(character in name + email for character in "\r\n<>") or "@" not in email or any(character.isspace() for character in email):
        raise SystemExit("git user.name/user.email must be a valid single-line Git identity")
    if name in {"Example Developer", "Your Name"} or email.rsplit("@", 1)[-1].lower() in {"example.com", "example.org", "example.net"}:
        raise SystemExit("replace the placeholder Git identity with your own name and verified GitHub email")
    return name, email


def seed_payloads(input_paths: dict[str, Path] | None = None) -> dict[str, bytes]:
    sources = {name: Path(name) for name in SEED_FILES}
    sources.update(input_paths or {})
    for name in OPTIONAL_TOOLING_FILES:
        if Path(name).is_file():
            sources[name] = Path(name)
    for pattern in (".github/workflows/*.yml", ".github/workflows/*.yaml", "tests/**/*.py", "tests/**/*.json"):
        for path in Path(".").glob(pattern):
            if path.is_file():
                sources[path.as_posix()] = path
    missing = [str(path) for path in sources.values() if not path.is_file()]
    if missing:
        raise SystemExit(f"missing seed files: {', '.join(missing)}")
    return {name: path.read_bytes() for name, path in sorted(sources.items())}


def write_import_stream(
    out,
    planned: list[PlannedCommit],
    work_history: list[dict],
    travel: dict,
    rules: dict,
    seeds: dict[str, bytes],
    identity: tuple[str, str],
) -> dict[str, bytes]:
    """Build the complete stream before any destination repository mutation."""
    author_name, author_email = identity
    day_payloads: dict[str, bytes] = defaultdict(bytes)
    last_metadata_payloads: dict[str, bytes] = {}
    for index, item in enumerate(planned):
        stamp = commit_timestamp(item, rules)
        current_metadata_payloads = metadata_snapshots(work_history, travel, item.day)
        metadata_changes = [
            name for name, payload in current_metadata_payloads.items()
            if last_metadata_payloads.get(name) != payload
        ]
        if index == 0:
            message = f"Initialize contribution notes {item.day.isoformat()}"
        elif set(metadata_changes) == set(METADATA_FILES):
            message = f"Update work and travel history {item.day.isoformat()}"
        elif metadata_changes == ["work_history.json"]:
            message = f"Update work history {item.day.isoformat()}"
        elif metadata_changes == ["travel_history.json"]:
            message = f"Update travel history {item.day.isoformat()}"
        else:
            message = f"Record activity {item.day.isoformat()}"
            if item.sequence > 1:
                message += f" #{item.sequence}"
        out.write(f"commit {REF}\n".encode("ascii"))
        out.write(ident_line("author", author_name, author_email, stamp))
        out.write(ident_line("committer", author_name, author_email, stamp))
        out.write(data_block(message.encode("utf-8")))
        if index == 0:
            for name in INITIAL_SEED_FILES:
                out.write(file_command(name, seeds[name]))
        for name, payload in current_metadata_payloads.items():
            if last_metadata_payloads.get(name) != payload:
                out.write(file_command(name, payload))
                last_metadata_payloads[name] = payload
        if index == len(planned) - 1:
            for name, payload in seeds.items():
                out.write(file_command(name, payload))
        path = day_path(item.day)
        day_payloads[path] += record_line(item, rules)
        out.write(file_command(path, day_payloads[path]))
        out.write(b"\n")
    return {**seeds, **day_payloads}


def validate_output_paths(payloads: dict[str, bytes], seeds: dict[str, bytes]) -> None:
    for name in payloads:
        path = Path(name)
        for ancestor in (path, *path.parents):
            if ancestor.is_symlink():
                raise SystemExit(f"refusing to overwrite or follow a symlink: {ancestor}")
        if path.exists() and (name not in seeds or not path.is_file()):
            raise SystemExit(f"generated output would overwrite an existing path: {name}")
        for parent in path.parents:
            if parent.exists() and not parent.is_dir():
                raise SystemExit(f"generated output needs a directory at: {parent}")


def replace_output_file(path: Path, payload: bytes, mode: int = 0o644) -> None:
    """Replace one file atomically, without following links or truncating inodes."""
    descriptor, temporary_name = tempfile.mkstemp(prefix=".contribution-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def publish_import(temporary_repo: Path, commit: str, payloads: dict[str, bytes], seeds: dict[str, bytes]) -> None:
    """Install the completed tree, then publish main as the last operation.

    The destination has no refs or tracked files. Preserve unrelated untracked
    files and restore its original HEAD, index, and seed files on any failure.
    """
    ensure_importable()
    validate_output_paths(payloads, seeds)
    git_dir = Path(run_git(["rev-parse", "--absolute-git-dir"]).strip())
    head_path = git_dir / "HEAD"
    index_path = Path(run_git(["rev-parse", "--git-path", "index"]).strip())
    original_head = head_path.read_bytes()
    original_index = index_path.read_bytes() if index_path.exists() else None
    originals = {
        name: (Path(name).read_bytes(), stat.S_IMODE(Path(name).stat().st_mode))
        for name in payloads if Path(name).exists()
    }
    created_directories: list[Path] = []
    written: list[Path] = []
    try:
        # The explicit source-only refspec imports objects without creating a ref.
        run_git(["-c", "gc.auto=0", "-c", "maintenance.auto=false", "fetch", "--no-write-fetch-head", "--no-tags", "--no-recurse-submodules", str(temporary_repo), REF])
        ensure_importable()
        for name, payload in payloads.items():
            path = Path(name)
            missing_parents = [parent for parent in path.parents if not parent.exists()]
            for parent in reversed(missing_parents):
                parent.mkdir()
                created_directories.append(parent)
            replace_output_file(path, payload)
            written.append(path)
        run_git(["read-tree", commit])
        run_git(["symbolic-ref", "HEAD", REF])
        run_git(["update-ref", REF, commit, "0" * len(commit)])
    except BaseException as import_error:
        # A failed ref transaction normally changes nothing. CAS deletion also
        # covers failures reported after the ref was published by Git.
        rollback_errors = []
        try:
            if git_value(["rev-parse", "--verify", REF], "") == commit:
                run_git(["update-ref", "-d", REF, commit])
        except (OSError, subprocess.CalledProcessError) as exc:
            rollback_errors.append(f"{REF}: {exc}")
        try:
            replace_output_file(head_path, original_head)
        except OSError as exc:
            rollback_errors.append(f"HEAD: {exc}")
        try:
            if original_index is None:
                index_path.unlink(missing_ok=True)
            else:
                replace_output_file(index_path, original_index)
        except OSError as exc:
            rollback_errors.append(f"index: {exc}")
        for path in reversed(written):
            try:
                original = originals.get(path.as_posix())
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    replace_output_file(path, original[0], original[1])
            except OSError as exc:
                rollback_errors.append(f"{path}: {exc}")
        for directory in reversed(created_directories):
            try:
                directory.rmdir()
            except OSError as exc:
                rollback_errors.append(f"{directory}: {exc}")
        if rollback_errors:
            raise RuntimeError("import failed; rollback needs manual review: " + "; ".join(rollback_errors)) from import_error
        raise


def import_history(
    planned: list[PlannedCommit],
    work_history: list[dict] | None = None,
    travel: dict | None = None,
    rules: dict | None = None,
    input_paths: dict[str, Path] | None = None,
) -> None:
    if not planned:
        raise SystemExit("nothing to import")
    ensure_importable()
    if work_history is None:
        work_history = load_work_history(Path("work_history.json"))
    if travel is None:
        travel = load_travel(Path("travel_history.json"))
    if rules is None:
        rules = load_contribution_rules(Path("contribution_rules.json"))
    identity = import_identity()
    seeds = seed_payloads(input_paths)
    git_dir = Path(run_git(["rev-parse", "--absolute-git-dir"]).strip())
    lock_path = git_dir / "contribution-import.lock"
    try:
        lock = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise SystemExit(f"another import is running, or a stale lock needs review: {lock_path}") from exc
    try:
        with tempfile.TemporaryDirectory(prefix="contribution-history-") as temporary:
            directory = Path(temporary)
            stream_path = directory / "history.fi"
            with stream_path.open("wb") as out:
                payloads = write_import_stream(out, planned, work_history, travel, rules, seeds, identity)
            validate_output_paths(payloads, seeds)
            temporary_repo = directory / "repository.git"
            environment = os.environ.copy()
            for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE"):
                environment.pop(key, None)
            object_format = run_git(["rev-parse", "--show-object-format"]).strip()
            subprocess.run(["git", "init", "--quiet", "--bare", f"--object-format={object_format}", str(temporary_repo)], check=True, env=environment)
            with stream_path.open("rb") as stream:
                subprocess.run(["git", "-C", str(temporary_repo), "fast-import", "--quiet"], stdin=stream, check=True, env=environment)
            commit = subprocess.check_output(["git", "-C", str(temporary_repo), "rev-parse", REF], text=True, env=environment).strip()
            publish_import(temporary_repo, commit, payloads, seeds)
    finally:
        os.close(lock)
        lock_path.unlink(missing_ok=True)


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
    rules = load_contribution_rules(Path(args.rules))
    start = parse_day(args.start) if args.start else rules["_start"]
    end = parse_day(args.end) if args.end else rules["_end"]
    if start < rules["_start"] or end > rules["_end"]:
        raise SystemExit(f"date range must stay within {rules['_start']}..{rules['_end']}")
    if end < start:
        raise SystemExit("--end must be on or after --start")

    existing = load_existing_contributions(Path(args.existing_contributions))
    work_history = load_work_history(Path(args.work_history))
    travel = load_travel(Path(args.travel_history))
    planned, skip_reasons, real_active_skips = plan_commits(start, end, existing, work_history, travel, rules)
    for item in planned:
        commit_timestamp(item, rules)

    print_summary(planned, skip_reasons, real_active_skips)

    if args.import_history:
        import_history(planned, work_history, travel, rules, {
            "contribution_rules.json": Path(args.rules),
            "existing_contributions.json": Path(args.existing_contributions),
            "work_history.json": Path(args.work_history),
            "travel_history.json": Path(args.travel_history),
        })
        print("\nimport complete")
    elif not args.dry_run:
        print("\nno import requested; pass --import-history to write Git history")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, subprocess.CalledProcessError) as exc:
        sys.exit(f"cannot generate history: {exc}")

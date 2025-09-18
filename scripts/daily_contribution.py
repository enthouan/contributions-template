#!/usr/bin/env python3
"""Append one safety-net contribution record if today has no activity."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from calendar import monthrange
from hashlib import sha256
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


GRAPHQL_URL = "https://api.github.com/graphql"


def output(name: str, value: str) -> None:
    path = os.getenv("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")
    print(f"{name}={value}")


def no_change(reason: str) -> int:
    output("changed", "false")
    print(reason)
    return 0


def stable_percentage(value: str, salt: str) -> int:
    digest = sha256(f"{salt}:{value}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % 100


def monthly_vacation_dates(day: datetime) -> set[str]:
    month_key = f"{day.year:04d}-{day.month:02d}"
    target = 3 + (stable_percentage(month_key, "monthly-vacation-count") % 2)
    _, month_days = monthrange(day.year, day.month)
    candidates = [f"{month_key}-{number:02d}" for number in range(1, month_days + 1)]
    candidates.sort(key=lambda value: (stable_percentage(value, "monthly-vacation-day"), value))
    return set(candidates[:target])


def thanksgiving_date(year: int):
    day = datetime(year, 11, 1).date()
    while day.weekday() != 3:
        day += timedelta(days=1)
    return day + timedelta(days=21)


def holiday_slowdown_label(day: datetime) -> str | None:
    local_day = day.date()
    thanksgiving = thanksgiving_date(local_day.year)
    if thanksgiving - timedelta(days=3) <= local_day <= thanksgiving + timedelta(days=3):
        return "Thanksgiving slowdown"
    if local_day.month == 12 and local_day.day >= 23:
        return "Christmas/New Year slowdown"
    if local_day.month == 1 and local_day.day <= 2:
        return "Christmas/New Year slowdown"
    return None


def graphql(token: str, query: str, variables: dict) -> dict:
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "daily-contribution-safety-net",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"GitHub GraphQL request failed: {exc.code} {detail}") from exc

    if payload.get("errors"):
        raise SystemExit(json.dumps(payload["errors"], indent=2))
    return payload["data"]


def contribution_count(token: str, login: str, day: datetime, use_viewer: bool) -> int:
    local_start = datetime.combine(day.date(), time.min, tzinfo=day.tzinfo)
    local_end = datetime.combine(day.date(), time(23, 59, 59), tzinfo=day.tzinfo)
    variables = {
        "from": local_start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "to": local_end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "login": login,
    }
    if use_viewer:
        query = """
        query($from: DateTime!, $to: DateTime!) {
          viewer {
            login
            contributionsCollection(from: $from, to: $to) {
              contributionCalendar {
                weeks {
                  contributionDays {
                    date
                    contributionCount
                  }
                }
              }
            }
          }
        }
        """
        collection = graphql(token, query, variables)["viewer"]["contributionsCollection"]
    else:
        query = """
        query($login: String!, $from: DateTime!, $to: DateTime!) {
          user(login: $login) {
            contributionsCollection(from: $from, to: $to) {
              contributionCalendar {
                weeks {
                  contributionDays {
                    date
                    contributionCount
                  }
                }
              }
            }
          }
        }
        """
        user = graphql(token, query, variables)["user"]
        if user is None:
            raise SystemExit(f"GitHub user not found: {login}")
        collection = user["contributionsCollection"]

    target = day.date().isoformat()
    for week in collection["contributionCalendar"]["weeks"]:
        for contribution_day in week["contributionDays"]:
            if contribution_day["date"] == target:
                return int(contribution_day["contributionCount"])
    return 0


def record_path(day: datetime) -> Path:
    return Path(f"{day.year:04d}") / f"{day.month:02d}" / f"{day.day:02d}.jsonl"


def main() -> int:
    tz = ZoneInfo(os.getenv("CONTRIBUTION_TIMEZONE", "America/Los_Angeles"))
    now = datetime.now(timezone.utc).astimezone(tz)
    author_name = os.getenv("CONTRIBUTION_AUTHOR_NAME", "").strip()
    author_email = os.getenv("CONTRIBUTION_AUTHOR_EMAIL", "").strip()
    if not author_name:
        raise SystemExit("missing repository variable CONTRIBUTION_AUTHOR_NAME")
    if not author_email:
        raise SystemExit("missing repository variable CONTRIBUTION_AUTHOR_EMAIL")

    holiday_label = holiday_slowdown_label(now)
    if holiday_label and stable_percentage(now.date().isoformat(), holiday_label) >= 15:
        return no_change(f"{holiday_label}: {now.date().isoformat()}")

    if now.date().isoformat() in monthly_vacation_dates(now):
        return no_change(f"monthly vacation day: {now.date().isoformat()}")

    if now.weekday() in {0, 5, 6} and stable_percentage(now.date().isoformat(), "quiet-safety-net") >= 35:
        return no_change(f"quiet safety-net day: {now.date().isoformat()}")

    token = os.getenv("GH_CONTRIBUTIONS_TOKEN")
    use_viewer = bool(token)
    if not token:
        token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        raise SystemExit("missing GH_CONTRIBUTIONS_TOKEN or GITHUB_TOKEN")

    login = os.getenv("CONTRIBUTION_LOGIN") or os.getenv("GITHUB_REPOSITORY_OWNER", "")
    if not login:
        raise SystemExit("missing CONTRIBUTION_LOGIN or GITHUB_REPOSITORY_OWNER")
    count = contribution_count(token, login, now, use_viewer)
    if count > 0:
        return no_change(f"{now.date().isoformat()} already has {count} contribution(s)")

    commit_time = datetime.combine(now.date(), time(18, 5), tzinfo=tz)
    path = record_path(commit_time)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing_lines = []
    if path.exists():
        existing_lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    for line in existing_lines:
        try:
            existing_record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if existing_record.get("source") == "github_action":
            return no_change(f"{now.date().isoformat()} already has a safety-net record")

    record = {
        "category": "daily-safety-net",
        "committed_at": commit_time.isoformat(timespec="seconds"),
        "date": now.date().isoformat(),
        "era": "daily_safety_net",
        "existing_contributions": count,
        "location": os.getenv("CONTRIBUTION_LOCATION", "Local"),
        "roles": [],
        "sequence": len(existing_lines) + 1,
        "source": "github_action",
        "timezone": os.getenv("CONTRIBUTION_TIMEZONE", "America/Los_Angeles"),
        "total_generated_for_day": len(existing_lines) + 1,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")

    output("changed", "true")
    output("date", now.date().isoformat())
    output("path", path.as_posix())
    output("author_date", commit_time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    output("author_email", author_email)
    output("author_name", author_name)
    return 0


if __name__ == "__main__":
    sys.exit(main())

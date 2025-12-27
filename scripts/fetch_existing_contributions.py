#!/usr/bin/env python3
"""Fetch GitHub contribution counts into existing_contributions.json."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path


GRAPHQL_URL = "https://api.github.com/graphql"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", default=os.getenv("CONTRIBUTION_LOGIN", ""))
    parser.add_argument("--start", default="2014-05-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--output", default="existing_contributions.json")
    return parser.parse_args()


def graphql(token: str, query: str, variables: dict) -> dict:
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "contribution-history-template",
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


def contribution_windows(start_day: date, end_day: date) -> Iterator[tuple[date, date]]:
    """Yield contiguous, inclusive windows shorter than GitHub's one-year limit."""
    window_start = start_day
    while window_start <= end_day:
        # At most 365 dates, even when the window includes February 29.
        window_end = window_start + timedelta(days=min(364, (end_day - window_start).days))
        yield window_start, window_end
        if window_end == end_day:
            return
        window_start = window_end + timedelta(days=1)


def fetch_window(token: str, login: str, start_day: date, end_day: date) -> dict[str, int]:
    variables = {
        "from": datetime.combine(start_day, time.min, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
        "to": datetime.combine(end_day, time.max, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if login:
        variables["login"] = login
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
    else:
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

    active_days = {}
    for week in collection["contributionCalendar"]["weeks"]:
        for contribution_day in week["contributionDays"]:
            day = date.fromisoformat(contribution_day["date"])
            count = int(contribution_day["contributionCount"])
            if start_day <= day <= end_day and count > 0:
                active_days[day.isoformat()] = count
    return active_days


def write_json_atomically(path: Path, payload: dict) -> None:
    data = json.dumps(payload, indent=2, sort_keys=False) + "\n"
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(data)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> int:
    args = parse_args()
    token = os.getenv("GH_CONTRIBUTIONS_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        raise SystemExit("set GH_CONTRIBUTIONS_TOKEN or GITHUB_TOKEN")

    start_day = date.fromisoformat(args.start)
    end_day = date.fromisoformat(args.end)
    if end_day < start_day:
        raise SystemExit("--end must be on or after --start")

    active_days = {}
    for window_start, window_end in contribution_windows(start_day, end_day):
        active_days.update(fetch_window(token, args.login, window_start, window_end))

    payload = {
        "source": "GitHub contribution calendar",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "range": {
            "start": start_day.isoformat(),
            "end": end_day.isoformat(),
        },
        "active_days": dict(sorted(active_days.items())),
    }
    write_json_atomically(Path(args.output), payload)
    print(f"wrote {args.output}: {len(active_days)} active days")
    return 0


if __name__ == "__main__":
    sys.exit(main())

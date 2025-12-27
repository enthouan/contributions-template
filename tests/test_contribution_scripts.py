import argparse
from contextlib import contextmanager, redirect_stdout
from datetime import date, datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import daily_contribution as daily
from scripts import fetch_existing_contributions as fetch


def calendar_response(days, field="user"):
    return {
        field: {
            "contributionsCollection": {
                "contributionCalendar": {"weeks": [{"contributionDays": days}]}
            }
        }
    }


def dates_between(start, end):
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


@contextmanager
def working_directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class FetchExistingContributionsTests(unittest.TestCase):
    def test_requested_dates_are_covered_once_in_windows_under_one_year(self):
        ranges = [
            ("2014-05-01", "2026-09-26"),
            ("2023-12-31", "2025-01-01"),
            ("2024-02-29", "2025-03-01"),
            ("2024-02-29", "2024-02-29"),
        ]
        for start, end in ranges:
            with self.subTest(start=start, end=end), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "baseline.json"
                original = '{"active_days":{}}\n'
                output.write_text(original, encoding="utf-8")
                args = argparse.Namespace(login="configured-user", start=start, end=end, output=str(output))
                queried_days = []
                last_end = None

                def graphql(token, query, variables):
                    nonlocal last_end
                    self.assertEqual(output.read_text(encoding="utf-8"), original)
                    self.assertEqual(token, "test-token")
                    self.assertEqual(variables["login"], "configured-user")
                    self.assertIn("user(login: $login)", query)
                    window_start = datetime.fromisoformat(variables["from"].replace("Z", "+00:00"))
                    window_end = datetime.fromisoformat(variables["to"].replace("Z", "+00:00"))
                    self.assertLess(window_end - window_start, timedelta(days=365))
                    if last_end is not None:
                        self.assertEqual(window_start, last_end + timedelta(microseconds=1))
                    last_end = window_end
                    window_days = dates_between(window_start.date(), window_end.date())
                    queried_days.extend(window_days)
                    return calendar_response([
                        {"date": day.isoformat(), "contributionCount": 2} for day in window_days
                    ])

                with patch.dict(os.environ, {"GH_CONTRIBUTIONS_TOKEN": "test-token"}, clear=True), \
                        patch.object(fetch, "parse_args", return_value=args), \
                        patch.object(fetch, "graphql", side_effect=graphql), \
                        redirect_stdout(io.StringIO()):
                    self.assertEqual(fetch.main(), 0)

                expected_days = dates_between(date.fromisoformat(start), date.fromisoformat(end))
                self.assertEqual(queried_days, expected_days)
                payload = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(payload["range"], {"start": start, "end": end})
                self.assertEqual(payload["active_days"], {day.isoformat(): 2 for day in expected_days})
                self.assertEqual(list(Path(tmp).iterdir()), [output])

    def test_later_api_failure_preserves_existing_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "baseline.json"
            original = '{"active_days":{"2020-01-01":4}}\n'
            output.write_text(original, encoding="utf-8")
            args = argparse.Namespace(login="example", start="2020-01-01", end="2022-01-01", output=str(output))
            first_response = calendar_response([{"date": "2020-01-01", "contributionCount": 10}])
            with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}, clear=True), \
                    patch.object(fetch, "parse_args", return_value=args), \
                    patch.object(fetch, "graphql", side_effect=[first_response, SystemExit("API failed")]) as api:
                with self.assertRaisesRegex(SystemExit, "API failed"):
                    fetch.main()
            self.assertEqual(api.call_count, 2)
            self.assertEqual(output.read_text(encoding="utf-8"), original)
            self.assertEqual(list(Path(tmp).iterdir()), [output])

    def test_failed_atomic_replacement_preserves_baseline_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "baseline.json"
            output.write_text("previous baseline\n", encoding="utf-8")
            with patch.object(fetch.os, "replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    fetch.write_json_atomically(output, {"active_days": {}})
            self.assertEqual(output.read_text(encoding="utf-8"), "previous baseline\n")
            self.assertEqual(list(Path(tmp).iterdir()), [output])

    def test_window_ignores_zero_counts_and_dates_outside_its_range(self):
        days = [
            {"date": "2024-02-27", "contributionCount": 9},
            {"date": "2024-02-28", "contributionCount": 0},
            {"date": "2024-02-29", "contributionCount": 3},
            {"date": "2024-03-01", "contributionCount": 9},
        ]
        with patch.object(fetch, "graphql", return_value=calendar_response(days)):
            self.assertEqual(
                fetch.fetch_window("test-token", "example", date(2024, 2, 28), date(2024, 2, 29)),
                {"2024-02-29": 3},
            )

    def test_fetch_without_login_queries_viewer(self):
        with patch.object(fetch, "graphql", return_value=calendar_response([], field="viewer")) as api:
            self.assertEqual(fetch.fetch_window("test-token", "", date(2024, 2, 29), date(2024, 2, 29)), {})
        _, query, variables = api.call_args.args
        self.assertIn("viewer", query)
        self.assertNotIn("login", variables)

    def test_invalid_date_range_makes_no_request_or_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "baseline.json"
            args = argparse.Namespace(login="example", start="2024-03-01", end="2024-02-29", output=str(output))
            with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}, clear=True), \
                    patch.object(fetch, "parse_args", return_value=args), \
                    patch.object(fetch, "graphql") as api:
                with self.assertRaisesRegex(SystemExit, "--end must be on or after --start"):
                    fetch.main()
            api.assert_not_called()
            self.assertFalse(output.exists())


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 23, 22, 0, tzinfo=timezone.utc).astimezone(tz)


class DailyContributionTests(unittest.TestCase):
    def environment(self):
        return {
            "CONTRIBUTION_AUTHOR_NAME": "Example User",
            "CONTRIBUTION_AUTHOR_EMAIL": "example@example.com",
            "CONTRIBUTION_LOGIN": "configured-user",
            "GITHUB_REPOSITORY_OWNER": "repository-owner",
            "CONTRIBUTION_TIMEZONE": "UTC",
        }

    def test_both_token_types_query_configured_user_and_skip_existing_activity(self):
        for token_variable in ("GH_CONTRIBUTIONS_TOKEN", "GITHUB_TOKEN"):
            with self.subTest(token_variable=token_variable), tempfile.TemporaryDirectory() as tmp:
                env = self.environment()
                env[token_variable] = "test-token"
                response = calendar_response([{"date": "2026-09-23", "contributionCount": 3}])
                with working_directory(tmp), patch.dict(os.environ, env, clear=True), \
                        patch.object(daily, "datetime", FixedDateTime), \
                        patch.object(daily, "monthly_vacation_dates", return_value=set()), \
                        patch.object(daily, "graphql", return_value=response) as api, \
                        redirect_stdout(io.StringIO()):
                    self.assertEqual(daily.main(), 0)
                token, query, variables = api.call_args.args
                self.assertEqual(token, "test-token")
                self.assertEqual(variables["login"], "configured-user")
                self.assertIn("user(login: $login)", query)
                self.assertNotIn("viewer", query)
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_unconfigured_login_falls_back_to_repository_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self.environment()
            del env["CONTRIBUTION_LOGIN"]
            env["GH_CONTRIBUTIONS_TOKEN"] = "test-token"
            with working_directory(tmp), patch.dict(os.environ, env, clear=True), \
                    patch.object(daily, "datetime", FixedDateTime), \
                    patch.object(daily, "monthly_vacation_dates", return_value=set()), \
                    patch.object(daily, "contribution_count", return_value=1) as count, \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(daily.main(), 0)
            self.assertEqual(count.call_args.args[1], "repository-owner")

    def test_repeated_zero_activity_check_creates_only_one_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self.environment()
            env["GH_CONTRIBUTIONS_TOKEN"] = "test-token"
            with working_directory(tmp), patch.dict(os.environ, env, clear=True), \
                    patch.object(daily, "datetime", FixedDateTime), \
                    patch.object(daily, "monthly_vacation_dates", return_value=set()), \
                    patch.object(daily, "graphql", return_value=calendar_response([])), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(daily.main(), 0)
                output = Path(tmp) / "2026/09/23.jsonl"
                original = output.read_text(encoding="utf-8")
                self.assertEqual(daily.main(), 0)
                self.assertEqual(output.read_text(encoding="utf-8"), original)
            records = [json.loads(line) for line in original.splitlines()]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["source"], "github_action")
            self.assertEqual(records[0]["date"], "2026-09-23")

    def test_unknown_configured_user_fails(self):
        with patch.object(daily, "graphql", return_value={"user": None}):
            with self.assertRaisesRegex(SystemExit, "GitHub user not found: missing-user"):
                daily.contribution_count("test-token", "missing-user", FixedDateTime.now(timezone.utc))


if __name__ == "__main__":
    unittest.main()

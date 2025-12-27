"""Regression tests for annual quotas and deterministic partial previews."""

from collections import Counter
from datetime import date, timedelta
from pathlib import Path
import unittest

import generate_history as generator


RULES_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "contribution_rules.json"


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.rules = generator.load_contribution_rules(RULES_FIXTURE)
        self.rules["_start"] = date(2025, 1, 1)
        self.rules["_end"] = date(2025, 12, 31)
        self.rules["_annual_total_targets"] = {2025: 500}
        self.travel = {
            "default_location": {"name": "Example City", "timezone": "UTC"},
            "exact": set(),
            "no_generation": [],
            "vacation": [],
            "locations": [],
        }

    def plan(self, start=None, end=None, existing=None):
        return generator.plan_commits(
            start or self.rules["_start"], end or self.rules["_end"],
            existing or {}, [], self.travel, self.rules,
        )[0]

    def test_annual_target_is_exact_after_monthly_rest_days(self):
        planned = self.plan()
        rest_days = generator.claude_monthly_vacation_days(
            self.rules["_start"], self.rules["_end"], {}, self.travel, self.rules,
        )
        self.assertTrue(rest_days)
        self.assertEqual(500, len(planned))
        self.assertFalse({item.day for item in planned} & rest_days.keys())

    def test_existing_activity_at_or_above_target_generates_nothing(self):
        # A non-holiday final date also covers the old forced end-day commit.
        self.rules["_end"] = date(2025, 9, 16)
        for total in (500, 700):
            with self.subTest(total=total):
                self.assertEqual([], self.plan(existing={"2025-01-02": total}))

    def test_end_day_cannot_exceed_a_small_annual_target(self):
        self.rules["_end"] = date(2025, 9, 16)
        self.rules["_annual_total_targets"] = {2025: 1}
        self.assertEqual(1, len(self.plan()))

    def test_existing_activity_counts_toward_total_and_is_not_overwritten(self):
        existing = {"2025-01-02": 120, "2025-08-19": 30}
        planned = self.plan(existing=existing)
        self.assertEqual(500, len(planned) + sum(existing.values()))
        self.assertFalse({item.day.isoformat() for item in planned} & existing.keys())

    def test_existing_activity_outside_configured_range_counts_toward_year(self):
        self.rules["_start"] = date(2025, 6, 1)
        self.rules["_annual_total_targets"] = {2025: 200}
        existing = {"2025-01-02": 150}
        planned = self.plan(existing=existing)
        self.assertEqual(50, len(planned))
        self.assertTrue(all(item.day >= self.rules["_start"] for item in planned))
        self.assertEqual([], self.plan(existing={"2025-01-02": 200}))

    def test_partial_preview_matches_full_year_slice_with_existing_activity(self):
        existing = {"2025-01-02": 120, "2025-12-20": 30}
        start, end = date(2025, 8, 5), date(2025, 8, 22)
        full = self.plan(existing=existing)
        expected = [item for item in full if start <= item.day <= end]
        self.assertTrue(expected)
        self.assertEqual(expected, self.plan(start, end, existing))

    def test_one_day_quota_preview_does_not_squeeze_in_annual_target(self):
        full = generator.annual_quota_counts(
            self.rules["_start"], self.rules["_end"], {}, self.travel, self.rules,
        )
        day = next(iter(full))
        partial = generator.annual_quota_counts(day, day, {}, self.travel, self.rules)
        self.assertEqual({day: full[day]}, partial)
        self.assertLess(sum(partial.values()), 500)

    def test_travel_vacation_and_rest_exclusions_survive_quotas_and_bursts(self):
        self.rules["_annual_total_targets"] = {2025: 250}
        self.travel["exact"] = {date(2025, 2, 14)}
        self.travel["no_generation"] = [(date(2025, 4, 1), date(2025, 4, 15), "buffer")]
        self.travel["vacation"] = [(date(2025, 7, 1), date(2025, 7, 14), "vacation")]
        planned = self.plan()
        rest_days = generator.claude_monthly_vacation_days(
            self.rules["_start"], self.rules["_end"], {}, self.travel, self.rules,
        )
        self.assertEqual(250, len(planned))
        for item in planned:
            self.assertIsNone(generator.travel_skip_reason(self.travel, item.day))
            self.assertIsNone(generator.vacation_slowdown_reason(self.travel, item.day))
            self.assertIsNone(generator.holiday_slowdown_reason(item.day))
            self.assertNotIn(item.day, rest_days)

    def test_burst_redistribution_preserves_annual_total(self):
        with_bursts = self.plan()
        self.rules["annual_bursts"]["enabled"] = False
        without_bursts = self.plan()
        self.assertEqual(500, len(with_bursts))
        self.assertEqual(len(without_bursts), len(with_bursts))
        self.assertGreaterEqual(max(Counter(item.day for item in with_bursts).values()), 10)

    def test_impossible_target_explains_capacity_and_exclusions(self):
        self.rules["_annual_total_targets"] = {2025: 5000}
        with self.assertRaisesRegex(SystemExit, r"cannot meet 2025 annual target 5000: .*eligible days.*rest exclusions"):
            self.plan()

    def test_monthly_rest_selection_is_stable_for_partial_month(self):
        full = generator.claude_monthly_vacation_days(
            date(2025, 8, 1), date(2025, 8, 31), {}, self.travel, self.rules,
        )
        day = next(iter(full))
        partial = generator.claude_monthly_vacation_days(day, day, {}, self.travel, self.rules)
        self.assertEqual({day: full[day]}, partial)

    def test_monthly_rest_days_stop_at_the_configured_era_end(self):
        rest_era = self.rules["monthly_rest_days"]["era"]
        era_end = date(2025, 8, 14)
        self.rules["_eras_by_name"][rest_era]["_end"] = era_end
        rest_days = generator.claude_monthly_vacation_days(
            era_end + timedelta(days=1), self.rules["_end"], {}, self.travel, self.rules,
        )
        self.assertEqual({}, rest_days)


if __name__ == "__main__":
    unittest.main()

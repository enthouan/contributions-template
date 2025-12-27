"""Exercise the importer against disposable Git repositories."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest import mock

import generate_history as history


SOURCE = Path(__file__).resolve().parents[1]


class ImportHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="history-import-test-")
        self.addCleanup(self.temporary.cleanup)
        self.repository = Path(self.temporary.name)
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        self.environment = mock.patch.dict(os.environ, environment, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.original_directory = Path.cwd()
        os.chdir(self.repository)
        self.addCleanup(os.chdir, self.original_directory)
        files = [*history.SEED_FILES, *history.OPTIONAL_TOOLING_FILES]
        files.extend(path.relative_to(SOURCE).as_posix() for path in SOURCE.glob(".github/workflows/*.yml"))
        files.extend(path.relative_to(SOURCE).as_posix() for pattern in ("tests/**/*.py", "tests/**/*.json") for path in SOURCE.glob(pattern))
        for name in set(files):
            if name in {"contribution_rules.json", "existing_contributions.json", "work_history.json", "travel_history.json"}:
                continue
            source = SOURCE / name
            if source.is_file():
                destination = self.repository / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
        shutil.copyfile(SOURCE / "tests/fixtures/contribution_rules.json", "contribution_rules.json")
        Path("existing_contributions.json").write_text(json.dumps({"active_days": {}}))
        Path("work_history.json").write_text(json.dumps({
            "source": "Synthetic test work history",
            "entries": [{"slug": "test-role", "type": "education", "organization": "Test School", "title": "Student", "location": "Test City", "start": "2010-01-03"}],
        }))
        Path("travel_history.json").write_text(json.dumps({
            "source": "Synthetic test travel history",
            "default_location": {"name": "Test City", "timezone": "UTC"},
            "exact_travel_dates": [], "no_generation_ranges": [], "vacation_ranges": [], "location_ranges": [],
        }))
        self.git("init", "--quiet", "-b", "master")
        self.git("config", "user.name", "Audit User")
        self.git("config", "user.email", "12345+audit@users.noreply.github.com")

    def git(self, *arguments):
        return subprocess.check_output(["git", *arguments], text=True).strip()

    def plan(self):
        rules = history.load_contribution_rules(Path("contribution_rules.json"))
        work = history.load_work_history(Path("work_history.json"))
        travel = history.load_travel(Path("travel_history.json"))
        planned, _, _ = history.plan_commits(date(2010, 1, 3), date(2010, 1, 10), {}, work, travel, rules)
        return planned, work, travel, rules

    def invoke(self, *arguments):
        return subprocess.run(
            [sys.executable, "generate_history.py", "--start", "2010-01-03", "--end", "2010-01-10", *arguments],
            capture_output=True, text=True,
        )

    def assert_empty_repository(self):
        self.assertEqual(self.git("for-each-ref", "--format=%(refname)"), "")
        self.assertEqual(self.git("ls-files", "--stage"), "")
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD"), "master")
        self.assertFalse(Path(".git/contribution-import.lock").exists())

    def test_success_checks_out_main_with_clean_worktree_and_release_files(self):
        planned, work, travel, rules = self.plan()
        history.import_history(planned, work, travel, rules)
        self.assertEqual(self.git("branch", "--show-current"), "main")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(int(self.git("rev-list", "--count", "HEAD")), len(planned))
        self.assertTrue(Path("2010/01/03.jsonl").is_file())
        tracked = self.git("ls-files").splitlines()
        for name in ("LICENSE", "CONTRIBUTING.md", "SECURITY.md", ".github/workflows/ci.yml", "tests/test_import_history.py", "tests/fixtures/contribution_rules.json"):
            self.assertIn(name, tracked)
        self.assertEqual(self.git("fsck", "--full"), "")

    def test_unrelated_files_remain_untouched(self):
        Path("personal-notes.txt").write_bytes(b"keep exactly\n")
        history.import_history(*self.plan())
        self.assertEqual(Path("personal-notes.txt").read_bytes(), b"keep exactly\n")
        self.assertEqual(self.git("status", "--porcelain"), "?? personal-notes.txt")

    def test_dry_run_and_import_are_mutually_exclusive(self):
        result = self.invoke("--dry-run", "--import-history")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not allowed with argument", result.stderr)
        self.assert_empty_repository()

    def test_dry_run_does_not_mutate_repository(self):
        result = self.invoke("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_empty_repository()

    def test_missing_or_placeholder_identity_is_rejected_before_mutation(self):
        self.git("config", "--unset", "user.email")
        with self.assertRaisesRegex(SystemExit, "configure git user.name and user.email"):
            history.import_history(*self.plan())
        self.assert_empty_repository()
        self.git("config", "user.email", "developer@example.com")
        with self.assertRaisesRegex(SystemExit, "placeholder Git identity"):
            history.import_history(*self.plan())
        self.assert_empty_repository()

    def test_alternate_inputs_are_preserved_under_canonical_names(self):
        selected = {}
        options = []
        for name, flag in (
            ("contribution_rules.json", "--rules"),
            ("existing_contributions.json", "--existing-contributions"),
            ("work_history.json", "--work-history"),
            ("travel_history.json", "--travel-history"),
        ):
            payload = json.loads(Path(name).read_text())
            payload["source"] = f"selected {name}"
            if name == "work_history.json":
                payload["entries"][0]["organization"] = "Selected Organization"
            encoded = (json.dumps(payload, indent=4) + "\n").encode()
            custom = Path(f"custom-{name}")
            custom.write_bytes(encoded)
            selected[name] = encoded
            options.extend((flag, str(custom)))
        result = self.invoke("--import-history", *options)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name, encoded in selected.items():
            self.assertEqual(Path(name).read_bytes(), encoded)
            self.assertEqual(subprocess.check_output(["git", "show", f"HEAD:{name}"]), encoded)
            self.assertEqual(Path(f"custom-{name}").read_bytes(), encoded)
        record = json.loads(Path("2010/01/03.jsonl").read_text().splitlines()[0])
        self.assertEqual(record["roles"][0]["organization"], "Selected Organization")
        self.assertEqual(self.git("diff", "--exit-code"), "")
        self.assertEqual(self.git("diff", "--cached", "--exit-code"), "")

    def test_invalid_later_timezone_fails_dry_run_and_import_without_refs(self):
        path = Path("travel_history.json")
        travel = json.loads(path.read_text())
        travel["location_ranges"] = [
            {"location": "First", "timezone": "UTC", "start": "2010-01-03", "end": "2010-01-06"},
            {"location": "Later", "timezone": "INVALID_ZONE", "start": "2010-01-07"},
        ]
        path.write_text(json.dumps(travel))
        for flag in ("--dry-run", "--import-history"):
            result = self.invoke(flag)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unavailable IANA timezone", result.stderr)
            self.assert_empty_repository()
        self.assertFalse(Path("2010").exists())

    def test_invalid_planned_timestamp_never_publishes_partial_history(self):
        planned, work, travel, rules = self.plan()
        planned[-1] = replace(planned[-1], timezone="INVALID_ZONE")
        with self.assertRaisesRegex(SystemExit, "unavailable IANA timezone"):
            history.import_history(planned, work, travel, rules)
        self.assert_empty_repository()
        self.assertFalse(Path("2010").exists())

    def test_invalid_rule_shapes_and_ranges_fail_before_import(self):
        path = Path("contribution_rules.json")
        original = json.loads(path.read_text())
        mutations = [
            lambda rules: rules.update(commit_times=[]),
            lambda rules: rules["eras"][0].update(note_categories=[]),
            lambda rules: rules["eras"][1]["density"].update(count=[4, 1]),
            lambda rules: rules["eras"][1]["density"].update(active_probability=1.5),
            lambda rules: rules["eras"][-1]["density"].update(weekday_probabilities=[0.5]),
            lambda rules: rules["monthly_rest_days"].update(min_days_per_month=5, max_days_per_month=2),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                rules = json.loads(json.dumps(original))
                mutation(rules)
                path.write_text(json.dumps(rules))
                with self.assertRaises(SystemExit):
                    history.load_contribution_rules(path)
                self.assert_empty_repository()

    def test_existing_refs_are_rejected_even_on_an_orphan_branch(self):
        Path("valuable.txt").write_text("keep history\n")
        self.git("add", "valuable.txt")
        self.git("commit", "--quiet", "-m", "Existing history")
        original = self.git("rev-parse", "master")
        self.git("checkout", "--quiet", "--orphan", "scratch")
        with self.assertRaisesRegex(SystemExit, "existing refs or history"):
            history.import_history(*self.plan())
        self.assertEqual(self.git("rev-parse", "master"), original)
        self.assertEqual(self.git("for-each-ref", "--format=%(refname)"), "refs/heads/master")

    def test_staged_files_are_rejected_and_preserved(self):
        self.git("add", "README.md")
        before = self.git("ls-files", "--stage")
        with self.assertRaisesRegex(SystemExit, "nonempty Git index"):
            history.import_history(*self.plan())
        self.assertEqual(self.git("ls-files", "--stage"), before)
        self.assertEqual(self.git("for-each-ref", "--format=%(refname)"), "")

    def test_generated_path_collision_is_rejected_and_preserved(self):
        path = Path("2010/01/03.jsonl")
        path.parent.mkdir(parents=True)
        path.write_bytes(b"unrelated content\n")
        with self.assertRaisesRegex(SystemExit, "overwrite an existing path"):
            history.import_history(*self.plan())
        self.assertEqual(path.read_bytes(), b"unrelated content\n")
        self.assert_empty_repository()

    def test_symlink_in_output_path_is_rejected(self):
        external = Path(self.temporary.name).parent / (self.repository.name + "-external")
        external.mkdir()
        self.addCleanup(external.rmdir)
        Path("2010").symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(SystemExit, "symlink"):
            history.import_history(*self.plan())
        self.assertEqual(list(external.iterdir()), [])
        self.assert_empty_repository()

    def test_failed_checkout_rolls_back_seed_files_index_head_and_refs(self):
        original = Path("work_history.json").read_bytes()
        selected = Path("custom-work.json")
        custom = json.loads(original)
        custom["source"] = "replacement should roll back"
        selected.write_text(json.dumps(custom))
        original_run_git = history.run_git

        def fail_read_tree(arguments):
            if arguments[0] == "read-tree":
                raise subprocess.CalledProcessError(1, ["git", *arguments])
            return original_run_git(arguments)

        with mock.patch.object(history, "run_git", side_effect=fail_read_tree):
            with self.assertRaises(subprocess.CalledProcessError):
                history.import_history(*self.plan(), input_paths={"work_history.json": selected})
        self.assertEqual(Path("work_history.json").read_bytes(), original)
        self.assertFalse(Path("2010").exists())
        self.assert_empty_repository()

    def test_readonly_original_is_restored_after_a_later_write_failure(self):
        path = Path("work_history.json")
        original = path.read_bytes()
        path.chmod(0o444)
        selected = Path("custom-work.json")
        custom = json.loads(original)
        custom["source"] = "must be rolled back"
        selected.write_text(json.dumps(custom))
        real_replace = os.replace

        def fail_generated_file(source, destination):
            if Path(destination).as_posix() == "2010/01/03.jsonl":
                raise PermissionError("injected later write failure")
            return real_replace(source, destination)

        with mock.patch.object(history.os, "replace", side_effect=fail_generated_file):
            with self.assertRaisesRegex(PermissionError, "injected later write failure"):
                history.import_history(*self.plan(), input_paths={"work_history.json": selected})
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(path.stat().st_mode & 0o777, 0o444)
        self.assertFalse(Path("2010").exists())
        self.assertEqual(list(self.repository.rglob(".contribution-*")), [])
        self.assert_empty_repository()

    def test_hardlinked_source_outside_repository_is_not_modified(self):
        path = Path("work_history.json")
        original = path.read_bytes()
        external = self.repository.parent / (self.repository.name + "-hardlink.json")
        os.link(path, external)
        self.addCleanup(external.unlink)
        selected = Path("custom-work.json")
        custom = json.loads(original)
        custom["source"] = "selected replacement"
        selected.write_text(json.dumps(custom))
        history.import_history(*self.plan(), input_paths={"work_history.json": selected})
        self.assertEqual(external.read_bytes(), original)
        self.assertEqual(path.read_bytes(), selected.read_bytes())

    def test_failure_reported_after_ref_update_rolls_back_published_ref(self):
        original_run_git = history.run_git

        def fail_after_publish(arguments):
            result = original_run_git(arguments)
            if arguments[:2] == ["update-ref", history.REF]:
                raise subprocess.CalledProcessError(1, ["git", *arguments])
            return result

        with mock.patch.object(history, "run_git", side_effect=fail_after_publish):
            with self.assertRaises(subprocess.CalledProcessError):
                history.import_history(*self.plan())
        self.assert_empty_repository()
        self.assertFalse(Path("2010").exists())


if __name__ == "__main__":
    unittest.main()

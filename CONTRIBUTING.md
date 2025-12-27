# Contributing

Bug reports and focused pull requests are welcome. For a larger change, open
an issue explaining the problem and proposed behavior first.

Use Python 3.10 or newer and Git 2.29 or newer on macOS or Linux. No third-party Python
dependencies are required. Run these commands from the repository root:

```sh
python3 -m unittest discover -s tests -v
python3 generate_history.py --dry-run
git diff --check
```

Add a regression test for changes to planning, import safety, or calendar
handling. Tests must use disposable repositories and mocked network responses,
so they can run without credentials or changes to a contributor's Git config.
Never run `--import-history` against this source repository or another repository
with existing work.

Keep examples fictional and omit personal timelines, private contribution
counts, credentials, and local machine paths from reports and fixtures. Include
the Python/Git versions, relevant command, expected result, and a minimal
synthetic configuration when reporting a bug. Report security issues privately
using [SECURITY.md](SECURITY.md).

Use four spaces for Python indentation and keep changes consistent with the
existing standard-library implementation. Explain the resulting behavior and
the checks you ran in your pull request. Contributions are provided under the
project's [MIT License](LICENSE).

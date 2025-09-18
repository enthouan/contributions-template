# Contributions Template

Generate a deterministic, private contribution-history repository from local
work and travel notes.

This repository is the reusable template. It should stay small and normal. Run
the generator inside a separate empty output repository when you want to build
the synthetic history.

## Files

- `generate_history.py`: deterministic history planner and `git fast-import`
  writer.
- `work_history.json`: example work/education history.
- `travel_history.json`: example travel, timezone, vacation, and no-generation
  ranges.
- `existing_contributions.json`: baseline GitHub contribution counts to avoid
  painting over real activity after `2014-05-01`.
- `scripts/daily_contribution.py`: optional daily safety-net script for future
  activity.
- `scripts/fetch_existing_contributions.py`: optional helper for fetching the
  baseline contribution counts from GitHub.
- `.github/workflows/daily-contribution.yml`: optional scheduled GitHub Action.

## Basic Flow

1. Create a private empty output repository, for example
   `yourname/contributions`.
2. Clone this template somewhere separate.
3. Copy or edit the JSON config files:
   - `work_history.json`
   - `travel_history.json`
   - `existing_contributions.json`
4. Optional: fetch your real contribution baseline:
   ```sh
   export GH_CONTRIBUTIONS_TOKEN=ghp_your_token_here
   python3 scripts/fetch_existing_contributions.py --login your-github-login
   ```
5. Preview:
   ```sh
   python3 generate_history.py --dry-run
   ```
6. In the empty output repository, copy the template files in, then import:
   ```sh
   python3 generate_history.py --import-history
   ```
7. Push the generated output repository:
   ```sh
   git push -u origin main
   ```

## Daily Safety Net

The workflow is manual-only in this template so GitHub Actions does not run
automatically. Enable scheduled runs only in the generated output repo, after
the initial history has been pushed.

Uncomment the `schedule` block in `.github/workflows/daily-contribution.yml`
when ready. The example uses one daily cron at `02:05 UTC`; adjust it for your
timezone if needed. The Python script records safety-net commits at `6:05 PM` in
`CONTRIBUTION_TIMEZONE`.

Set these repository variables in the generated output repo:

- `CONTRIBUTION_AUTHOR_NAME`
- `CONTRIBUTION_AUTHOR_EMAIL`
- `CONTRIBUTION_LOGIN`
- `CONTRIBUTION_LOCATION`
- `CONTRIBUTION_TIMEZONE`

Set this repository secret if you want private contribution awareness:

- `GH_CONTRIBUTIONS_TOKEN`

Without `GH_CONTRIBUTIONS_TOKEN`, the workflow falls back to `GITHUB_TOKEN` and
can only rely on contribution data visible to that token.

Useful setup commands:

```sh
gh variable set CONTRIBUTION_AUTHOR_NAME --body "Your Name"
gh variable set CONTRIBUTION_AUTHOR_EMAIL --body "you@example.com"
gh variable set CONTRIBUTION_LOGIN --body "your-github-login"
gh variable set CONTRIBUTION_LOCATION --body "Your City"
gh variable set CONTRIBUTION_TIMEZONE --body "America/Los_Angeles"
gh secret set GH_CONTRIBUTIONS_TOKEN
```

## Notes

The generator emits retrospective `work_history.json` and `travel_history.json`
snapshots. Job and travel-history notes appear a few days after the relevant
role or trip ends, rather than appearing in very old commits before they could
reasonably be known.

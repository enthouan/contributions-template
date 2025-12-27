# Contributions Template

[Read the article: Activity is not productivity](https://www.antoinemenard.com/articles/activity-is-not-productivity/)

Generate a deterministic, synthetic contribution-history repository from work
and travel notes. The generated commits are timeline records, not evidence of
software shipped or productivity.

This repo is the reusable template and setup guide. Keep it as a small tooling
repo. The generated contribution history should live in a separate empty private
repository, for example `yourname/contributions`.

## Requirements

- Python 3.10 or newer, with an IANA timezone database available to `zoneinfo`.
  The scripts and tests use only the Python standard library.
- Git 2.29 or newer and `rsync` for the setup commands below, on macOS or Linux.
- A GitHub account and Git authentication for pushing the output repository.
- The [GitHub CLI](https://cli.github.com/) (`gh auth login`) if you use the
  variable, secret, and workflow commands below.

No package installation is needed on a system with Python and timezone data.
Minimal Linux images may need their operating system's `tzdata` package.

## How To Set Up This Repo

This template is the tooling source. The generated contribution history should
be imported into a separate empty private repository.

The clean path is:

1. Prepare a clean output repo.
2. Build your personal timeline config.
3. Generate and push the initial history.
4. Enable the daily safety net.

### Step 1: Prepare A Clean Output Repo

Create or clone two repositories locally:

```text
contributions-template   # this template/tooling repo
contributions            # empty private output repo
```

The output repo should be newly created on GitHub as a private repo with no
README, license, `.gitignore`, or initial commit. Then clone it next to this
template repo:

```sh
git clone git@github.com:YOUR_LOGIN/contributions.git ../contributions
```

Copy the template files into the output repo without copying this repo's Git
history:

```sh
rsync -av \
  --exclude .git \
  --exclude __pycache__ \
  --exclude '*/__pycache__' \
  --exclude .venv \
  --exclude '.env*' \
  ./ ../contributions/
```

From this point on, run setup commands from the output repo:

```sh
cd ../contributions
```

Set the identity used for the initial generated commits:

```sh
git config user.name "Your Name"
git config user.email "YOUR_GITHUB_LINKED_EMAIL"
```

Use an email connected to your GitHub account or your GitHub-provided `noreply`
address. The generator refuses to import without a configured name and email;
the workflow variables configured later do not set this local Git identity.
See [GitHub's contribution attribution requirements](https://docs.github.com/en/account-and-profile/how-tos/contribution-settings/troubleshooting-missing-contributions).

Check that the output repo still has no commits before importing history:

```sh
git status --short --branch
git rev-parse --verify HEAD >/dev/null 2>&1 \
  && echo "repo has commits: stop" \
  || echo "empty repo: OK"
```

The `empty repo: OK` line is expected. If it prints `repo has commits: stop`,
stop and use a truly empty output repo.

### Step 2: Build Your Timeline Config

Edit:

```text
work_history.json
travel_history.json
contribution_rules.json
```

The included history files are examples. Replace them with your own data before
generation. The included rules file is usable as-is, but you should tune it if
your timeline needs different eras, yearly targets, or activity density.

Validate the JSON after editing:

```sh
python3 -m json.tool work_history.json >/dev/null
python3 -m json.tool travel_history.json >/dev/null
python3 -m json.tool contribution_rules.json >/dev/null
```

### `work_history.json` Schema

```json
{
  "source": "Short note about where this data came from",
  "entries": [
    {
      "slug": "short-stable-id",
      "type": "education",
      "organization": "Organization name",
      "title": "Role or program title",
      "location": "City, region",
      "start": "YYYY-MM-DD",
      "end": "YYYY-MM-DD"
    }
  ]
}
```

Use `type: "education"` for school and `type: "experience"` for work. Omit
`end` only for a current/open-ended role.

### `travel_history.json` Schema

```json
{
  "source": "Short note about where this data came from",
  "default_location": {
    "name": "Home city",
    "timezone": "America/Los_Angeles"
  },
  "exact_travel_dates": ["YYYY-MM-DD"],
  "no_generation_ranges": [
    {
      "label": "uncertain return buffer",
      "start": "YYYY-MM-DD",
      "end": "YYYY-MM-DD"
    }
  ],
  "vacation_ranges": [
    {
      "label": "summer vacation",
      "start": "YYYY-MM-DD",
      "end": "YYYY-MM-DD"
    }
  ],
  "location_ranges": [
    {
      "location": "City",
      "timezone": "Europe/London",
      "start": "YYYY-MM-DD",
      "end": "YYYY-MM-DD"
    }
  ]
}
```

Use IANA timezone names such as `America/Los_Angeles`, `Europe/London`,
`Europe/Paris`, `America/New_York`, or `Pacific/Tahiti`.

### `contribution_rules.json`

This file controls how active the generated history looks. Most users should
start by editing these sections:

- `date_range`: default start and end dates for generation.
- `eras`: date ranges, note categories, and density profile for each life/work
  era.
- `annual_total_targets`: exact yearly totals for years that should land in a
  specific range after existing GitHub activity is considered.
- `annual_bursts`: deterministic 10-12 commit spike days for busy years.
- `monthly_rest_days`: random-looking rest days in the assisted-coding era.
- `commit_times`: local author times used for multi-commit days.

Annual targets describe the complete calendar-year total. Generated commits are
allocated only within that year's intersection with `date_range`; all supplied
existing activity for the year counts, including outside `date_range`. A shorter
`--start`/`--end` preview selects that portion of the same yearly plan; it does not
compress the whole target into the preview. If existing activity already meets
or exceeds the target, no additional commits are generated for that year. An
impossible target fails with an error instead of silently producing a smaller
total.

The generator supports these density types:

- `college_coursework`: term-time, summer, and holiday-gap behavior.
- `weekend_only`: sparse weekend maintenance.
- `weekday_project`: mostly weekday project work with occasional weekends.
- `year_taper`: per-year probabilities for management or reduced activity.
- `assisted_gradient`: month-by-month ramp with quieter weekends/Mondays.

### LLM Prompts

These prompts are designed for ChatGPT, Claude, or another LLM. Paste your own
resume, LinkedIn export, flight history, calendar notes, or email snippets after
the prompt.

#### Prompt: Generate Work History JSON

```text
You are helping me produce a clean `work_history.json` file for a deterministic
Git contribution-history generator.

Use only the facts I provide. Do not invent employers, dates, titles, schools,
or locations. If a date is uncertain, use the most conservative first day of the
month or year and list the uncertainty in the `source` string.

Output valid JSON only, using this exact shape:

{
  "source": "Resume/LinkedIn/history notes provided by the user",
  "entries": [
    {
      "slug": "lowercase-kebab-case-id",
      "type": "education or experience",
      "organization": "Organization name",
      "title": "Role or program title",
      "location": "City, region",
      "start": "YYYY-MM-DD",
      "end": "YYYY-MM-DD"
    }
  ]
}

Rules:
- Use `type: "education"` for schools and `type: "experience"` for jobs.
- Use ISO dates only.
- If only a month is known, use the first day of the month for `start` and the
  last day of the month for `end`.
- If only a year is known, use January 1 for `start` and December 31 for `end`.
- Omit `end` only for the current role.
- Sort entries by `start`.
- Keep slugs stable and short.

Here is my source material:

[PASTE RESUME / LINKEDIN / WORK HISTORY HERE]
```

#### Prompt: Generate Travel History JSON

```text
You are helping me produce a clean `travel_history.json` file for a deterministic
Git contribution-history generator.

Use only the travel facts I provide. Do not invent trips. If a return date or
travel date is approximate, create a conservative `no_generation_ranges` buffer
instead of pretending the exact date is known.

Output valid JSON only, using this exact shape:

{
  "source": "Travel history provided by the user",
  "default_location": {
    "name": "Home city",
    "timezone": "IANA timezone"
  },
  "exact_travel_dates": ["YYYY-MM-DD"],
  "no_generation_ranges": [
    {
      "label": "description",
      "start": "YYYY-MM-DD",
      "end": "YYYY-MM-DD"
    }
  ],
  "vacation_ranges": [
    {
      "label": "description",
      "start": "YYYY-MM-DD",
      "end": "YYYY-MM-DD"
    }
  ],
  "location_ranges": [
    {
      "location": "City or region",
      "timezone": "IANA timezone",
      "start": "YYYY-MM-DD",
      "end": "YYYY-MM-DD"
    }
  ]
}

Rules:
- Use IANA timezone names, not abbreviations.
- Put actual flight/travel days in `exact_travel_dates`.
- Put approximate/uncertain transition periods in `no_generation_ranges`.
- Put vacation stays in `vacation_ranges`.
- Put where I was based each date in `location_ranges`.
- Leave no overlapping `location_ranges`.
- Leave gaps for exact travel dates and no-generation ranges.
- Sort all arrays chronologically.
- If a final location range is current, omit its `end`.

Here is my travel source material:

[PASTE FLIGHTS / EMAIL NOTES / CALENDAR / TRAVEL HISTORY HERE]
```

#### Prompt: Audit The JSON Before Running

```text
Review these `work_history.json`, `travel_history.json`, and
`contribution_rules.json` files for a deterministic Git contribution-history
generator.

Check for:
- invalid JSON
- invalid ISO dates
- overlapping location ranges
- exact travel dates that are also inside location ranges
- vacation ranges that fall outside matching location ranges
- missing IANA timezones
- future facts appearing too early
- date gaps that should be intentional travel/no-generation gaps
- contribution eras that overlap or leave unexpected gaps
- annual targets that conflict with vacation/travel/no-generation ranges

Return:
1. A short list of issues.
2. Corrected JSON if changes are needed.
3. No extra commentary if the files are valid.

Here are the files:

[PASTE work_history.json, travel_history.json, and contribution_rules.json HERE]
```

#### Prompt: Tune Contribution Rules JSON

```text
You are helping me tune `contribution_rules.json` for a deterministic Git
contribution-history generator.

Use my work and travel timeline to create believable contribution-density eras.
Do not change factual work or travel dates. Only adjust generation rules.

Output valid JSON only, preserving this top-level shape:
- source
- date_range
- existing_activity
- commit_times
- holiday_slowdown
- vacation_slowdown
- eras
- annual_total_targets
- annual_quota
- annual_bursts
- monthly_rest_days

Rules:
- Use non-overlapping era date ranges.
- Keep activity credible for each life stage.
- Prefer weekday-heavy activity for full-time project/work eras.
- Use lower activity during vacations, holidays, and management-heavy eras.
- Add annual targets only for years that need calibrated totals.
- Keep burst days enabled only for years with enough total activity to justify
  visible spikes.
- Keep commit times in local daytime/evening hours.
- Do not invent new jobs, schools, trips, or locations.

Here are my current files:

[PASTE work_history.json, travel_history.json, and contribution_rules.json HERE]
```

### Step 3: Generate And Push Initial History

If GitHub already shows real activity for your account, fetch a baseline first.
The generator uses this file to skip or reduce generated commits on dates that
already have real contributions.

Create a GitHub personal access token for the account whose calendar you are
checking, then export it locally. For a classic token, the `read:user` scope
allows private/internal contributions to be included, as documented by
[GitHub's ContributionsCollection API](https://docs.github.com/en/graphql/reference/users#contributionscollection).
These calendar queries do not require repository write access; pushing uses
your separately configured Git authentication.

```sh
export GH_CONTRIBUTIONS_TOKEN=ghp_your_token_here
```

Fetch existing contribution counts:

```sh
python3 scripts/fetch_existing_contributions.py --login YOUR_LOGIN
```

This fetches the requested range in windows of at most 365 days and writes
`existing_contributions.json` only after all requests succeed. Use `--start` and
`--end` to cover your intended timeline; the default starts on `2014-05-01`.
For years with annual targets, fetch the full calendar year, including activity
outside the generation range (through today for the current year), so it can all
count toward the target.

If you do not want to fetch a baseline, leave the included
`existing_contributions.json` in place. It contains an empty `active_days` map.

Preview the plan:

```sh
python3 generate_history.py --dry-run
```

To compare a rules variant without replacing the default file, pass it
explicitly:

```sh
python3 generate_history.py --rules contribution_rules.variant.json --dry-run
```

Import into the current empty repository:

```sh
python3 generate_history.py --import-history
```

The import validates the plan, builds history in a temporary repository, and
then installs it into the empty output repository on `main`. Existing history,
staged files, and conflicting output files are rejected. Alternate inputs passed
with `--rules`, `--work-history`, `--travel-history`, or
`--existing-contributions` are saved under the canonical filenames in the final
commit. Unrelated untracked files remain untouched.

`--dry-run` and `--import-history` are mutually exclusive. After a successful
import, inspect the result before pushing:

```sh
git status --short
git log -5 --format=fuller
python3 -m unittest discover -s tests -v
```

The standard setup leaves a clean checkout. Custom input files kept alongside
the template may still appear as untracked files.

Push:

```sh
git push -u origin main
```

Ensure `main` is the output repository's default branch. To display anonymized
counts from a private output repository, enable **Private contributions** under
your profile's **Contribution settings** ([GitHub instructions](https://docs.github.com/en/account-and-profile/how-tos/contribution-settings/manage-visibility-settings-for-private-contributions-and-achievements)).
Qualifying commits can take up to 24 hours to appear; check the author email and
default branch if they remain missing.

### Step 4: Enable The Daily Safety Net

The optional workflow keeps the graph warm after the generated range. It is
manual-only in this template so the daily workflow does not run automatically.
Enable scheduled runs only in the generated output repo, after the initial
history has been pushed.

Uncomment the `schedule` block in `.github/workflows/daily-contribution.yml`
when ready. The example uses one daily cron at `02:05 UTC`; adjust it for your
timezone if needed. The Python script records safety-net commits at `6:05 PM` in
`CONTRIBUTION_TIMEZONE`. It skips many holidays, monthly rest days, and quiet
weekend/Monday days. If GitHub already shows activity for the day, it does
nothing.

Set these repository variables from your local output repo:

```sh
gh variable set CONTRIBUTION_AUTHOR_NAME --body "Your Name"
gh variable set CONTRIBUTION_AUTHOR_EMAIL --body "you@example.com"
gh variable set CONTRIBUTION_LOGIN --body "your-github-login"
gh variable set CONTRIBUTION_LOCATION --body "Your City"
gh variable set CONTRIBUTION_TIMEZONE --body "America/Los_Angeles"
```

Set this repository secret if you want the workflow to detect private
contribution activity before creating a safety-net commit:

```sh
gh secret set GH_CONTRIBUTIONS_TOKEN
```

Use a token with `read:user` access to the configured account as described above.
The script always checks `CONTRIBUTION_LOGIN`, regardless of which token is used.
Without this secret, it uses the workflow's `GITHUB_TOKEN`, whose view of private
activity outside the output repository is limited. The workflow commits with its
own `contents: write` permission, not with the calendar token.

If you enabled the schedule, commit and push the workflow change from the output
repository so GitHub receives it:

```sh
git add .github/workflows/daily-contribution.yml
git commit -m "enable daily contribution schedule"
git push
```

Run it manually once:

```sh
gh workflow run "Daily contribution safety net"
gh run list --workflow "Daily contribution safety net" --limit 5
```

If the manual run creates no commit, check the run logs. Common valid reasons
are: today already has activity, today is a monthly rest day, today is inside a
holiday slowdown, or today is a quiet weekend/Monday safety-net day.

## How The History Is Written

Generated records are stored as:

```text
YYYY/MM/DD.jsonl
```

Each commit uses local timestamps based on `travel_history.json`.

Busy years also get a few deterministic 10-12 commit burst days. Those spikes
are created by redistributing commits from nearby normal days, so yearly totals
stay stable instead of being inflated.

Density rules, annual targets, bursts, note categories, assisted-era rest days,
and commit times are configured in `contribution_rules.json`.

The generator also emits retrospective `work_history.json` and
`travel_history.json` snapshots. Job and travel-history notes appear a few days
after the relevant role or trip ends, rather than appearing in very old commits
before they could reasonably be known.

## Safety Notes

- Keep the generated output repo private unless you are comfortable publishing
  the timeline data.
- Do not run `--import-history` in a repo that already has important commits.
- Do not paste GitHub tokens into chat, commits, or JSON files.
- After generation, review `git log --stat`, yearly counts, and a few travel
  periods before pushing.

## Development And Contributions

Run the regression suite from the repository root:

```sh
python3 -m unittest discover -s tests -v
```

Tests use mocked calendar responses and disposable Git repositories. CI runs
them on Linux and macOS, including the minimum supported Python version.
See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidance and
[SECURITY.md](SECURITY.md) for private vulnerability reports.

## License

This project is licensed under the [MIT License](LICENSE). The generator retains
the license with the tooling in the output repository.

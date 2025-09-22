# Contributions Template

Generate a deterministic private contribution-history repository from work and
travel notes.

This repo is the reusable template and setup guide. Keep it as a small tooling
repo. The generated contribution history should live in a separate empty private
repository, for example `yourname/contributions`.

## The 3 Things You Need

1. **Personal timeline config**
   - `work_history.json`: school, jobs, roles, and major career eras.
   - `travel_history.json`: location ranges, exact travel days, vacation
     ranges, and timezones.

2. **Real contribution baseline**
   - `existing_contributions.json`: days where GitHub already shows real
     activity, so generated commits can skip or reduce those dates.

3. **An empty private output repo**
   - This is where the generated `YYYY/MM/DD.jsonl` history is imported and
     pushed.
   - Do not use the template repo itself as the generated output repo.

## Recommended Setup

Create two local folders:

```text
contributions-template   # this tooling/config repo
contributions            # empty private output repo with generated history
```

Clone or create the empty output repo:

```sh
git clone git@github.com:YOUR_LOGIN/contributions.git ../contributions
```

Copy the template files into the empty output repo:

```sh
rsync -av \
  --exclude .git \
  --exclude __pycache__ \
  --exclude '*/__pycache__' \
  ./ ../contributions/
```

Then work from the output repo:

```sh
cd ../contributions
```

## Step 1: Build Your Timeline Config

Edit:

```text
work_history.json
travel_history.json
```

The included files are examples. Replace them with your own data before
generation.

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

## LLM Prompts

These prompts are designed for ChatGPT, Claude, or another LLM. Paste your own
resume, LinkedIn export, flight history, calendar notes, or email snippets after
the prompt.

### Prompt: Generate Work History JSON

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

### Prompt: Generate Travel History JSON

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

### Prompt: Audit The JSON Before Running

```text
Review these `work_history.json` and `travel_history.json` files for a
deterministic Git contribution-history generator.

Check for:
- invalid JSON
- invalid ISO dates
- overlapping location ranges
- exact travel dates that are also inside location ranges
- vacation ranges that fall outside matching location ranges
- missing IANA timezones
- future facts appearing too early
- date gaps that should be intentional travel/no-generation gaps

Return:
1. A short list of issues.
2. Corrected JSON if changes are needed.
3. No extra commentary if the files are valid.

Here are the files:

[PASTE BOTH JSON FILES HERE]
```

## Step 2: Fetch Real Contribution Baseline

This step is optional but recommended for any years where GitHub already has
real activity.

Create a GitHub personal access token and export it locally:

```sh
export GH_CONTRIBUTIONS_TOKEN=ghp_your_token_here
```

Fetch existing contribution counts:

```sh
python3 scripts/fetch_existing_contributions.py --login YOUR_LOGIN
```

This writes `existing_contributions.json`.

## Step 3: Generate And Push The Output Repo

Preview the plan:

```sh
python3 generate_history.py --dry-run
```

Import into the current empty repository:

```sh
python3 generate_history.py --import-history
```

Push:

```sh
git push -u origin main
```

If GitHub does not update the profile graph immediately, wait. Contribution
indexing is not instant.

## Daily Safety Net

The optional workflow keeps the graph warm after the generated range. It is
manual-only in this template so GitHub Actions does not run automatically.
Enable scheduled runs only in the generated output repo, after the initial
history has been pushed.

Uncomment the `schedule` block in `.github/workflows/daily-contribution.yml`
when ready. The example uses one daily cron at `02:05 UTC`; adjust it for your
timezone if needed. The Python script records safety-net commits at `6:05 PM` in
`CONTRIBUTION_TIMEZONE`. It skips many holidays, monthly rest days, and quiet
weekend/Monday days. If GitHub already shows activity for the day, it does
nothing.

Set these repository variables in the generated output repo:

```sh
gh variable set CONTRIBUTION_AUTHOR_NAME --body "Your Name"
gh variable set CONTRIBUTION_AUTHOR_EMAIL --body "you@example.com"
gh variable set CONTRIBUTION_LOGIN --body "your-github-login"
gh variable set CONTRIBUTION_LOCATION --body "Your City"
gh variable set CONTRIBUTION_TIMEZONE --body "America/Los_Angeles"
```

Set this repository secret if you want private contribution awareness:

```sh
gh secret set GH_CONTRIBUTIONS_TOKEN
```

Then test:

```sh
gh workflow run "Daily contribution safety net"
gh run list --workflow "Daily contribution safety net" --limit 5
```

## How The History Is Written

Generated records are stored as:

```text
YYYY/MM/DD.jsonl
```

Each commit uses local timestamps based on `travel_history.json`.

Busy years also get a few deterministic 10-12 commit burst days. Those spikes
are created by redistributing commits from nearby normal days, so yearly totals
stay stable instead of being inflated.

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

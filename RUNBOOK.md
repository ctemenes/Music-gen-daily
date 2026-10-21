# Runbook

## How the daily mix happens

1. **2:55am Brisbane**: the `Daily mix` GitHub Actions workflow (`.github/workflows/daily.yml`) runs `daily.py`
   for today's date, retries once with a re-rolled seed if QA fails, then `publish.sh` commits
   `days/<date>/` + `history.json` and creates the release `mix-<date>` with the audio.
2. **About 3:40am**: a Claude scheduled task checks that the release exists and QA passed, re-triggers the
   workflow once if it's missing, and sends a summary.

GitHub can delay scheduled workflows by 10 to 30 minutes at busy times, so allow for that.

## Checking a day (from any session with the repo attached)

Use the REST API via `gh api` (GraphQL-based commands like `gh release list` are blocked in Claude sessions,
and Claude sessions cannot create or delete releases, so publishing always happens in Actions).

```
DAY=$(TZ=Australia/Brisbane date +%F)
gh api repos/ctemenes/Music-gen-daily/releases/tags/mix-$DAY --jq '.html_url, [.assets[].name]'
gh api "repos/ctemenes/Music-gen-daily/actions/runs?per_page=3" --jq '.workflow_runs[] | [.status, .conclusion, .event, .created_at, .html_url]'
curl -sL https://raw.githubusercontent.com/ctemenes/Music-gen-daily/main/days/$DAY/brief.md
```

## Triggering a run by hand

```
gh api -X POST repos/ctemenes/Music-gen-daily/actions/workflows/daily.yml/dispatches \
  -H "Content-Type: application/json" --input - <<EOF
{"ref":"main","inputs":{"date":"$DAY"}}
EOF
```

Optional inputs: `tracks` (e.g. "12"), `theme` (an id from `dnbgen/themes.py`), `test` ("true" = don't publish
or touch history; files are attached to the run as an artifact for 3 days).

## Failure handling

- Workflow failed: open the run's logs (`gh api repos/ctemenes/Music-gen-daily/actions/runs/<id>/jobs`), fix,
  push, re-dispatch for that date. Re-running a date replaces that day's release.
- QA failed twice: `qa.json` lists the problems. Re-dispatch is safe; the workflow re-rolls the seed.
- Never edit `history.json` by hand except to remove a day you deleted.

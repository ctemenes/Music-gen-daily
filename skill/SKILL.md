---
name: music-gen-daily
description: Run, check, re-run or tweak Carti's daily Y2K drum & bass mix (GitHub repo ctemenes/Music-gen-daily), and fetch a day's handoff pack for titles, thumbnails and upload.
---

# music-gen-daily

A new Y2K / PS1-era drum & bass mix (in the spirit of Rollcage Stage II) is generated every day by Python
scripts in the GitHub repo **ctemenes/Music-gen-daily** (public). Every track is synthesized from scratch and
hands over to the next with an 8-bar DJ transition. Each day has a theme that sets both the sound and the
visual brief, and mix length rotates by weekday (about 22 to 58 minutes).

## How it is wired

- **Generation and publishing happen in GitHub Actions**, not in Claude sessions. The `Daily mix` workflow
  (`.github/workflows/daily.yml`) runs at about 2:55am Brisbane time, runs `daily.py`, retries once with a
  re-rolled seed if QA fails, commits `days/<date>/` + `history.json`, and creates release `mix-<date>`.
- Claude sessions **cannot create, edit or delete GitHub releases** (HTTP 403) and **GraphQL is blocked**
  (so `gh release list` / `gh run list` fail). Use REST: `gh api repos/ctemenes/Music-gen-daily/...`.
  Anything that publishes must go through a workflow dispatch.
- Release assets per day: `mix.mp3` (320kbps), `short_1..3.mp3` (45s, opens on a build, drop at
  `drop_hits_at_seconds`), `midi.zip`, `brief.json`, `description.txt`.
- Committed per day under `days/<date>/`: `brief.json`, `brief.md`, `description.txt`, `tracklist.txt`,
  `plan.json`, `qa.json`.

## Setup in a session

1. Attach the repo with push access (`add_repo` owner `ctemenes`, repo `Music-gen-daily`) and clone it with
   `git clone --depth 1 https://github.com/ctemenes/Music-gen-daily` (long timeout).
2. Only needed for local work: `pip install --break-system-packages -q -r requirements.txt` and check `ffmpeg -version`.

## Daily check (what the 3:40am scheduled task does)

1. `DAY=$(TZ=Australia/Brisbane date +%F)`
2. Check the release: `gh api repos/ctemenes/Music-gen-daily/releases/tags/mix-$DAY --jq '.html_url, [.assets[] | .name]'`
   - Expect `mix.mp3`, `short_1.mp3`, `short_2.mp3`, `short_3.mp3`, `midi.zip`, `brief.json`, `description.txt`.
3. If it is missing, look at the latest runs:
   `gh api "repos/ctemenes/Music-gen-daily/actions/runs?per_page=3" --jq '.workflow_runs[] | [.status, .conclusion, .event, .created_at, .html_url]'`
   - Still queued or running: wait (poll every 2 minutes, up to 30 minutes), then re-check.
   - Never started or failed: read the failed job's log
     (`gh api repos/ctemenes/Music-gen-daily/actions/runs/<id>/jobs --jq '.jobs[].steps[] | [.name, .conclusion]'`),
     then dispatch once for `$DAY` (see below) and wait for it.
4. Read `days/$DAY/brief.md` from the clone after `git pull` (or
   `curl -sL https://raw.githubusercontent.com/ctemenes/Music-gen-daily/main/days/$DAY/brief.md`) and sanity check:
   QA pass, track count matches, timestamps increase, duration 15 to 70 minutes.
5. Report with SendUserMessage: theme, duration, track count, BPM range, release link, the tracklist, and any
   problem. If it failed twice, say exactly what failed and stop; never fake a result.

## Run or re-run a date

```
gh api -X POST repos/ctemenes/Music-gen-daily/actions/workflows/daily.yml/dispatches \
  -H "Content-Type: application/json" --input - <<EOF
{"ref":"main","inputs":{"date":"YYYY-MM-DD"}}
EOF
```

Optional inputs (all strings): `tracks` (e.g. "12"), `theme` (id from `dnbgen/themes.py`), `test` ("true" =
don't publish or touch history; output is attached to the run as an artifact for 3 days). Re-running a date
replaces that day's release. The dispatch returns nothing; find the run with the runs query above.

## Handoff to the title / thumbnail / upload agent

Point it at the release (`https://github.com/ctemenes/Music-gen-daily/releases/tag/mix-<date>`) or at
`days/<date>/brief.json`. The brief contains:

- `theme`: name, scene, setting, time of day, weather, mood words, hex palette, visual motifs, hook line, 3 thumbnail prompts
- `music`: duration, BPM range, style tags, `tracks[]` (title, timestamp, BPM, style)
- `audio`: release URL, mix URL, shorts with `drop_hits_at_seconds`, MIDI URL, `gh release download` command
- `youtube`: finished description (Carti's template, tracklist filled in, includes the "generated with custom
  Python scripts" line), tracklist text, tags, hashtags
- `handoff`: tasks and rules (no Rollcage/Sony/PlayStation logos, box art or footage; no affiliation claims)
- `qa`: automated check results

## Tweaking

- Themes (sound odds, tempo range, track-name words, visuals, hook line): `dnbgen/themes.py`. Add a theme by
  copying an existing dict with a new `id`.
- Tracks per mix by weekday: `LENGTH_PLAN` in `dnbgen/recipes.py` (about 2.2 minutes per track).
- Synth sounds: `dnbgen/engine.py`. Transitions: `dnbgen/transitions.py`.
- Schedule: the `cron` line in `.github/workflows/daily.yml` (UTC; Brisbane is UTC+10 all year).
- After any change: commit, push, then dispatch a `test` run for one date with `tracks` "3" and check it
  passes before relying on it.

Write any message or copy for Carti without em dashes.

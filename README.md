# music-gen-daily

A new Y2K drum & bass mix every day, in the spirit of Rollcage Stage II and the PS1 racing era.
Everything is synthesized from scratch with Python: no samples, fully original.

Each day gets a **theme** (Neon Rain Night Drive, Outback Dust Run, Orbital Station Circuit, ...) that sets
both the sound palette and the visual brief. Mix length rotates by weekday (about 22 to 58 minutes), and
every track hands over to the next with an 8-bar DJ transition (tempo glide, filter sweep, impact, riser).

## Daily output

Each day is published as a GitHub release `mix-YYYY-MM-DD` with:

| File | What it is |
|---|---|
| `mix.mp3` | the full continuous mix, 320kbps |
| `short_1..3.mp3` | 45s clips that open on a build and hit a drop (for Shorts) |
| `midi.zip` | MIDI for every track |
| `brief.json` | the handoff pack for the next agent (schema below) |
| `description.txt` | ready-to-paste YouTube description with tracklist |

The text files are also committed to `days/YYYY-MM-DD/`. `history.json` tracks used themes and track
names so nothing repeats.

## For the next agent: `brief.json`

- `theme`: name, scene, setting, time of day, weather, mood words, hex palette, visual motifs, hook line, 3 thumbnail prompts
- `music`: duration, BPM range, style tags, and `tracks[]` with title, timestamp, BPM and style
- `audio`: release URL, mix URL, shorts (with `drop_hits_at_seconds` for cutting visuals), MIDI, and a `gh release download` command
- `youtube`: finished description, tracklist text, tags, hashtags
- `handoff`: the tasks (titles, thumbnail, video, Shorts) and rules (no real game logos or footage, no affiliation claims)
- `qa`: automated checks (clipping, clicks at the joins, silence, length)

Download a day: `gh release download mix-2026-10-22 -R ctemenes/Music-gen-daily`

## How it runs

GitHub Actions generates and publishes the mix every day at about 3am Brisbane time
(`.github/workflows/daily.yml`), and a Claude scheduled task checks the result afterwards.
See `RUNBOOK.md` for checking a day, re-running a date and handling failures.

Locally:

```
pip install -r requirements.txt     # plus ffmpeg (with rubberband if available)
python3 daily.py                    # today's mix (Brisbane date), output in out/<date>/
python3 daily.py --tracks 3 --no-history   # quick test
python3 daily.py --theme outback    # force a theme
python3 daily.py --variant 1        # re-roll a day with a different seed
```

## Tweaking the sound

- `dnbgen/themes.py`: add or edit themes (odds of each drum/bass/pad/lead style, tempo range, names, visuals)
- `dnbgen/recipes.py`: `LENGTH_PLAN` sets tracks per mix by weekday
- `dnbgen/engine.py`: the synth itself
- `dnbgen/transitions.py`: the DJ transitions

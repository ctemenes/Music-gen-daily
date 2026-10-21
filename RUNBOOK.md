# Daily run runbook

This is what the 3am scheduled task does. Follow it exactly.

1. **Get the repo**: attach `ctemenes/Music-gen-daily` with push access and clone it (or `git pull` if already cloned).
2. **Install**: `pip install --break-system-packages -q -r requirements.txt`. Check `ffmpeg -version` works.
3. **Generate**: `python3 daily.py` (uses today's date in Brisbane time). Takes about 3 to 12 minutes.
   - Exit code 0 means QA passed. Exit code 1 means QA failed: read `out/<date>/qa.json`, then re-run once
     with `python3 daily.py --date <date>`; if it fails again, stop and report the problems. Never publish a failed mix.
4. **Spot check**: open `out/<date>/brief.md` and confirm the tracklist, duration and theme look sane
   (track count matches `music.track_count`, timestamps increase, duration between 15 and 70 minutes).
5. **Publish**: `./publish.sh <date>`. This commits `days/<date>/` and `history.json`, then creates the
   release `mix-<date>` with the audio. If `gh` is not authenticated, run `gh auth status`, and if it still
   fails, report it rather than skipping the commit.
6. **Report**: send a short message with the theme, duration, track count, release link and the first
   3 tracklist lines. Attach `brief.md`.

If a day is already published (release exists), re-running steps 3 and 5 replaces it.

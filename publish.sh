#!/usr/bin/env bash
# Publish one day's mix: commit the text pack + history, then create a GitHub release with the audio.
# Runs inside GitHub Actions (needs GH_TOKEN with contents: write).
# Usage: ./publish.sh 2026-10-22 [owner/repo]
set -euo pipefail
cd "$(dirname "$0")"
DAY="${1:?usage: publish.sh YYYY-MM-DD [owner/repo]}"
REPO="${2:-ctemenes/Music-gen-daily}"
OUT="out/$DAY"
TAG="mix-$DAY"

[ -f "$OUT/brief.json" ] || { echo "no pack at $OUT"; exit 1; }
python3 -c "import json,sys; q=json.load(open('$OUT/qa.json')); sys.exit(0 if q['pass'] else 1)" \
  || { echo "QA failed for $DAY, not publishing"; exit 1; }

# 1. text pack goes into the repo
mkdir -p "days/$DAY"
cp "$OUT"/{brief.json,brief.md,description.txt,tracklist.txt,plan.json,qa.json} "days/$DAY/"
git config user.name "Digital Aura Racing Team"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add "days/$DAY" history.json
SUMMARY=$(python3 -c "import json; b=json.load(open('$OUT/brief.json')); print(b['theme']['name'], '|', b['music']['track_count'], 'tracks,', b['music']['duration'])")
git commit -q -m "Daily mix $DAY: $SUMMARY" || echo "(nothing new to commit)"
git pull -q --rebase origin main
git push -q origin HEAD:main

# 2. replace any earlier release for this day, then publish the audio
gh release delete "$TAG" -R "$REPO" -y --cleanup-tag >/dev/null 2>&1 || true
TITLE=$(python3 -c "import json; b=json.load(open('$OUT/brief.json')); print(f\"{b['date']} · {b['theme']['name']} · {b['music']['duration']}\")")
gh release create "$TAG" -R "$REPO" --target main --title "$TITLE" --notes-file "$OUT/brief.md" \
  "$OUT/mix.mp3" "$OUT"/short_*.mp3 "$OUT/midi.zip" "$OUT/brief.json" "$OUT/description.txt"
echo "published https://github.com/$REPO/releases/tag/$TAG"

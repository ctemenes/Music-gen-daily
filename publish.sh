#!/usr/bin/env bash
# Publish one day's mix: commit the text pack + history, then create a GitHub release with the audio.
# Uses the GitHub REST API through `gh api` (GraphQL-based gh commands are not used).
# Usage: ./publish.sh 2026-10-22 [owner/repo]
set -euo pipefail
cd "$(dirname "$0")"
DAY="${1:?usage: publish.sh YYYY-MM-DD [owner/repo]}"
REPO="${2:-ctemenes/music-gen-daily}"
OUT="out/$DAY"
TAG="mix-$DAY"

[ -f "$OUT/brief.json" ] || { echo "no pack at $OUT"; exit 1; }
python3 -c "import json,sys; q=json.load(open('$OUT/qa.json')); sys.exit(0 if q['pass'] else 1)" \
  || { echo "QA failed for $DAY, not publishing"; exit 1; }

# 1. text pack goes into the repo
mkdir -p "days/$DAY"
cp "$OUT"/{brief.json,brief.md,description.txt,tracklist.txt,plan.json,qa.json} "days/$DAY/"
git add "days/$DAY" history.json
SUMMARY=$(python3 -c "import json; b=json.load(open('$OUT/brief.json')); print(b['theme']['name'], '|', b['music']['track_count'], 'tracks,', b['music']['duration'])")
git -c user.name="Digital Aura Racing Team" -c user.email="noreply@digitalaurasolutions.com" \
  commit -q -m "Daily mix $DAY: $SUMMARY" || echo "(nothing new to commit)"
git push -q origin HEAD:main

# 2. replace any earlier release for this day
OLD_ID=$(gh api "repos/$REPO/releases/tags/$TAG" --jq .id 2>/dev/null || true)
if [ -n "$OLD_ID" ]; then
  gh api -X DELETE "repos/$REPO/releases/$OLD_ID" >/dev/null
  gh api -X DELETE "repos/$REPO/git/refs/tags/$TAG" >/dev/null 2>&1 || true
fi

# 3. create the release and upload the audio
TITLE=$(python3 -c "import json; b=json.load(open('$OUT/brief.json')); print(f\"{b['date']} · {b['theme']['name']} · {b['music']['duration']}\")")
REL_ID=$(gh api -X POST "repos/$REPO/releases" -f tag_name="$TAG" -f target_commitish=main \
  -f name="$TITLE" -F body=@"$OUT/brief.md" --jq .id)

upload () {
  local f="$1" type="$2"
  gh api -X POST "https://uploads.github.com/repos/$REPO/releases/$REL_ID/assets?name=$(basename "$f")" \
    -H "Content-Type: $type" --input "$f" --jq .browser_download_url
}
upload "$OUT/mix.mp3" audio/mpeg
for s in "$OUT"/short_*.mp3; do upload "$s" audio/mpeg; done
upload "$OUT/midi.zip" application/zip
upload "$OUT/brief.json" application/json
upload "$OUT/description.txt" text/plain
echo "published https://github.com/$REPO/releases/tag/$TAG"

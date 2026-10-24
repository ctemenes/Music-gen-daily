#!/usr/bin/env python3
"""Edit an existing YouTube video's details (needs the youtube.force-ssl scope from tools/get_token.py).

Runs in GitHub Actions. Only the fields given are changed; everything else on the video is kept.
  --title, --description-file (path in the repo), --tags "a, b, c", --privacy, --publish-at (RFC3339),
  --unschedule (clear a scheduled publish time and keep the video private).
Never deletes videos.
"""
import argparse, json, os, sys
from pathlib import Path

import requests

API = "https://www.googleapis.com/youtube/v3/videos"


def token():
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"], "grant_type": "refresh_token"}, timeout=60)
    if r.status_code != 200:
        sys.exit(f"Google token refresh failed ({r.status_code}): {r.json().get('error_description', r.text[:200])}")
    return r.json()["access_token"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video-id", required=True)
    ap.add_argument("--title")
    ap.add_argument("--description-file")
    ap.add_argument("--tags")
    ap.add_argument("--privacy", choices=["private", "unlisted", "public"])
    ap.add_argument("--publish-at")
    ap.add_argument("--unschedule", action="store_true")
    a = ap.parse_args()
    h = {"Authorization": f"Bearer {token()}"}
    r = requests.get(API, params={"part": "snippet,status", "id": a.video_id}, headers=h, timeout=60)
    r.raise_for_status()
    items = r.json().get("items", [])
    if not items:
        sys.exit(f"Video {a.video_id} not found on this channel")
    v = items[0]
    sn, st = v["snippet"], v["status"]
    snippet = {"title": sn["title"], "description": sn.get("description", ""), "tags": sn.get("tags", []),
               "categoryId": sn.get("categoryId", "10")}
    if sn.get("defaultLanguage"):
        snippet["defaultLanguage"] = sn["defaultLanguage"]
    status = {"privacyStatus": st["privacyStatus"], "selfDeclaredMadeForKids": st.get("selfDeclaredMadeForKids", False)}
    if st.get("publishAt"):
        status["publishAt"] = st["publishAt"]
    if a.title:
        snippet["title"] = a.title[:100]
    if a.description_file:
        snippet["description"] = Path(a.description_file).read_text(encoding="utf-8")[:5000]
    if a.tags is not None:
        snippet["tags"] = [t.strip() for t in a.tags.split(",") if t.strip()][:30]
    if a.privacy:
        status["privacyStatus"] = a.privacy
    if a.publish_at:
        status.update(privacyStatus="private", publishAt=a.publish_at)
    if a.unschedule:
        status.pop("publishAt", None)
        status["privacyStatus"] = "private"
    body = {"id": a.video_id, "snippet": snippet, "status": status}
    r = requests.put(API, params={"part": "snippet,status"}, headers=h, json=body, timeout=60)
    if r.status_code != 200:
        if r.status_code == 403 and "insufficient" in r.text.lower():
            sys.exit("The YouTube login can't edit videos yet: re-run tools/get_token.py to add the edit permission.")
        sys.exit(f"Update failed ({r.status_code}): {r.text[:400]}")
    out = r.json()
    print(f"Updated https://youtu.be/{a.video_id}: '{out['snippet']['title']}', {out['status']['privacyStatus']}"
          + (f", goes public {out['status'].get('publishAt')}" if out['status'].get('publishAt') else ""))


if __name__ == "__main__":
    main()

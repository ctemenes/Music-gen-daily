#!/usr/bin/env python3
"""Upload the Cartoon Curiosities queue to YouTube (runs in GitHub Actions on a push to cartoon/queue.json).

Each entry in cartoon/queue.json with no "video_id" is uploaded once, then its id and link are written back.
"publish_at" (RFC3339) keeps a video private until that moment, then YouTube makes it public by itself.
Credentials: CC_YT_CLIENT_ID, CC_YT_CLIENT_SECRET, CC_YT_REFRESH_TOKEN (GitHub secrets, set once with
`python tools/get_token.py client_secret.json --prefix CC_` signed in to the Cartoon Curiosities channel).
Uses the same upload code as the mix videos (tools/render_upload.py). Nothing secret is printed.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"):  # point the shared code at this channel
    os.environ[k] = os.environ.get("CC_" + k, "")

import render_upload as ru  # noqa: E402

QUEUE = HERE / "queue.json"


def fail(q, msg):
    q["last_error"] = msg  # saved by the workflow, so the reason shows up in the repo
    QUEUE.write_text(json.dumps(q, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sys.exit(msg)


def main():
    q = json.loads(QUEUE.read_text(encoding="utf-8"))
    q.pop("last_error", None)
    horizon = datetime.now(timezone.utc) + timedelta(hours=q.get("upload_window_hours", 30))

    def due(v):  # drip-feed: upload only what goes public soon, so a new channel never hits its daily upload limit
        return not v.get("publish_at") or datetime.fromisoformat(v["publish_at"].replace("Z", "+00:00")) <= horizon

    todo = [v for v in q["videos"] if not v.get("video_id") and not v.get("hold") and due(v)]
    thumbs = [v for v in q["videos"] if v.get("thumbnail") and not v.get("thumbnail_set")]
    if not todo and not any(v.get("video_id") for v in thumbs):
        print("Nothing to upload.")
        return
    try:
        token = ru.access_token()
    except SystemExit as e:
        fail(q, str(e))
    want = q.get("expected_channel")
    if want:  # never upload to the wrong channel again
        r = ru.requests.get("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                            headers={"Authorization": f"Bearer {token}"}, timeout=60)
        if r.status_code != 200:
            fail(q, f"Channel check failed ({r.status_code}): {r.text[:300]}")
        got = [c["snippet"]["title"] for c in r.json().get("items", [])]
        if want not in got:
            fail(q, f"Signed in to {got or 'no channel'}, not {want}. Run get_token.py --prefix CC_ again and pick {want}.")
        print(f"Channel check OK: {want}", flush=True)
    links = {v["file"]: v["url"] for v in q["videos"] if v.get("url")}
    for v in todo:
        desc = re.sub(r"\{\{link:([^}]+)\}\}", lambda m: links.get(m.group(1), ""), v["description"])
        status = {"privacyStatus": v.get("privacy", "private"), "selfDeclaredMadeForKids": False,
                  "containsSyntheticMedia": False}
        if v.get("publish_at"):
            status["privacyStatus"] = "private"
            status["publishAt"] = v["publish_at"]
        meta = {"snippet": {"title": v["title"], "description": desc, "tags": v.get("tags", []),
                            "categoryId": v.get("category", "27"), "defaultLanguage": "en", "defaultAudioLanguage": "en"},
                "status": status}
        print(f"Uploading {v['file']} as: {v['title']}", flush=True)
        path = HERE / v["file"]
        if v.get("parts"):  # files over GitHub's 100 MB limit are stored in pieces and joined here
            with open(path, "wb") as out:
                for part in v["parts"]:
                    out.write((HERE / part).read_bytes())
        res = ru.upload(path, meta, token)
        v["video_id"] = res["id"]
        v["url"] = f"https://youtu.be/{res['id']}"
        links[v["file"]] = v["url"]
        QUEUE.write_text(json.dumps(q, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Done: {v['url']} ({status['privacyStatus']}{', public at ' + v['publish_at'] if v.get('publish_at') else ''})",
              flush=True)
    for v in thumbs:  # custom thumbnails, set once the video exists
        if not v.get("video_id"):
            continue
        r = ru.requests.post(f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={v['video_id']}",
                             headers={"Authorization": f"Bearer {token}", "Content-Type": "image/jpeg"},
                             data=(HERE / v["thumbnail"]).read_bytes(), timeout=120)
        if r.status_code == 200:
            v["thumbnail_set"] = True
            print(f"Thumbnail set on {v['video_id']}", flush=True)
        else:
            q["last_error"] = f"Thumbnail not set on {v['video_id']} ({r.status_code}): {r.text[:300]}"
            print(q["last_error"], flush=True)
        QUEUE.write_text(json.dumps(q, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

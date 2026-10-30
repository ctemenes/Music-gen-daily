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
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"):  # point the shared code at this channel
    os.environ[k] = os.environ.get("CC_" + k, "")

import render_upload as ru  # noqa: E402

QUEUE = HERE / "queue.json"


def main():
    q = json.loads(QUEUE.read_text(encoding="utf-8"))
    todo = [v for v in q["videos"] if not v.get("video_id") and not v.get("hold")]
    if not todo:
        print("Nothing to upload.")
        return
    token = ru.access_token()
    want = q.get("expected_channel")
    if want:  # never upload to the wrong channel again
        r = ru.requests.get("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                            headers={"Authorization": f"Bearer {token}"}, timeout=60)
        got = [c["snippet"]["title"] for c in r.json().get("items", [])]
        if want not in got:
            sys.exit(f"Signed in to {got or 'no channel'}, not {want}. Run get_token.py --prefix CC_ again and pick {want}.")
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
        res = ru.upload(HERE / v["file"], meta, token)
        v["video_id"] = res["id"]
        v["url"] = f"https://youtu.be/{res['id']}"
        links[v["file"]] = v["url"]
        QUEUE.write_text(json.dumps(q, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Done: {v['url']} ({status['privacyStatus']}{', public at ' + v['publish_at'] if v.get('publish_at') else ''})",
              flush=True)


if __name__ == "__main__":
    main()

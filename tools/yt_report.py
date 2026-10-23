#!/usr/bin/env python3
"""Pull a YouTube Studio performance report for the channel (Data API + Analytics API).

Runs in GitHub Actions with the same OAuth secrets as the upload (scopes include youtube.readonly and
yt-analytics.readonly). Writes reports/analytics/<date>.json and a short <date>.md summary.

Studio's Reach tab (impressions and click-through rate) is not exposed by the public API, so it is not here.
Analytics data lags YouTube by roughly 1 to 3 days.
"""
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = "https://www.googleapis.com/youtube/v3"
ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"


def token() -> str:
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"], "grant_type": "refresh_token"}, timeout=60)
    if r.status_code != 200:
        sys.exit(f"token refresh failed ({r.status_code}): {r.text[:300]}")
    return r.json()["access_token"]


def get(url, tok, **params):
    r = requests.get(url, params=params, headers={"Authorization": f"Bearer {tok}"}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"{r.status_code}: {r.text[:400]}")
    return r.json()


def report(tok, start, end, metrics, dimensions=None, filters=None, sort=None, max_results=None):
    p = {"ids": "channel==MINE", "startDate": start, "endDate": end, "metrics": ",".join(metrics)}
    if dimensions:
        p["dimensions"] = ",".join(dimensions)
    if filters:
        p["filters"] = filters
    if sort:
        p["sort"] = sort
    if max_results:
        p["maxResults"] = max_results
    try:
        j = get(ANALYTICS, tok, **p)
    except RuntimeError as e:
        return {"error": str(e)}
    cols = [h["name"] for h in j.get("columnHeaders", [])]
    return [dict(zip(cols, row)) for row in j.get("rows", [])]


def iso_dur(s):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return None
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def main():
    tok = token()
    today = dt.date.today()
    end = today.isoformat()
    start = (today - dt.timedelta(days=int(os.environ.get("DAYS", "90")))).isoformat()
    out = {"pulled_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z", "start": start, "end": end}

    ch = get(f"{DATA}/channels", tok, part="snippet,statistics,contentDetails", mine="true")["items"][0]
    out["channel"] = {"id": ch["id"], "title": ch["snippet"]["title"], "handle": ch["snippet"].get("customUrl"),
                      **{k: int(v) for k, v in ch["statistics"].items() if v.isdigit()}}
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]

    ids, page = [], None
    while True:
        j = get(f"{DATA}/playlistItems", tok, part="contentDetails", playlistId=uploads, maxResults=50,
                **({"pageToken": page} if page else {}))
        ids += [i["contentDetails"]["videoId"] for i in j["items"]]
        page = j.get("nextPageToken")
        if not page:
            break

    videos = []
    for i in range(0, len(ids), 50):
        j = get(f"{DATA}/videos", tok, part="snippet,statistics,contentDetails,status", id=",".join(ids[i:i + 50]))
        for v in j["items"]:
            st = v.get("statistics", {})
            videos.append({
                "id": v["id"], "title": v["snippet"]["title"], "published": v["snippet"]["publishedAt"],
                "privacy": v["status"]["privacyStatus"], "publish_at": v["status"].get("publishAt"),
                "duration_s": iso_dur(v["contentDetails"]["duration"]),
                "views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                "comments": int(st.get("commentCount", 0)), "tags": v["snippet"].get("tags", [])[:15]})
    videos.sort(key=lambda x: x["published"], reverse=True)
    out["videos"] = videos

    base = ["views", "engagedViews", "estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage",
            "likes", "comments", "shares", "subscribersGained", "subscribersLost"]
    per = report(tok, start, end, base, ["video"], sort="-views", max_results=200)
    if isinstance(per, dict):  # engagedViews not supported here on older API versions: retry without it
        per = report(tok, start, end, [m for m in base if m != "engagedViews"], ["video"], sort="-views",
                     max_results=200)
    out["per_video"] = per
    out["daily"] = report(tok, start, end, ["views", "estimatedMinutesWatched", "subscribersGained"], ["day"],
                          sort="day")
    out["traffic_sources"] = report(tok, start, end, ["views", "estimatedMinutesWatched", "averageViewDuration"],
                                    ["insightTrafficSourceType"], sort="-views")
    out["search_terms"] = report(tok, start, end, ["views", "estimatedMinutesWatched"],
                                 ["insightTrafficSourceDetail"], filters="insightTrafficSourceType==YT_SEARCH",
                                 sort="-views", max_results=25)
    out["suggested_from"] = report(tok, start, end, ["views", "estimatedMinutesWatched"],
                                   ["insightTrafficSourceDetail"], filters="insightTrafficSourceType==RELATED_VIDEO",
                                   sort="-views", max_results=25)
    out["external"] = report(tok, start, end, ["views"], ["insightTrafficSourceDetail"],
                             filters="insightTrafficSourceType==EXT_URL", sort="-views", max_results=25)
    out["countries"] = report(tok, start, end, ["views", "estimatedMinutesWatched", "averageViewDuration"],
                              ["country"], sort="-views", max_results=15)
    out["devices"] = report(tok, start, end, ["views", "estimatedMinutesWatched", "averageViewDuration"],
                            ["deviceType"], sort="-views")
    out["playback_locations"] = report(tok, start, end, ["views", "estimatedMinutesWatched"],
                                       ["insightPlaybackLocationType"], sort="-views")
    out["subscribed_status"] = report(tok, start, end, ["views", "averageViewDuration"], ["subscribedStatus"])
    out["demographics"] = report(tok, start, end, ["viewerPercentage"], ["ageGroup", "gender"])
    out["content_type"] = report(tok, start, end, ["views", "estimatedMinutesWatched"], ["creatorContentType"])

    out["retention"], out["video_traffic"] = {}, {}
    for v in videos[:15]:
        if v["views"] < 10:
            continue
        f = f"video=={v['id']}"
        out["retention"][v["id"]] = report(tok, start, end, ["audienceWatchRatio", "relativeRetentionPerformance"],
                                           ["elapsedVideoTimeRatio"], filters=f, max_results=100)
        out["video_traffic"][v["id"]] = report(tok, start, end, ["views", "averageViewDuration"],
                                               ["insightTrafficSourceType"], filters=f, sort="-views")

    d = Path(os.environ.get("OUT_DIR") or ROOT / "reports" / "analytics")
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{end}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    (d / "latest.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))

    c = out["channel"]
    lines = [f"# YouTube report {end}", "", f"Channel: {c['title']} ({c.get('handle')}), "
             f"{c.get('subscriberCount', '?')} subscribers, {c.get('viewCount', '?')} views, "
             f"{c.get('videoCount', '?')} videos. Analytics window {start} to {end}.", "",
             "| Published | Title | Privacy | Length | Views | Likes | Comments |", "|---|---|---|---|---|---|---|"]
    for v in videos:
        ln = f"{(v['duration_s'] or 0) // 60}m"
        lines.append(f"| {v['published'][:10]} | {v['title']} | {v['privacy']} | {ln} | {v['views']} | "
                     f"{v['likes']} | {v['comments']} |")
    (d / f"{end}.md").write_text("\n".join(lines) + "\n")
    print(f"report written for {len(videos)} videos")


if __name__ == "__main__":
    main()

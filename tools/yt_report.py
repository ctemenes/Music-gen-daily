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
        raise RuntimeError(f"token refresh failed ({r.status_code}): {r.text[:300]}")
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


REPORTING = "https://youtubereporting.googleapis.com/v1"


def reach(tok):
    """Thumbnail impressions and CTR come only from the YouTube Reporting API (bulk daily CSVs).

    Makes sure a reach reporting job exists, then downloads every report it has produced so far.
    YouTube fills a new job within about 48 hours, with some backfill of earlier days.
    """
    res = {}
    try:
        types = get(f"{REPORTING}/reportTypes", tok).get("reportTypes", [])
    except RuntimeError as e:
        return {"error": str(e)}
    res["available_types"] = [t["id"] for t in types if "reach" in t["id"]]
    want = [t for t in res["available_types"] if "basic" in t] or res["available_types"][:1]
    if not want:
        res["error"] = "no reach report type offered for this channel"
        return res
    try:
        jobs = get(f"{REPORTING}/jobs", tok).get("jobs", [])
    except RuntimeError as e:
        return {**res, "error": str(e)}
    job = next((j for j in jobs if j["reportTypeId"] == want[0]), None)
    if not job:
        r = requests.post(f"{REPORTING}/jobs", headers={"Authorization": f"Bearer {tok}"},
                          json={"reportTypeId": want[0], "name": "music-gen-daily reach"}, timeout=60)
        if r.status_code != 200:
            return {**res, "error": f"create job {r.status_code}: {r.text[:300]}"}
        job = r.json()
        res["job_created"] = job.get("createTime")
    res["job"] = {"id": job["id"], "type": job["reportTypeId"], "created": job.get("createTime")}
    try:
        reps = get(f"{REPORTING}/jobs/{job['id']}/reports", tok).get("reports", [])
    except RuntimeError as e:
        return {**res, "error": str(e)}
    res["reports"] = []
    rows = []
    for rep in sorted(reps, key=lambda x: x["startTime"]):
        r = requests.get(rep["downloadUrl"], headers={"Authorization": f"Bearer {tok}"}, timeout=120)
        res["reports"].append({"start": rep["startTime"], "end": rep["endTime"], "status": r.status_code})
        if r.status_code == 200:
            lines = r.text.strip().splitlines()
            head = lines[0].split(",")
            rows += [dict(zip(head, ln.split(","))) for ln in lines[1:]]
    # roll up per video: impressions and impression-weighted CTR
    per = {}
    for row in rows:
        vid = row.get("video_id")
        imp = float(row.get("video_thumbnail_impressions") or 0)
        ctr = float(row.get("video_thumbnail_impressions_ctr") or 0)
        p = per.setdefault(vid, {"impressions": 0.0, "clicks": 0.0, "days": set()})
        p["impressions"] += imp
        p["clicks"] += imp * ctr
        p["days"].add(row.get("date"))
    res["per_video"] = {k: {"impressions": int(v["impressions"]),
                            "ctr": round(v["clicks"] / v["impressions"], 4) if v["impressions"] else None,
                            "days": sorted(x for x in v["days"] if x)} for k, v in per.items()}
    res["rows"] = rows[:3000]
    return res


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
                      **{k: int(v) for k, v in ch["statistics"].items() if str(v).isdigit()}}
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

    # comments on recent public videos (what viewers say)
    out["comments"] = {}
    for v in videos[:15]:
        if v["comments"] and v["privacy"] == "public":
            try:
                j = get(f"{DATA}/commentThreads", tok, part="snippet,replies", videoId=v["id"], maxResults=50,
                        order="time")
                out["comments"][v["id"]] = [{
                    "text": t["snippet"]["topLevelComment"]["snippet"]["textOriginal"][:500],
                    "likes": t["snippet"]["topLevelComment"]["snippet"]["likeCount"],
                    "at": t["snippet"]["topLevelComment"]["snippet"]["publishedAt"],
                    "replies": t["snippet"]["totalReplyCount"]} for t in j.get("items", [])]
            except RuntimeError as e:
                out["comments"][v["id"]] = {"error": str(e)}

    out["reach"] = reach(tok)

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
    try:
        main()
    except BaseException as e:  # noqa: BLE001  keep the error inside the sealed report (logs aren't readable)
        import traceback
        d = Path(os.environ.get("OUT_DIR") or ROOT / "reports" / "analytics")
        d.mkdir(parents=True, exist_ok=True)
        (d / "error.json").write_text(json.dumps({"error": repr(e), "trace": traceback.format_exc()}))
        print("report failed, error saved")

#!/usr/bin/env python3
"""Cut vertical YouTube Shorts from a released mix and upload them.

For days/<date>:
1. Reads youtube.json (full video id, loop clip, speed, grain) and the release's brief.json + short_N.mp3
   (45s audio cuts around the drops, made by daily.py).
2. Renders each short at 1080x1920: the loop clip letterboxed in the middle over a blurred, darkened copy of
   itself, the hook line at the top, the track name and "full mix on the channel" at the bottom, film grain.
3. Uploads them as Shorts (vertical, under 60s, #shorts) with a link back to the full mix, optionally
   scheduled one after another, and writes days/<date>/shorts.json.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from render_upload import REPO, access_token, download, find_font, sh, upload  # noqa: E402

W, H = 1080, 1920
BOLD = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]


def bold_font():
    for f in BOLD:
        if Path(f).exists():
            return f
    return find_font()


def wrap(text, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def esc(s):
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")


def render_short(work, loop, audio, out, hook, track, seconds, speed, grain, font, layout="full", text=True):
    """layout 'full': the loop scaled to 1920 high and centre-cropped to 9:16 (fills the phone).
    layout 'box': the loop letterboxed over a blurred copy of itself. text=False drops the hook line."""
    hook_file = work / "hook.txt"
    hook_file.write_text(wrap(hook, 22), encoding="utf-8")
    txt = (f",drawtext=fontfile='{font}':textfile='{hook_file}':fontsize=64:fontcolor=white:line_spacing=10:"
           f"x=(w-text_w)/2:y=260:shadowcolor=black@0.7:shadowx=3:shadowy=3") if text else ""
    noise = f",noise=alls={int(grain)}:allf=t+u" if grain else ""
    if layout == "full":
        fc = (f"[0:v]setpts=PTS/{speed},scale=-2:{H}:flags=lanczos,crop={W}:{H},format=yuv420p{txt}{noise}[v]")
    else:
        fg_h = int(round(W * 9 / 16 / 2)) * 2  # 608
        fc = (f"[0:v]setpts=PTS/{speed},split[a][b];"
              f"[a]scale={W}:{fg_h}:flags=lanczos[fg];"
              f"[b]scale=-2:{H},crop={W}:{H},gblur=sigma=28,eq=brightness=-0.25:saturation=0.8[bg];"
              f"[bg][fg]overlay=0:(H-h)/2,format=yuv420p{txt}{noise}[v]")
    sh("ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", loop, "-i", audio,
       "-filter_complex", fc, "-map", "[v]", "-map", "1:a", "-t", seconds, "-r", "30",
       "-c:v", "libx264", "-preset", "medium", "-crf", "22", "-maxrate", "14M", "-bufsize", "28M", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out)


def previews(work, outs, day):
    """Small contact sheet + 360p copy of each short, committed under days/<date>/shorts_preview/ so they can
    be checked from the chat (run artifacts are not reachable from there)."""
    pdir = day / "shorts_preview"
    pdir.mkdir(exist_ok=True)
    for k, _meta, _hook, out in outs:
        sh("ffmpeg", "-y", "-loglevel", "error", "-i", out, "-vf", "select='not(mod(n\\,270))',scale=216:-1,tile=5x1",
           "-frames:v", "1", "-q:v", "5", pdir / f"short_{k}_sheet.jpg")
        sh("ffmpeg", "-y", "-loglevel", "error", "-i", out, "-vf", "scale=360:640", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "30", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", pdir / f"short_{k}_360p.mp4")


def tracklist(desc):
    """[(seconds, name)] from the TRACKLIST block of a description."""
    out, on = [], False
    for ln in desc.splitlines():
        if ln.strip().upper() == "TRACKLIST":
            on = True
            continue
        if on:
            if not ln.strip() or ln.startswith("━"):
                break
            ts, _, name = ln.strip().partition(" ")
            parts = [int(p) for p in ts.split(":")]
            secs = parts[-1] + 60 * parts[-2] + (3600 * parts[-3] if len(parts) > 2 else 0)
            out.append((secs, name.strip()))
    return out


def loudest_cuts(mix, tracks, seconds, work, count):
    """Cut the loudest `seconds` window of each track straight from the mix (for mixes released without short_N.mp3).
    Returns [{"file", "track", "start"}]."""
    import struct
    pcm = work / "mix_mono.raw"
    sh("ffmpeg", "-y", "-loglevel", "error", "-i", mix, "-ac", "1", "-ar", "8000", "-f", "s16le", pcm)
    data = pcm.read_bytes()
    n = len(data) // 2
    samples = struct.unpack(f"<{n}h", data)
    sr = 8000
    secs = n // sr
    rms = [sum(s * s for s in samples[i * sr:(i + 1) * sr]) / sr for i in range(secs)]
    total = secs
    out = []
    for k, (start, name) in enumerate(tracks[:count], 1):
        end = tracks[k][0] if k < len(tracks) else total
        lo, hi = int(start) + 5, int(end) - int(seconds) - 2
        if hi <= lo:
            lo, hi = int(start), max(int(start), int(end) - int(seconds))
        best, best_t = -1, lo
        for t in range(lo, hi + 1):
            v = sum(rms[t:t + int(seconds)])
            if v > best:
                best, best_t = v, t
        f = work / f"short_{k}.mp3"
        sh("ffmpeg", "-y", "-loglevel", "error", "-ss", str(best_t), "-t", str(seconds), "-i", mix,
           "-af", "afade=t=in:d=0.3,afade=t=out:st=%.2f:d=1.5" % (seconds - 1.5), "-c:a", "libmp3lame", "-b:a", "320k", f)
        out.append({"file": f.name, "track": name, "start": best_t})
        print(f"short {k}: {name} from {best_t // 60}:{best_t % 60:02d}", flush=True)
    return out


def unschedule(date, token):
    """Pull a batch back: every short in days/<date>/shorts.json goes private with no publish time and an [old] title
    prefix, so the duplicates can be deleted from Studio."""
    day = Path("days") / date
    sj = json.loads((day / "shorts.json").read_text(encoding="utf-8"))
    for s in sj["shorts"]:
        r = requests.get("https://www.googleapis.com/youtube/v3/videos", params={"part": "snippet", "id": s["video_id"]},
                         headers={"Authorization": f"Bearer {token}"}, timeout=60).json()
        if not r.get("items"):
            print(f"{s['video_id']}: not found (already deleted?)", flush=True)
            continue
        sn = r["items"][0]["snippet"]
        body = {"id": s["video_id"], "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False},
                "snippet": {"title": ("[old] " + sn["title"])[:100], "categoryId": sn.get("categoryId", "10"),
                            "description": sn.get("description", ""), "tags": sn.get("tags", [])}}
        u = requests.put("https://www.googleapis.com/youtube/v3/videos", params={"part": "snippet,status"}, json=body,
                         headers={"Authorization": f"Bearer {token}"}, timeout=60)
        print(f"{s['url']}: {'unscheduled, private' if u.status_code == 200 else 'failed ' + u.text[:200]}", flush=True)
        s["privacy"], s["publish_at"], s["unscheduled"] = "private", None, True
    (day / "shorts.json").write_text(json.dumps(sj, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="day whose loop clip is used (and whose shorts audio, unless --audio-date)")
    ap.add_argument("--audio-date", default="", help="take the audio and full-mix link from this day's mix instead")
    ap.add_argument("--unschedule", action="store_true", help="pull this day's shorts back to private, no render")
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--seconds", type=float, default=45)
    ap.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    ap.add_argument("--publish-from", default="", help="RFC3339 time for the first short; the rest follow at --spacing-hours")
    ap.add_argument("--spacing-hours", type=float, default=24)
    ap.add_argument("--hooks", default="", help="JSON list of hook lines, one per short (default: the mix title)")
    ap.add_argument("--speed", type=float, default=None, help="loop speed-up (default: what the full video used)")
    ap.add_argument("--grain", type=int, default=None)
    ap.add_argument("--synthetic", default="false")
    ap.add_argument("--layout", default="full", choices=["full", "box"])
    ap.add_argument("--text", default="true", help="false = no hook text on the video")
    ap.add_argument("--render-only", action="store_true")
    a = ap.parse_args()

    if a.unschedule:
        return unschedule(a.date, access_token())
    vday = Path("days") / a.date  # visuals
    vyt = json.loads((vday / "youtube.json").read_text(encoding="utf-8"))
    day = Path("days") / (a.audio_date or a.date)  # audio, link, outputs
    yt = json.loads((day / "youtube.json").read_text(encoding="utf-8"))
    brief = json.loads((day / "brief.json").read_text(encoding="utf-8")) if (day / "brief.json").exists() else {}
    work = Path("out") / f"shorts-{day.name}"
    work.mkdir(parents=True, exist_ok=True)
    rel = f"https://github.com/{REPO}/releases/download/mix-{day.name}"
    shorts_meta = []
    try:
        rbrief = work / "release_brief.json"
        download(f"{rel}/brief.json", rbrief)
        rb = json.loads(rbrief.read_text(encoding="utf-8"))
        shorts_meta = (rb.get("audio") or {}).get("shorts") or rb.get("shorts") or []
        for meta in shorts_meta[:a.count]:
            download(f"{rel}/{meta['file']}", work / meta["file"])
    except Exception as e:
        print(f"No release short cuts for {day.name} ({type(e).__name__}); cutting from the mix", flush=True)
    if not shorts_meta:
        mix = day / "mix.mp3"
        if not mix.exists():
            mix = work / "mix.mp3"
            download(f"{rel}/mix.mp3", mix)
        desc_full = (day / "description.txt").read_text(encoding="utf-8") if (day / "description.txt").exists() else ""
        tracks = tracklist(desc_full) or [(0, yt["title"].split("|")[0].strip())]
        shorts_meta = loudest_cuts(mix, tracks, a.seconds, work, a.count)
    loop = work / "loop_src.mp4"
    clips = [vyt["loop_url"], *(vyt.get("clips") or [])]
    pick = 0
    if vyt.get("pattern") and len(clips) > 1:  # the clip the pattern uses most is the main shot
        from collections import Counter
        c = Counter()
        for tok in vyt["pattern"].split(","):
            i, _, n = tok.partition("x")
            c[int(i) - 1] += int(n or 1)
        pick = c.most_common(1)[0][0]
    download(clips[pick], loop)
    speed = a.speed if a.speed is not None else float(vyt.get("speed") or 1.0)
    grain = a.grain if a.grain is not None else int(vyt.get("grain") or 14)
    font = bold_font()
    hooks = json.loads(a.hooks) if a.hooks.strip() else []
    base_title = yt["title"].split("(")[0].split("|")[0].strip()

    outs = []
    for k, meta in enumerate(shorts_meta[:a.count], 1):
        audio = work / meta["file"]
        hook = hooks[k - 1] if k - 1 < len(hooks) else (base_title if k == 1 else meta["track"])  # never three identical titles
        out = work / f"short_{k}.mp4"
        t0 = time.time()
        render_short(work, loop, audio, out, hook, meta["track"], a.seconds, speed, grain, font, a.layout, a.text.lower() != "false")
        print(f"Rendered {out.name} in {time.time() - t0:.0f}s", flush=True)
        outs.append((k, meta, hook, out))
    previews(work, outs, day)
    if a.render_only:
        return

    tags = list((brief.get("youtube") or {}).get("tags") or [])[:20] + ["shorts", "dnb shorts", "y2k dnb"]
    token = access_token()
    results = []
    for k, meta, hook, out in outs:
        title = f"{hook} #shorts"
        if len(title) > 100:
            title = hook[:92].rstrip() + " #shorts"
        desc = (f"full mix: {yt['url']}\n\n{meta['track']} from \"{yt['title']}\"\n\n"
                f"Y2K drum & bass in the spirit of Rollcage Stage II and the PS1 era.\n\n#shorts #dnb #y2k #drumandbass")
        status = {"privacyStatus": a.privacy, "selfDeclaredMadeForKids": False,
                  "containsSyntheticMedia": a.synthetic.lower() == "true"}
        if a.publish_from:
            from datetime import datetime, timedelta, timezone
            t = datetime.fromisoformat(a.publish_from.replace("Z", "+00:00")).astimezone(timezone.utc)
            t += timedelta(hours=a.spacing_hours * (k - 1))
            status.update(privacyStatus="private", publishAt=t.strftime("%Y-%m-%dT%H:%M:%SZ"))
        meta_yt = {"snippet": {"title": title, "description": desc, "tags": tags[:30], "categoryId": "10"}, "status": status}
        res = upload(out, meta_yt, token)
        vid = res["id"]
        print(f"Uploaded short {k}: https://youtube.com/shorts/{vid} ({res['status']['privacyStatus']})", flush=True)
        results.append({"short": k, "video_id": vid, "url": f"https://youtube.com/shorts/{vid}", "title": title,
                        "track": meta["track"], "publish_at": status.get("publishAt"), "privacy": res["status"]["privacyStatus"]})
    (day / "shorts.json").write_text(json.dumps({"full_video": yt["url"], "loop_from": a.date, "shorts": results}, indent=2), encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            for r in results:
                f.write(f"Short {r['short']}: {r['url']} ({r['privacy']}, {r['publish_at'] or 'now'})\n")


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        if e.code not in (0, None):
            print(f"::error::{e.code}", flush=True)
        raise
    except Exception as e:  # surface the reason as a run annotation (logs are not readable from the agent)
        import traceback
        print(f"::error::{type(e).__name__}: {str(e)[:600]} | {traceback.format_exc().splitlines()[-3:]}", flush=True)
        raise

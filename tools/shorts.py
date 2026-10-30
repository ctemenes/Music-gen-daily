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


def render_short(work, loop, audio, out, hook, track, seconds, speed, grain, font):
    hook_file, track_file = work / "hook.txt", work / "track.txt"
    hook_file.write_text(wrap(hook, 22), encoding="utf-8")
    track_file.write_text("full mix on the channel", encoding="utf-8")
    fg_h = int(round(W * 9 / 16 / 2)) * 2  # 608
    txt = (f"drawtext=fontfile='{font}':textfile='{hook_file}':fontsize=64:fontcolor=white:line_spacing=10:"
           f"x=(w-text_w)/2:y=300:shadowcolor=black@0.7:shadowx=3:shadowy=3,"
           f"drawtext=fontfile='{font}':textfile='{track_file}':fontsize=40:fontcolor=white@0.9:line_spacing=12:"
           f"x=(w-text_w)/2:y=h-420:shadowcolor=black@0.7:shadowx=2:shadowy=2")
    noise = f",noise=alls={int(grain)}:allf=t+u" if grain else ""
    fc = (f"[0:v]setpts=PTS/{speed},split[a][b];"
          f"[a]scale={W}:{fg_h}:flags=lanczos[fg];"
          f"[b]scale=-2:{H},crop={W}:{H},gblur=sigma=28,eq=brightness=-0.25:saturation=0.8[bg];"
          f"[bg][fg]overlay=0:(H-h)/2,format=yuv420p,{txt}{noise}[v]")
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--seconds", type=float, default=45)
    ap.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    ap.add_argument("--publish-from", default="", help="RFC3339 time for the first short; the rest follow at --spacing-hours")
    ap.add_argument("--spacing-hours", type=float, default=24)
    ap.add_argument("--hooks", default="", help="JSON list of hook lines, one per short (default: the mix title)")
    ap.add_argument("--speed", type=float, default=None, help="loop speed-up (default: what the full video used)")
    ap.add_argument("--grain", type=int, default=None)
    ap.add_argument("--synthetic", default="false")
    ap.add_argument("--render-only", action="store_true")
    a = ap.parse_args()

    day = Path("days") / a.date
    yt = json.loads((day / "youtube.json").read_text(encoding="utf-8"))
    brief = json.loads((day / "brief.json").read_text(encoding="utf-8")) if (day / "brief.json").exists() else {}
    work = Path("out") / f"shorts-{a.date}"
    work.mkdir(parents=True, exist_ok=True)
    rel = f"https://github.com/{REPO}/releases/download/mix-{a.date}"
    rbrief = work / "release_brief.json"
    download(f"{rel}/brief.json", rbrief)
    rb = json.loads(rbrief.read_text(encoding="utf-8"))
    shorts_meta = (rb.get("audio") or {}).get("shorts") or rb.get("shorts") or []
    if not shorts_meta:
        sys.exit("This release has no short_N.mp3 cuts; make them first.")
    loop = work / "loop_src.mp4"
    download(yt["loop_url"], loop)
    speed = a.speed if a.speed is not None else float(yt.get("speed") or 1.0)
    grain = a.grain if a.grain is not None else int(yt.get("grain") or 14)
    font = bold_font()
    hooks = json.loads(a.hooks) if a.hooks.strip() else []
    base_title = yt["title"].split("(")[0].split("|")[0].strip()

    outs = []
    for k, meta in enumerate(shorts_meta[:a.count], 1):
        audio = work / meta["file"]
        download(f"{rel}/{meta['file']}", audio)
        hook = hooks[k - 1] if k - 1 < len(hooks) else base_title
        out = work / f"short_{k}.mp4"
        t0 = time.time()
        render_short(work, loop, audio, out, hook, meta["track"], a.seconds, speed, grain, font)
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
        desc = (f"{meta['track']} from \"{yt['title']}\"\n\nfull mix: {yt['url']}\n\n"
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
    (day / "shorts.json").write_text(json.dumps({"full_video": yt["url"], "shorts": results}, indent=2), encoding="utf-8")
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

#!/usr/bin/env python3
"""Render a day's mix video from a short seamless loop clip and upload it to YouTube.

  python3 tools/render_upload.py --date 2026-10-21 --loop-url https://.../loop.mp4 --title "..." [--privacy private]

1. Downloads mix.mp3 from the day's release and the loop clip (first frame = last frame).
2. Re-encodes the clip once to 1920x1080: fixes the loop seam with a short crossfade, speeds it up (default 2.5x,
   since video models render cars too slowly) and outputs up to 60 fps.
3. Copies that clip end to end under the full mix (no second encode), so rendering takes about a minute.
4. Uploads to YouTube with the day's description and tags (resumable upload), then writes days/<date>/youtube.json.

YouTube credentials come from env: YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN (GitHub secrets). Never printed.
--render-only skips the upload (for testing). --thumb-url sets a thumbnail after upload.
--video-id with --thumb-url only sets the thumbnail on an existing video.
"""
import argparse, json, os, re, subprocess, sys, time, urllib.request
from pathlib import Path

import requests

REPO = os.environ.get("GITHUB_REPOSITORY", "ctemenes/Music-gen-daily")


def sh(*cmd):
    print("+", " ".join(str(c) for c in cmd)[:300], flush=True)
    subprocess.run([str(c) for c in cmd], check=True)


def probe(path, entries):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                          entries, "-of", "json", str(path)], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def download(url, dest):
    print(f"Downloading {url.split('?')[0][-80:]}", flush=True)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "music-gen-daily"}), timeout=600) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)


def shake_filter(px, seg_len):
    """Handheld camera wobble of about px pixels. Every term completes a whole number of cycles over
    seg_len, so the wobble is back at rest at the end and the cut or loop point stays seamless."""
    if not px:
        return ""
    a, D = float(px), seg_len

    def k(hz):  # cycles per segment closest to the wanted frequency
        return max(1, round(hz * D))

    def term(amp, hz, phase=0.0):
        return f"+{amp:.2f}*sin(2*PI*t*{k(hz)}/{D:.6f}+{phase})"

    x = "(iw-1920)/2" + term(a, .2) + term(a / 2, .47, 1.3) + term(a / 5, 2.1) + term(a / 7, 3.1, .4)
    y = "(ih-1080)/2" + term(a * .8, .33, .7) + term(a / 3, .71) + term(a / 5, 2.5, 2.1) + term(a / 7, 3.4)
    m = 1 + 4 * a / 1080  # zoom in just enough that the moving crop never shows an edge
    return f",scale=ceil(1920*{m:.4f}/2)*2:ceil(1080*{m:.4f}/2)*2,crop=1920:1080:x='{x}':y='{y}'"


def parse_pattern(pattern, n_clips):
    """'1x4,2,1x5,2' -> [1,1,1,1,2,1,1,1,1,1,2]"""
    order = []
    for part in pattern.replace(" ", "").split(","):
        if not part:
            continue
        clip, _, times = part.replace("*", "x").partition("x")
        c, t = int(clip), int(times or 1)
        if not 1 <= c <= n_clips:
            sys.exit(f"pattern uses clip {c} but only {n_clips} clips were given")
        order += [c] * t
    return order


def build_sequence(work, clips, pattern, mbps, speed, grain, shake):
    """Several clips that all start and end on the same frame, cut together in a pattern (e.g. shot 1 four
    times, shot 2, shot 1 five times...). Each clip is encoded once with its first frame dropped (it repeats
    the previous clip's last frame), then the pattern is joined by stream copy into one loop segment."""
    order = parse_pattern(pattern, len(clips))
    base = "scale=1920:1080:flags=lanczos,setsar=1"
    if grain:
        base += f",gblur=sigma=0.6,noise=alls={int(grain)}:allf=t+u,eq=saturation=0.93"
    parts = []
    for i, src in enumerate(clips, 1):
        s = probe(src, "stream=nb_read_packets,r_frame_rate")["streams"][0]
        frames = int(s["nb_read_packets"])
        num, den = (int(x) for x in s["r_frame_rate"].split("/"))
        fps = num / den
        out_fps = min(60.0, fps * speed)
        seg_len = (frames - 1) / fps / speed
        graph = (f"[0:v]trim=start_frame=1:end_frame={frames},setpts=(N/{fps}/TB)/{speed},{base}"
                 f"{shake_filter(shake, seg_len)}[v]")
        dst = work / f"clip{i}_1080p.mp4"
        sh("ffmpeg", "-y", "-v", "error", "-i", src, "-an", "-filter_complex", graph, "-map", "[v]",
           "-c:v", "libx264", "-preset", "slow", "-profile:v", "high", "-pix_fmt", "yuv420p",
           "-b:v", f"{mbps}M", "-maxrate", f"{mbps * 1.5}M", "-bufsize", f"{mbps * 3}M",
           "-x264-params", f"keyint={int(out_fps * 2)}:min-keyint={int(out_fps * 2)}:scenecut=0:open-gop=0",
           "-r", f"{out_fps:g}", dst)
        parts.append(dst)
    lst = work / "sequence.txt"
    lst.write_text("".join(f"file '{parts[c - 1].resolve()}'\n" for c in order))
    seq = work / "sequence_1080p.mp4"
    sh("ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", seq)
    print(f"Sequence: {len(order)} shots, pattern {pattern}", flush=True)
    return seq


def render(work, loop_src, mix, out, mbps, speed=1.0, xfade=0.0, grain=0, shake=0, clips=None, pattern=""):
    s = probe(loop_src, "stream=nb_read_packets,r_frame_rate")["streams"][0]
    frames = int(s["nb_read_packets"])
    num, den = (int(x) for x in s["r_frame_rate"].split("/"))
    src_fps = num / den
    out_fps = min(60.0, src_fps * speed)
    if mbps is None:
        mbps = 8.0 if (out_fps > 30 or grain) else 5.0
    seg = work / "loop_1080p.mp4"
    if pattern:
        seg = build_sequence(work, [loop_src, *(clips or [])], pattern, mbps, speed, grain, shake)
        xfade = 0
    base = "scale=1920:1080:flags=lanczos,setsar=1"
    if grain:
        # 2001 camera look: slight softness, moving film grain, a touch less saturation
        base += f",gblur=sigma=0.6,noise=alls={int(grain)}:allf=t+u,eq=saturation=0.93"
    if xfade > 0:
        # Seam fix: start the loop xfade seconds in, and fade its tail into the opening frames, so the last
        # frame flows straight into the first even when the model's end frame is not an exact match.
        x = max(1, round(xfade * src_fps))
        if frames < 3 * x:
            sys.exit(f"Loop clip too short ({frames} frames) for a {xfade}s seam crossfade.")
        offset = (frames - 2 * x) / src_fps
        seg_len = (frames - x) / src_fps / speed
        graph = (f"[0:v]{base},split[s1][s2];"
                 f"[s1]trim=start_frame={x}:end_frame={frames},setpts=N/{src_fps}/TB[a];"
                 f"[s2]trim=start_frame=0:end_frame={x},setpts=N/{src_fps}/TB[b];"
                 f"[a][b]xfade=transition=fade:duration={x / src_fps:.6f}:offset={offset:.6f},setpts=PTS/{speed}"
                 f"{shake_filter(shake, seg_len)}[v]")
        frame_args = []
    else:
        # Plain loop: drop the duplicate last frame so the seam does not stutter.
        seg_len = (frames - 1) / src_fps / speed
        graph = f"[0:v]{base},setpts=PTS/{speed}{shake_filter(shake, seg_len)}[v]"
        frame_args = ["-frames:v", frames - 1]
    # One encode of the short clip: exact 1080p, sped up, closed GOP so copies join cleanly.
    if not pattern:
        sh("ffmpeg", "-y", "-v", "error", "-i", loop_src, "-an", "-filter_complex", graph, "-map", "[v]", *frame_args,
         "-c:v", "libx264", "-preset", "slow", "-profile:v", "high", "-pix_fmt", "yuv420p",
         "-b:v", f"{mbps}M", "-maxrate", f"{mbps * 1.5}M", "-bufsize", f"{mbps * 3}M",
         "-x264-params", f"keyint={int(out_fps * 2)}:min-keyint={int(out_fps * 2)}:scenecut=0:open-gop=0",
         "-r", f"{out_fps:g}", seg)
    seg_info = probe(seg, "stream=nb_read_packets,r_frame_rate")["streams"][0]
    seg_frames = int(seg_info["nb_read_packets"])
    sn, sd = (int(v) for v in seg_info["r_frame_rate"].split("/"))
    seg_dur = seg_frames * sd / sn
    print(f"Loop segment: {seg_frames} frames at {sn / sd:g} fps = {seg_dur:.2f}s (speed {speed}x, seam fade {xfade}s)", flush=True)
    # Loop the encoded clip under the whole mix without re-encoding the video. An exact loop count and duration,
    # because -stream_loop -1 with stream copy does not stop at -shortest.
    mix_dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(mix)],
                                   check=True, capture_output=True, text=True).stdout)
    loops = int(mix_dur / seg_dur) + 1
    sh("ffmpeg", "-y", "-v", "error", "-stream_loop", loops, "-i", seg, "-i", mix, "-map", "0:v", "-map", "1:a",
       "-c:v", "copy", "-c:a", "aac", "-b:a", "320k", "-t", f"{mix_dur:.3f}", "-movflags", "+faststart", out)
    size = out.stat().st_size / 1e9
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                               check=True, capture_output=True, text=True).stdout)
    print(f"Rendered {out.name}: {dur / 60:.1f} min, {size:.2f} GB", flush=True)
    return dur, mbps


def access_token():
    need = ["YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"]
    missing = [n for n in need if not os.environ.get(n)]
    if missing:
        sys.exit(f"Missing GitHub secrets: {', '.join(missing)}. Run get_token.py once on the PC.")
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"], "grant_type": "refresh_token"}, timeout=60)
    if r.status_code != 200:
        sys.exit(f"Google token refresh failed ({r.status_code}): {r.json().get('error_description', r.text[:200])}")
    return r.json()["access_token"]


def upload(path, meta, token):
    size = path.stat().st_size
    init = requests.post("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                         headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
                                  "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
                         data=json.dumps(meta), timeout=60)
    if init.status_code != 200:
        sys.exit(f"YouTube refused the upload ({init.status_code}): {init.text[:500]}")
    loc, sent, chunk = init.headers["Location"], 0, 64 * 1024 * 1024
    with open(path, "rb") as f:
        while True:
            f.seek(sent)
            data = f.read(chunk)
            end = sent + len(data) - 1
            for attempt in range(5):
                try:
                    r = requests.put(loc, data=data, timeout=600, headers={
                        "Authorization": f"Bearer {token}", "Content-Range": f"bytes {sent}-{end}/{size}"})
                    break
                except requests.RequestException as e:
                    print(f"Chunk retry {attempt + 1}: {e}", flush=True)
                    time.sleep(5 * (attempt + 1))
            else:
                sys.exit("Upload failed after retries.")
            if r.status_code in (200, 201):
                return r.json()
            if r.status_code == 308:
                rng = r.headers.get("Range")
                sent = int(rng.split("-")[1]) + 1 if rng else 0
                print(f"Uploaded {sent / size:.0%}", flush=True)
                continue
            sys.exit(f"Upload error {r.status_code}: {r.text[:500]}")


def set_thumbnail(video_id, thumb_url, work, token):
    src = work / "thumb_src"
    download(thumb_url, src)
    jpg = work / "thumb.jpg"
    for q in (2, 4, 6, 8):  # YouTube wants 1280x720 under 2 MB
        sh("ffmpeg", "-y", "-v", "error", "-i", src, "-vf", "scale=1280:720:flags=lanczos", "-q:v", q, jpg)
        if jpg.stat().st_size < 2_000_000:
            break
    r = requests.post(f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={video_id}",
                      headers={"Authorization": f"Bearer {token}", "Content-Type": "image/jpeg"},
                      data=jpg.read_bytes(), timeout=120)
    if r.status_code != 200:
        print(f"Thumbnail not set ({r.status_code}): {r.text[:300]}. Custom thumbnails need a phone-verified channel.", flush=True)
        return False
    print(f"Thumbnail set on {video_id} ({jpg.stat().st_size // 1024} KB)", flush=True)
    return True


def shift_timestamps(desc, secs):
    """Move every tracklist timestamp except 00:00 earlier by secs (used with --trim-start)."""
    def fmt(t):
        h, rem = divmod(int(t), 3600)
        m, sec = divmod(rem, 60)
        return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"

    def fix(m):
        parts = [int(x) for x in m.group(1).split(":")]
        t = parts[0] * 3600 + parts[1] * 60 + parts[2] if len(parts) == 3 else parts[0] * 60 + parts[1]
        return (fmt(max(0, round(t - secs))) if t > 0 else m.group(1)) + m.group(2)

    return re.sub(r"(?m)^((?:\d+:)?\d{1,2}:\d{2})(\s)", fix, desc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--loop-url", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    ap.add_argument("--publish-at", default="", help="RFC3339 time to go public (video stays private until then)")
    ap.add_argument("--synthetic", default="false", help="YouTube altered or synthetic content flag for the AI visuals")
    ap.add_argument("--mbps", type=float, default=None, help="video bitrate (default 5, or 8 above 30 fps)")
    ap.add_argument("--speed", type=float, default=2.5, help="speed-up applied to the loop clip (default 2.5)")
    ap.add_argument("--xfade", type=float, default=1.0, help="seam crossfade in seconds of source clip (0 = off)")
    ap.add_argument("--mix", help="local mix.mp3 instead of the release download")
    ap.add_argument("--loop-file", help="local loop clip instead of --loop-url")
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--thumb-url", default="", help="image to set as the thumbnail (resized to 1280x720)")
    ap.add_argument("--video-id", default="", help="only set the thumbnail on this existing video; no render or upload")
    ap.add_argument("--extra", default="", help='JSON of optional settings, e.g. {"trim_start": 16.4, "grain": 22}')
    ap.add_argument("--clips", nargs="*", default=[], help="more loop clips (URLs) for --pattern; clip 1 is --loop-url")
    ap.add_argument("--pattern", default="", help="shot order, e.g. 1x4,2,1x5,2,1x7,2,1x5,2 (clips must share start/end frame)")
    ap.add_argument("--shake", type=float, default=0, help="handheld camera wobble in pixels (0 = off, about 8 = subtle)")
    ap.add_argument("--grain", type=int, default=0, help="film grain strength on the loop (0 = off, about 20 = 2001 camera)")
    ap.add_argument("--trim-start", type=float, default=0.0,
                    help="cut this many seconds off the start of the mix (cold open for mixes made before it was "
                         "built in); description timestamps shift to match")
    a = ap.parse_args()
    if a.extra.strip():
        for k, v in json.loads(a.extra).items():
            k = k.replace("-", "_")
            if k == "clips":
                a.clips = [str(u) for u in v]
            elif k == "pattern":
                a.pattern = str(v)
            elif k in ("trim_start", "grain", "xfade", "mbps", "shake"):
                setattr(a, k, type(getattr(a, k) if getattr(a, k) is not None else 0.0)(v))
            else:
                sys.exit(f"unknown extra setting: {k}")

    if len(a.title) > 100:
        sys.exit(f"Title is {len(a.title)} characters; YouTube allows 100.")
    day = Path("days") / a.date
    work = Path("out") / f"video-{a.date}"
    work.mkdir(parents=True, exist_ok=True)
    if a.video_id:
        if not a.thumb_url:
            sys.exit("--video-id needs --thumb-url")
        sys.exit(0 if set_thumbnail(a.video_id, a.thumb_url, work, access_token()) else 1)
    mix = Path(a.mix) if a.mix else work / "mix.mp3"
    if not a.mix:
        download(f"https://github.com/{REPO}/releases/download/mix-{a.date}/mix.mp3", mix)
    if a.trim_start > 0:
        trimmed = work / "mix_trimmed.mp3"
        sh("ffmpeg", "-y", "-loglevel", "error", "-ss", f"{a.trim_start:.3f}", "-i", str(mix),
           "-af", "afade=t=in:d=0.25", "-c:a", "libmp3lame", "-b:a", "320k", str(trimmed))
        mix = trimmed
    loop = Path(a.loop_file) if a.loop_file else work / "loop_src.mp4"
    if not a.loop_file:
        download(a.loop_url, loop)
    clip_files = []
    for i, u in enumerate(a.clips, 2):
        f = work / f"loop_src{i}.mp4"
        if Path(u).exists():
            f = Path(u)
        else:
            download(u, f)
        clip_files.append(f)
    out = work / f"mix-{a.date}.mp4"
    t0 = time.time()
    dur, mbps = render(work, loop, mix, out, a.mbps, a.speed, a.xfade, a.grain, a.shake, clip_files, a.pattern)
    print(f"Render took {time.time() - t0:.0f}s", flush=True)
    if a.render_only:
        return

    brief = json.loads((day / "brief.json").read_text(encoding="utf-8")) if (day / "brief.json").exists() else {}
    desc = (day / "description.txt").read_text(encoding="utf-8") if (day / "description.txt").exists() else ""
    if a.trim_start > 0:
        desc = shift_timestamps(desc, a.trim_start)
    tags = (brief.get("youtube") or {}).get("tags") or []
    status = {"privacyStatus": a.privacy, "selfDeclaredMadeForKids": False,
              "containsSyntheticMedia": a.synthetic.lower() == "true"}
    if a.publish_at:
        status.update(privacyStatus="private", publishAt=a.publish_at)
    meta = {"snippet": {"title": a.title, "description": desc[:5000], "tags": tags[:30], "categoryId": "10"}, "status": status}
    t1 = time.time()
    res = upload(out, meta, access_token())
    vid = res["id"]
    print(f"Uploaded in {time.time() - t1:.0f}s: https://youtu.be/{vid} ({res['status']['privacyStatus']})", flush=True)
    thumb_ok = set_thumbnail(vid, a.thumb_url, work, access_token()) if a.thumb_url else None
    (day / "youtube.json").write_text(json.dumps({
        "video_id": vid, "url": f"https://youtu.be/{vid}", "title": a.title, "privacy": res["status"]["privacyStatus"],
        "publish_at": a.publish_at or None, "loop_url": a.loop_url, "duration_s": round(dur), "bitrate_mbps": mbps, "speed": a.speed, "seam_xfade_s": a.xfade,
        "trim_start_s": a.trim_start or None, "grain": a.grain or None, "shake": a.shake or None,
        "clips": a.clips or None, "pattern": a.pattern or None,
        "thumbnail_url": a.thumb_url or None, "thumbnail_set": thumb_ok},
        indent=2), encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"Uploaded **{a.title}** ({res['status']['privacyStatus']}): https://youtu.be/{vid}\n")


if __name__ == "__main__":
    main()

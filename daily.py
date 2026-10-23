"""Generate one day's Y2K drum & bass mix and its handoff pack.

Usage:
  python3 daily.py                       # today's date in Brisbane time
  python3 daily.py --date 2026-10-22     # a specific day
  python3 daily.py --tracks 3            # quick test with 3 tracks
  python3 daily.py --theme outback       # force a theme (ids in dnbgen/themes.py)
  python3 daily.py --no-history          # don't update history.json (for tests)
  python3 daily.py --variant 1           # re-roll the day with a different seed

Outputs land in out/<date>/:
  mix.mp3, short_1..3.mp3, midi.zip       (release assets)
  brief.json, brief.md, description.txt, tracklist.txt, plan.json, qa.json  (also committed under days/<date>/)
"""
import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor
from zoneinfo import ZoneInfo

import numpy as np
import scipy.io.wavfile as wf

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from dnbgen.engine import Track, SR  # noqa: E402
from dnbgen import transitions as T  # noqa: E402
from dnbgen.recipes import plan_mix  # noqa: E402
from dnbgen.themes import THEMES_BY_ID  # noqa: E402

DEFAULT_REPO = "ctemenes/Music-gen-daily"
BASE_TAGS = ["y2k dnb", "ps1 drum and bass", "racing game music", "2000s jungle", "liquid dnb", "retro racing",
             "rollcage style", "low poly dnb", "breakbeat mix", "drum and bass mix", "ps1 racing", "y2k aesthetic"]
HASHTAGS = ["#y2kdnb", "#drumandbass", "#ps1"]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def ts(seconds):
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def run(cmd):
    subprocess.run(cmd, check=True)


# ---------------------------------------------------------------- workers
def render_track(args):
    recipe, base = args
    t0 = time.time()
    Track(recipe).render(base, mp3=False, midi=True)
    return recipe["name"], time.time() - t0


def render_transition(args):
    wa, wb, ta, tb, out_wav, seed = args
    A, B = T.load(wa), T.load(wb)
    mix, _ = T.build_transition(ta, tb, A, B, seed=seed)
    wf.write(out_wav, SR, (np.clip(mix, -1, 1) * 32767).astype(np.int16))
    return out_wav, len(mix)


# ---------------------------------------------------------------- pipeline
def slice_wav(src, a, b, dst):
    sr, x = wf.read(src, mmap=True)
    wf.write(dst, SR, np.array(x[a:b]))
    return b - a


def build(date, repo, n_tracks=None, theme=None, update_history=True, workers=2, variant=0):
    t_start = time.time()
    day = date.isoformat()
    work = os.path.join(ROOT, "build", day)
    out = os.path.join(ROOT, "out", day)
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    os.makedirs(out, exist_ok=True)

    hist_path = os.path.join(ROOT, "history.json")
    history = json.load(open(hist_path)) if os.path.exists(hist_path) else {"used_names": [], "mixes": []}
    if any(m["date"] == day for m in history["mixes"]):
        log(f"note: {day} already in history, re-planning without its own entry")
        history = dict(used_names=[n for m in history["mixes"] if m["date"] != day for n in m["tracks"]]
                       + history.get("legacy_names", []),
                       mixes=[m for m in history["mixes"] if m["date"] != day],
                       legacy_names=history.get("legacy_names", []))
    plan = plan_mix(date, history, theme, n_tracks, variant)
    th = THEMES_BY_ID[plan["theme"]]
    tracks = plan["tracks"]
    N = len(tracks)
    json.dump(plan, open(os.path.join(out, "plan.json"), "w"), indent=2)
    log(f"{day}: theme '{th['name']}', {N} tracks")

    # 1. render tracks
    bases = [os.path.join(work, f"t{i:02d}") for i in range(N)]
    with ProcessPoolExecutor(workers) as ex:
        for name, secs in ex.map(render_track, zip(tracks, bases)):
            log(f"  track '{name}' rendered in {secs:.0f}s")

    # 2. transitions i -> i+1
    jobs = [(bases[i] + ".wav", bases[i + 1] + ".wav", tracks[i]["bpm"], tracks[i + 1]["bpm"],
             os.path.join(work, f"x{i:02d}.wav"), (plan["seed"] + i) % (2 ** 32)) for i in range(N - 1)]
    trans_len = {}
    with ProcessPoolExecutor(workers) as ex:
        for path, n in ex.map(render_transition, jobs):
            trans_len[path] = n
    log(f"  {N - 1} transitions built")

    # 3. cut segments and assemble the continuous mix (streamed through ffmpeg, never all in memory)
    segs, timeline, pos = [], [], 0
    first_bpm = tracks[0]["bpm"]
    p = os.path.join(work, "s_start.wav")
    pos += slice_wav(bases[0] + ".wav", 0, T.bs(first_bpm, T.MAIN[0]), p)
    segs.append(p)
    main_start = []
    for i in range(N):
        bpm = tracks[i]["bpm"]
        if i == 0:
            timeline.append(0)
        main_start.append(pos)
        p = os.path.join(work, f"s_main{i:02d}.wav")
        pos += slice_wav(bases[i] + ".wav", T.bs(bpm, T.MAIN[0]), T.bs(bpm, T.MAIN[1]), p)
        segs.append(p)
        if i < N - 1:
            xp = os.path.join(work, f"x{i:02d}.wav")
            timeline.append(pos)  # next track is announced where its pads come in
            segs.append(xp)
            pos += trans_len[xp]
    last_bpm = tracks[-1]["bpm"]
    _, last = wf.read(bases[-1] + ".wav", mmap=True)
    p = os.path.join(work, "s_end.wav")
    pos += slice_wav(bases[-1] + ".wav", T.bs(last_bpm, T.MAIN[1]), len(last), p)
    segs.append(p)
    total = pos

    listf = os.path.join(work, "concat.txt")
    with open(listf, "w") as f:
        for s in segs:
            f.write(f"file '{s}'\n")
    mix_wav = os.path.join(work, "mix.wav")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listf, "-c", "copy", mix_wav])
    log(f"  mix assembled: {ts(total / SR)}")

    # 4. QA on the assembled mix
    qa = quality_checks(mix_wav, segs, total, tracks, timeline)
    json.dump(qa, open(os.path.join(out, "qa.json"), "w"), indent=2)
    log(f"  QA: {'PASS' if qa['pass'] else 'FAIL'} {qa['problems']}")

    # 5. encode deliverables
    title = f"{th['name']} | Y2K DnB Mix {day}"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", mix_wav, "-c:a", "libmp3lame", "-b:a", "320k",
         "-metadata", f"title={title}", "-metadata", "artist=Digital Aura Racing Team",
         "-metadata", f"date={day}", os.path.join(out, "mix.mp3")])
    shorts = make_shorts(tracks, bases, main_start, out)
    with zipfile.ZipFile(os.path.join(out, "midi.zip"), "w", zipfile.ZIP_DEFLATED) as z:
        for i, r in enumerate(tracks):
            z.write(bases[i] + ".mid", f"{i + 1:02d} - {r['name']}.mid")

    # 6. brief + text files
    tracklist = [dict(n=i + 1, title=r["name"], start_seconds=round(timeline[i] / SR, 2),
                      timestamp=ts(timeline[i] / SR), bpm=r["bpm"], style=style_label(r),
                      style_tags=style_tags(r)) for i, r in enumerate(tracks)]
    brief = make_brief(date, repo, th, plan, tracklist, total, shorts, qa)
    json.dump(brief, open(os.path.join(out, "brief.json"), "w"), indent=2, ensure_ascii=False)
    open(os.path.join(out, "brief.md"), "w").write(brief_md(brief))
    open(os.path.join(out, "description.txt"), "w").write(brief["youtube"]["description"])
    open(os.path.join(out, "tracklist.txt"), "w").write(brief["youtube"]["tracklist_text"] + "\n")

    # 7. history (so names and themes never repeat too soon)
    if update_history and qa["pass"]:
        history = json.load(open(hist_path)) if os.path.exists(hist_path) else {"used_names": [], "mixes": []}
        history["mixes"] = [m for m in history["mixes"] if m["date"] != day]
        history["mixes"].append(dict(date=day, theme=th["id"], tracks=[r["name"] for r in tracks],
                                     seconds=round(total / SR)))
        names = set(history.get("legacy_names", []))
        for m in history["mixes"]:
            names.update(m["tracks"])
        history["used_names"] = sorted(names)
        json.dump(history, open(hist_path, "w"), indent=2)

    shutil.rmtree(work, ignore_errors=True)
    log(f"done in {(time.time() - t_start) / 60:.1f} min -> {out}")
    return brief


def quality_checks(mix_wav, segs, total, tracks, timeline):
    sr, x = wf.read(mix_wav, mmap=True)
    problems = []
    if len(x) != total:
        problems.append(f"length mismatch {len(x)} vs {total}")
    # peaks + clipping, in chunks
    peak, clipped = 0.0, 0
    for s in range(0, len(x), SR * 30):
        c = np.abs(np.asarray(x[s:s + SR * 30], dtype=np.float32)) / 32767
        peak = max(peak, float(c.max()))
        clipped += int((c > 0.99).sum())
    if clipped > 50:
        problems.append(f"{clipped} clipped samples")
    # seams between segments: no clicks
    # a click is a sample-to-sample jump at the seam far bigger than anything in the 50ms around it
    pos, jumps = 0, []
    w = SR // 20
    for s in segs[:-1]:
        _, y = wf.read(s, mmap=True)
        pos += len(y)
        region = np.asarray(x[max(0, pos - w):pos + w], dtype=np.float32) / 32767
        d = np.abs(np.diff(region, axis=0)).max(axis=1)
        k = min(w, pos) - 1                     # index of the seam step inside d
        seam = float(d[k])
        around = np.concatenate([d[:max(0, k - 2)], d[k + 3:]])
        ref = float(around.max()) if len(around) else 0.0
        jumps.append(round(seam / (ref + 1e-4), 2))
        if seam > 0.08 and seam > 2.5 * ref:
            problems.append(f"click at seam {len(jumps)} (jump {seam:.2f} vs {ref:.2f} nearby)")
    # silence check: no 4s gap of near silence after the intro
    quiet_run, longest = 0, 0
    hop = SR // 2
    for s in range(SR * 15, len(x) - SR * 10, hop):
        c = np.asarray(x[s:s + hop, 0], dtype=np.float32) / 32767
        rms = float(np.sqrt((c ** 2).mean()))
        quiet_run = quiet_run + 1 if rms < 0.003 else 0
        longest = max(longest, quiet_run)
    if longest * 0.5 >= 4:
        problems.append(f"{longest * 0.5:.1f}s of silence inside the mix")
    if sorted(timeline) != timeline:
        problems.append("timestamps out of order")
    return dict(**{"pass": not problems}, problems=problems, peak=round(peak, 3), clipped_samples=clipped,
                worst_seam_ratio=max(jumps, default=0), duration_seconds=round(len(x) / SR, 2))


def make_shorts(tracks, bases, main_start, out, n=3, seconds=45):
    """45s clips from the run-in to drop B of the three most energetic, well-spaced tracks."""
    def energy(r):
        return ({"rolling": 0, "twostep": 1, "amen": 2, "jungle": 3, "techstep": 3}[r["drums"]]
                + {"sub": 0, "reese": 1, "stab": 2, "growl": 2}[r["bass"]] + (r["bpm"] - 170) / 4)
    order = sorted(range(len(tracks)), key=lambda i: -energy(tracks[i]))
    picks = []
    for i in order:
        if all(abs(i - j) > 1 for j in picks) or len(tracks) < 5:
            picks.append(i)
        if len(picks) == min(n, len(tracks)):
            break
    shorts = []
    for k, i in enumerate(sorted(picks)):
        bpm = tracks[i]["bpm"]
        start_bar = 60  # 4 bars before drop B, so the clip opens on the build and hits the drop
        a = T.bs(bpm, start_bar)
        b = a + SR * seconds
        dst = os.path.join(out, f"short_{k + 1}.mp3")
        run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{a / SR:.4f}", "-t", f"{seconds}",
             "-i", bases[i] + ".wav", "-af", f"afade=t=in:d=0.3,afade=t=out:st={seconds - 1.5}:d=1.5",
             "-c:a", "libmp3lame", "-b:a", "320k", dst])
        drop_at = (T.bs(bpm, 64) - a) / SR
        shorts.append(dict(file=os.path.basename(dst), track=tracks[i]["name"], seconds=seconds,
                           drop_hits_at_seconds=round(drop_at, 2),
                           mix_position_seconds=round((main_start[i] + a - T.bs(bpm, T.MAIN[0])) / SR, 2)))
    return shorts


def style_label(r):
    d = {"twostep": "two-step", "rolling": "rolling", "amen": "amen break", "techstep": "techstep",
         "jungle": "jungle"}[r["drums"]]
    b = {"reese": "reese bass", "sub": "deep sub", "growl": "growling bass", "stab": "stab bass"}[r["bass"]]
    return f"{d}, {b}"


def style_tags(r):
    tags = {"rolling": ["liquid", "rolling"], "twostep": ["two-step"], "amen": ["amen break", "breakbeat"],
            "techstep": ["techstep", "dark"], "jungle": ["jungle", "chopped breaks"]}[r["drums"]]
    tags += {"reese": ["reese"], "sub": ["deep"], "growl": ["neuro-ish"], "stab": ["stabs"]}[r["bass"]]
    tags += {"rhodes": ["jazzy"], "dark": ["moody"], "strings": ["cinematic"], "warm": ["warm"]}[r["pad"]]
    return tags


def make_brief(date, repo, th, plan, tracklist, total, shorts, qa):
    tag = f"mix-{date.isoformat()}"
    dl = f"https://github.com/{repo}/releases/download/{tag}"
    tl_text = "\n".join(f"{t['timestamp']} {t['title']}" for t in tracklist)
    bpms = [t["bpm"] for t in tracklist]
    styles = sorted({s for t in tracklist for s in t["style_tags"]})
    desc = (f"{th['hook']}\n"
            "Y2K drum & bass in the spirit of Rollcage Stage II and the PS1 racing era: breakbeats, rolling bass "
            "and that late 90s / early 2000s game soundtrack energy.\n\n"
            "Put it on for driving, gaming, coding, studying or just staring out the window pretending you're "
            "in a loading screen.\n\n"
            "━━━━━━━━━━━━━━━\nTRACKLIST\n" + tl_text + "\n━━━━━━━━━━━━━━━\n\n"
            "🏎️ Visuals: Digital Aura Racing Team\n"
            "🎛️ Music: original tracks generated with custom Python scripts\n"
            "💿 Inspired by the sound of Rollcage Stage II and classic PS1 racers. Not affiliated with or endorsed "
            "by the original game or its publishers.\n\n"
            "If this takes you back, drop your favourite old racing game in the comments and subscribe for more "
            "Y2K DnB.\n\n" + " ".join(HASHTAGS))
    tags = BASE_TAGS + [th["name"].lower(), th["setting"], th["time_of_day"]] + th["mood_words"][:3]
    return {
        "schema_version": 1,
        "mix_id": date.isoformat(),
        "date": date.isoformat(),
        "theme": {
            "id": th["id"], "name": th["name"], "scene": th["scene"], "setting": th["setting"],
            "time_of_day": th["time_of_day"], "weather": th["weather"], "mood_words": th["mood_words"],
            "palette": [{"name": n, "hex": h} for n, h in th["palette"]],
            "visual_motifs": th["visual_motifs"], "hook_line": th["hook"],
            "thumbnail_prompts": th["thumbnail_prompts"],
        },
        "music": {
            "track_count": len(tracklist), "duration_seconds": round(total / SR, 2), "duration": ts(total / SR),
            "bpm_range": [min(bpms), max(bpms)], "style_tags": styles, "tracks": tracklist,
            "transitions": "Continuous mix. Every track hands over to the next with an 8-bar DJ transition "
                           "(tempo glide, filter sweep, impact and riser), about 11 seconds each.",
        },
        "audio": {
            "release_tag": tag, "release_url": f"https://github.com/{repo}/releases/tag/{tag}",
            "mix": {"file": "mix.mp3", "url": f"{dl}/mix.mp3", "format": "mp3 320kbps 44.1kHz stereo"},
            "shorts": [dict(s, url=f"{dl}/{s['file']}") for s in shorts],
            "midi": {"file": "midi.zip", "url": f"{dl}/midi.zip"},
            "download_hint": f"gh release download {tag} -R {repo}",
        },
        "youtube": {
            "description": desc, "tracklist_text": tl_text, "tags": tags, "hashtags": HASHTAGS,
            "chapters_note": "Timestamps assume the video starts exactly when the mix starts. If the video adds an "
                             "intro, add its length to every timestamp except 00:00.",
        },
        "handoff": {
            "next_agent_tasks": [
                "Write 3 title options. Keep 'Y2K DnB' or 'PS1 drum and bass' in the title and lean on the theme's scene "
                "(e.g. a short second-person scenario like the hook line). Under 70 characters.",
                "Make a 1280x720 thumbnail from theme.thumbnail_prompts and theme.palette: low-poly PS1 look, one strong "
                "focal car, high contrast, at most 3 to 4 words of text.",
                "Make the video loop: a clean 16:9 frame of the thumbnail scene (no text or spine), then a 15s Kling 3.0 "
                "Pro clip (sound off) with that frame as BOTH start and end image. The video prompt must always ask for "
                "high speed: the car racing flat out, road markings, streetlights and scenery rushing past fast, motion "
                "blur, constant high speed. Then run the 'Mix video to YouTube' workflow with the clip URL; it fixes the "
                "seam and speeds the loop up (speed input, default 2.5x) before looping it under mix.mp3.",
                "Make 3 vertical Shorts from audio.shorts; the drop lands at drop_hits_at_seconds, so cut visuals to it. "
                "Each Short should point to the full mix.",
                "Use youtube.description as-is (adjust timestamps if the video has an intro) and youtube.tags.",
            ],
            "rules": [
                "Do not use Rollcage, Psygnosis, Sony or PlayStation logos, box art or real game footage.",
                "Do not claim the music is from or affiliated with Rollcage or any real game.",
                "Keep the 'generated with custom Python scripts' line in the description.",
            ],
        },
        "qa": qa,
    }


def brief_md(b):
    th, mu, au = b["theme"], b["music"], b["audio"]
    lines = [f"# {th['name']}: Y2K DnB mix for {b['date']}", "",
             f"**Duration:** {mu['duration']}  |  **Tracks:** {mu['track_count']}  |  "
             f"**BPM:** {mu['bpm_range'][0]} to {mu['bpm_range'][1]}  |  **QA:** {'pass' if b['qa']['pass'] else 'FAIL'}",
             "", f"**Scene:** {th['scene']}", "", f"**Hook:** {th['hook_line']}", "",
             "**Mood:** " + ", ".join(th["mood_words"]), "",
             "**Palette:** " + ", ".join(f"{p['name']} {p['hex']}" for p in th["palette"]), "",
             "## Tracklist", "", "| Time | Track | BPM | Style |", "|---|---|---|---|"]
    lines += [f"| {t['timestamp']} | {t['title']} | {t['bpm']} | {t['style']} |" for t in mu["tracks"]]
    lines += ["", "## Files", "", f"- Mix: [{au['mix']['file']}]({au['mix']['url']})"]
    lines += [f"- Short {k + 1}: [{s['file']}]({s['url']}) from *{s['track']}*, drop at {s['drop_hits_at_seconds']}s"
              for k, s in enumerate(au["shorts"])]
    lines += [f"- MIDI: [{au['midi']['file']}]({au['midi']['url']})", "", "## Thumbnail prompts", ""]
    lines += [f"{k + 1}. {p}" for k, p in enumerate(th["thumbnail_prompts"])]
    lines += ["", "## YouTube description", "", "```", b["youtube"]["description"], "```", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (default: today in Brisbane)")
    ap.add_argument("--tracks", type=int)
    ap.add_argument("--theme")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--no-history", action="store_true")
    ap.add_argument("--variant", type=int, default=0, help="re-roll the day with a different seed (e.g. after a QA failure)")
    ap.add_argument("--workers", type=int, default=max(1, min(4, os.cpu_count() or 2)))
    a = ap.parse_args()
    d = dt.date.fromisoformat(a.date) if a.date else dt.datetime.now(ZoneInfo("Australia/Brisbane")).date()
    b = build(d, a.repo, a.tracks, a.theme, not a.no_history, a.workers, a.variant)
    sys.exit(0 if b["qa"]["pass"] else 1)

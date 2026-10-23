#!/usr/bin/env python3
"""Archive one day's raw video assets into a neat per-video folder.

Runs in GitHub Actions (the runner can reach Higgsfield and vidIQ media URLs; Claude sessions can't).

  videos/<date> - <title>/          committed: README (prompts, tools, links), previews, text pack
  release mix-<date>                full-res originals + loop as raw_* assets, plus a zip of the whole folder

Full-res files live on the release, not in git, so the repo stays small enough for the daily
workflows (a day is 30 to 60 MB of PNGs and mp4).

Usage: python tools/archive_video.py --date 2026-10-23 --manifest manifest.json [--repo owner/repo]

manifest.json:
{"files": [{"file": "01_base_scene.png", "url": "https://...", "what": "Base scene",
            "tool": "vidIQ generate_thumbnail", "prompt": "...", "id": "job id"}],
 "brand": [{"file": "playlist_station_layout.png", "url": "https://...", "what": "..."}],
 "notes": ["anything worth keeping"]}
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def safe_name(s: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s)  # illegal on Windows
    s = re.sub(r"\s+", " ", s).strip().rstrip(".")
    return s[:120]


def fetch(url: str, dest: Path) -> int:
    for attempt in range(3):
        try:
            with requests.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            n = dest.stat().st_size
            if n > 0:
                return n
        except Exception as e:  # noqa: BLE001
            print(f"  retry {attempt + 1} for {dest.name}: {e}", file=sys.stderr)
    raise SystemExit(f"could not download {url}")


def preview(src: Path, dest: Path, width: int = 1280) -> None:
    im = Image.open(src).convert("RGB")
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    im.save(dest, "JPEG", quality=85, optimize=True)


def human(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--manifest")
    ap.add_argument("--repo", default="ctemenes/Music-gen-daily")
    ap.add_argument("--no-upload", action="store_true", help="skip the release upload (local test)")
    ap.add_argument("--index-only", action="store_true", help="only rebuild videos/README.md")
    a = ap.parse_args()
    if a.index_only:
        write_index(ROOT / "videos", a.repo)
        return

    day = ROOT / "days" / a.date
    yt = json.loads((day / "youtube.json").read_text())
    brief = json.loads((day / "brief.json").read_text())
    man = json.loads(Path(a.manifest).read_text())
    tag = f"mix-{a.date}"
    dl_base = f"https://github.com/{a.repo}/releases/download/{tag}"

    folder_name = f"{a.date} - {safe_name(yt['title'])}"
    vids = ROOT / "videos"
    vids.mkdir(exist_ok=True)
    # one folder per date: drop an older folder for this date if the title changed
    for old in vids.glob(f"{a.date} - *"):
        if old.name != folder_name:
            shutil.rmtree(old)
    out = vids / folder_name
    (out / "previews").mkdir(parents=True, exist_ok=True)

    raw = ROOT / "out" / "archive" / a.date / folder_name
    if raw.exists():
        shutil.rmtree(raw)
    raw.mkdir(parents=True)

    rows, prompts, uploads = [], [], []
    for item in man.get("files", []):
        fname = item["file"]
        print(f"downloading {fname}")
        p = raw / fname
        size = fetch(item["url"], p)
        asset = f"raw_{fname}"
        uploads.append((p, asset))
        prev = ""
        if p.suffix.lower() in IMG_EXT:
            pv = out / "previews" / (p.stem + ".jpg")
            preview(p, pv)
            prev = f"![{item.get('what', fname)}](previews/{pv.name})"
        rows.append((fname, item, size, asset, prev))
        if item.get("prompt"):
            prompts.append((fname, item))

    # shared brand references, saved once in the repo at full size (small files)
    brand_dir = vids / "_brand"
    for item in man.get("brand", []):
        dest = brand_dir / item["file"]
        if not dest.exists():
            brand_dir.mkdir(exist_ok=True)
            print(f"downloading brand ref {item['file']}")
            fetch(item["url"], dest)

    # text pack copies
    for name in ("description.txt", "tracklist.txt", "youtube.json", "brief.json"):
        if (day / name).exists():
            shutil.copy(day / name, out / name)
            shutil.copy(day / name, raw / name)

    theme = brief.get("theme", {})
    music = brief.get("music", {})
    lines = [
        f"# {yt['title']}",
        "",
        f"- **Date:** {a.date}",
        f"- **YouTube:** {yt.get('url', '')} ({yt.get('privacy', '')}"
        + (f", goes public {yt['publish_at']}" if yt.get("publish_at") else "") + ")",
        f"- **Theme:** {theme.get('name', '')}",
        f"- **Mix:** {music.get('duration', '')}, {music.get('track_count', len(music.get('tracks', [])))} tracks",
        f"- **Loop:** sped up {yt.get('speed', '')}x, {yt.get('seam_xfade_s', '')}s seam crossfade",
        f"- **Download everything:** [{a.date}_raw_pack.zip]({dl_base}/{a.date}_raw_pack.zip)",
        "",
        "## Files",
        "",
        "| File | What | Made with | Size | Full res |",
        "|---|---|---|---|---|",
    ]
    for fname, item, size, asset, _ in rows:
        lines.append(f"| {fname} | {item.get('what', '')} | {item.get('tool', '')} | {human(size)} "
                     f"| [download]({dl_base}/{asset}) |")
    lines += ["", "## Previews", ""]
    for fname, item, _, _, prev in rows:
        if prev:
            lines += [f"**{fname}**", "", prev, ""]
    if prompts:
        lines += ["## Prompts", ""]
        for fname, item in prompts:
            lines += [f"### {fname}", "", f"{item.get('tool', '')}"
                      + (f", job `{item['id']}`" if item.get("id") else ""), "",
                      "```", item["prompt"].strip(), "```", ""]
    if man.get("notes"):
        lines += ["## Notes", ""] + [f"- {n}" for n in man["notes"]] + [""]
    lines += ["## Also in this folder", "",
              "`description.txt` and `tracklist.txt` (as uploaded), `youtube.json` (upload record), "
              "`brief.json` (the day's theme and music brief).", ""]
    readme = "\n".join(lines)
    (out / "README.md").write_text(readme)
    (raw / "README.md").write_text(readme)
    (out / "manifest.json").write_text(json.dumps(man, indent=1, ensure_ascii=False))

    # zip of the whole folder with full-res files, for one-click download
    zpath = raw.parent / f"{a.date}_raw_pack.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
        for f in sorted(raw.rglob("*")):
            z.write(f, Path(folder_name) / f.relative_to(raw))
    uploads.append((zpath, zpath.name))

    if not a.no_upload:
        staged = []
        for p, asset in uploads:
            target = p.with_name(asset)
            if target != p:
                shutil.copy(p, target)
            staged.append(str(target))
        subprocess.run(["gh", "release", "upload", tag, "-R", a.repo, "--clobber", *staged], check=True)

    write_index(vids, a.repo)
    print(f"archived {folder_name}")


def write_index(vids: Path, repo: str) -> None:
    rows = []
    for d in sorted((p for p in vids.iterdir() if p.is_dir() and not p.name.startswith("_")), reverse=True):
        yj = d / "youtube.json"
        y = json.loads(yj.read_text()) if yj.exists() else {}
        date = d.name[:10]
        thumb = next(iter(sorted((d / "previews").glob("03_*.jpg"))), None)
        link = quote(d.name)
        cell = f'<img src="{link}/previews/{quote(thumb.name)}" width="240">' if thumb else ""
        rows.append(f"| {date} | [{y.get('title', d.name[13:])}]({link}/README.md) | {y.get('url', '')} | {cell} |")
    text = "\n".join([
        "# Videos",
        "",
        "One folder per uploaded video: README with every prompt, previews, description, tracklist and the",
        f"upload record. Full-res originals and the raw loop clip are on each day's release "
        f"(https://github.com/{repo}/releases), including a zip of the whole folder.",
        "",
        "Shared brand references (PlayList Station layout, D-A logo) are in [_brand](_brand).",
        "",
        "| Date | Title | YouTube | Thumbnail |",
        "|---|---|---|---|",
        *rows,
        "",
    ])
    (vids / "README.md").write_text(text)


if __name__ == "__main__":
    main()

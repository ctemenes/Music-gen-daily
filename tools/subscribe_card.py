#!/usr/bin/env python3
"""Make the 1920x1080 "new mix every 2 days / subscribe" card that render_upload.py cuts into the loop (--promo).
Digicam grade to match the mixes: cool tint, lifted blacks, bloom, vignette, grain. Writes assets/subscribe_card.png."""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1920, 1080
ROOT = Path(__file__).resolve().parent.parent
DEJAVU = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
METAL = ROOT / "assets" / "MetalMania-Regular.ttf"


def chrome_text(img, text, font, y):
    d = ImageDraw.Draw(img)
    bbox = d.textbbox((0, 0), text, font=font, anchor="lt")
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    mask = Image.new("L", (tw + 40, th + 40), 0)
    ImageDraw.Draw(mask).text((20, 20), text, font=font, fill=255, anchor="lt")
    col = np.zeros((th + 40, tw + 40, 3), np.uint8)
    for i, g in enumerate(np.linspace(0, 1, th + 40)):
        v = int(235 - 240 * abs(g - 0.45)) if g < 0.5 else int(120 + 220 * (g - 0.5))
        col[i] = (v, v, min(255, v + 25))
    shadow = mask.filter(ImageFilter.GaussianBlur(6))
    img.paste((0, 0, 0), (W // 2 - tw // 2 - 14, y + 6), shadow)
    img.paste(Image.fromarray(col), (W // 2 - tw // 2 - 20, y), mask)


def main(out=ROOT / "assets" / "subscribe_card.png", line1="NEW MIX EVERY 2 DAYS", line2="SUBSCRIBE  //  チャンネル登録"):
    img = Image.new("RGB", (W, H), (6, 6, 8))
    d = ImageDraw.Draw(img)
    for y in range(0, H, 4):
        d.line([(0, y), (W, y)], fill=(14, 14, 18))
    # D-A mark: red D over yellow, blue and green bars forming the A
    cx, cy = W // 2, 300
    d.text((cx - 120, cy), "D", font=ImageFont.truetype(DEJAVU, 230), fill=(220, 30, 40), anchor="mm")
    # the A: yellow, blue and green bars widening downwards, counter cut out of the lower two
    ax = cx + 125
    for i, c in enumerate([(240, 200, 40), (40, 110, 220), (40, 180, 80)]):
        y = cy - 105 + i * 72
        half = 38 + i * 42
        d.rectangle([ax - half, y, ax + half, y + 60], fill=c)
    d.rectangle([ax - 30, cy - 105 + 72 + 60, ax + 30, cy - 105 + 144], fill=(6, 6, 8))
    d.rectangle([ax - 44, cy - 105 + 144 + 30, ax + 44, cy - 105 + 216], fill=(6, 6, 8))
    chrome_text(img, line1, ImageFont.truetype(str(METAL), 150), 470)
    d = ImageDraw.Draw(img)
    f2 = ImageFont.truetype(DEJAVU, 64)
    cjk = next((f for f in ["/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc", "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf"] if Path(f).exists()), None)
    if "//" in line2 and cjk:
        left, right = (t.strip() for t in line2.split("//", 1))
        fj = ImageFont.truetype(cjk, 60)
        lw = d.textlength(left + "  //  ", font=f2)
        rw = d.textlength(right, font=fj)
        x0 = W // 2 - (lw + rw) / 2
        d.text((x0, 720), left + "  //  ", font=f2, fill=(240, 200, 40), anchor="lm")
        d.text((x0 + lw, 722), right, font=fj, fill=(240, 200, 40), anchor="lm")
    else:
        d.text((W // 2, 720), line2.split("//")[0].strip(), font=f2, fill=(240, 200, 40), anchor="mm")
    fy = ImageFont.truetype(DEJAVU, 46)
    d.text((W - 60, H - 50), "DIGITAL AURA", font=fy, fill=(240, 200, 40), anchor="rs")
    d.text((60, H - 50), "PlayList Station", font=fy, fill=(200, 200, 210), anchor="ls")
    a = np.asarray(img).astype(np.float32)
    a += 0.35 * np.asarray(img.filter(ImageFilter.GaussianBlur(18))).astype(np.float32)
    a = a * np.array([0.96, 0.98, 1.06]) + 10
    yy, xx = np.mgrid[0:H, 0:W]
    r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    a *= (1 - 0.45 * np.clip(r - 0.5, 0, 1))[:, :, None]
    a += np.random.default_rng(7).normal(0, 9, a.shape)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(out, optimize=True)
    print(out)


if __name__ == "__main__":
    main(*sys.argv[1:])

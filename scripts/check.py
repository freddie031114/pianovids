#!/usr/bin/env python3
"""check.py: look at the finished video. Makes a contact sheet and measures how well the red line meets the notes.

  python check.py WORK_DIR RESULT.mp4 SHEET.jpg [--samples 6] [same look options you gave render.py]

For a few real note starts heard in the audio, it grabs that frame, finds the notehead nearest the red line and
says how many milliseconds early (-) or late (+) the line reaches it. Then OPEN SHEET.jpg and look at it.
"""
import argparse, io, os, subprocess, sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import HOP, SR, find_tool, onset_flux, peaks, read_audio, spectrum  # noqa: E402
import render  # noqa: E402


def grab(video, t):
    r = subprocess.run([find_tool("ffmpeg"), "-v", "error", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1",
                        "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True)
    return Image.open(io.BytesIO(r.stdout)).convert("RGB")


def heads_x(band_img, gap):
    """x centres of noteheads in a band picture (dark blobs about one staff-space tall and wide)."""
    g = np.array(band_img.convert("L")).astype(np.int16)
    dark = g < 110
    hv = max(1, int(0.3 * gap)); hh = max(1, int(0.35 * gap))
    m = dark.copy()
    for s in (-hv, hv):
        m &= np.roll(dark, s, 0)
    m2 = m.copy()
    for s in (-hh, hh):
        m2 &= np.roll(m, s, 1)
    cols = np.where(m2.any(0))[0]
    if not len(cols):
        return []
    xs, start = [], cols[0]
    for a, b in zip(cols[:-1], cols[1:]):
        if b - a > 2:
            xs.append((start + a) / 2); start = b
    xs.append((start + cols[-1]) / 2)
    return xs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("work"); ap.add_argument("video"); ap.add_argument("sheet")
    ap.add_argument("--samples", type=int, default=6)
    for k, v in (("--band-top", 0.07), ("--band-height", 0.16), ("--staff", 0.40), ("--bars", 0), ("--playhead", 0.30),
                 ("--margin", 0.03), ("--opacity", 1.0)):
        ap.add_argument(k, type=float, default=v)
    ap.add_argument("--dark", action="store_true"); ap.add_argument("--ink-color", default="#ececf0"); ap.add_argument("--band-color", default="#ffffff"); ap.add_argument("--line-color", default="#e0262b")
    ap.add_argument("--line-width", type=int, default=4); ap.add_argument("--corner", type=int, default=22)
    a = ap.parse_args()
    B = render.Band(a.work, a)
    gap_px = B.N["geometry"]["gap"] / B.N["svg_units_per_png_px"] * B.scale

    # real note starts heard in the audio (independent of the alignment), matched to the nearest written note
    x = read_audio(a.video)
    S, t, hz, lvl = spectrum(x)
    pk = peaks(onset_flux(S), min_gap=5, rel=1.5)
    heard = t[pk]
    atk = [n for n in B.T["attacks"] if 0.2 < n["t"] < B.T["duration"] - 0.3]
    pairs = []
    for n in atk:
        if len(heard):
            k = int(np.argmin(np.abs(heard - n["t"])))
            if abs(heard[k] - n["t"]) < 0.15:
                pairs.append((n, float(heard[k])))
    pick = [pairs[int(i)] for i in np.linspace(0, len(pairs) - 1, min(a.samples, len(pairs)))] if pairs else []

    first = grab(a.video, 0.0)
    f0_luma = float(np.array(first.convert("L")).mean())
    W = first.size[0]; sc = W / B.W
    tiles, offs = [], []
    for n, ta in pick:
        fr = grab(a.video, ta)
        top, bh = int(B.top * sc), int(B.bh * sc)
        band = fr.crop((0, top, W, top + bh))
        hx = heads_x(band, gap_px * sc)
        lx = B.px * sc
        if hx:
            dx = min(hx, key=lambda v: abs(v - lx)) - lx
            speed = (B.scroll_x(ta + 0.05) - B.scroll_x(ta - 0.05)) / 0.1 * sc
            off = 1000 * dx / speed if speed > 1 else float("nan")
            offs.append(off)
        else:
            off = float("nan")
        z = band.crop((max(0, int(lx - 260 * sc)), 0, min(W, int(lx + 260 * sc)), bh)).resize((520, int(520 * bh / (520 * sc))))
        thumb = fr.resize((180, int(180 * fr.size[1] / W)))
        tile = Image.new("RGB", (520 + 190, max(z.size[1] + 40, thumb.size[1])), "#222")
        tile.paste(z, (0, 30)); tile.paste(thumb, (530, 0))
        ImageDraw.Draw(tile).text((6, 6), f"t={ta:.2f}s heard  note {n['midi']} bar {n['bar'] + 1}  line off {off:+.0f} ms",
                                  fill="white")
        tiles.append(tile)
    f0t = first.resize((180, int(180 * first.size[1] / W)))
    h = sum(t_.size[1] + 8 for t_ in tiles) + f0t.size[1] + 30
    sheet = Image.new("RGB", (720, h), "#111")
    d = ImageDraw.Draw(sheet)
    d.text((6, 6), f"frame 0 brightness {f0_luma:.0f}/255 {'(BLACK!)' if f0_luma < 12 else '(not black)'}", fill="white")
    sheet.paste(f0t, (6, 24)); y = f0t.size[1] + 30
    for tl in tiles:
        sheet.paste(tl, (5, y)); y += tl.size[1] + 8
    sheet.save(a.sheet, quality=90)
    med = float(np.nanmedian(np.abs(offs))) if offs else float("nan")
    print(f"frame 0 brightness {f0_luma:.0f} | offsets ms: {', '.join('%+.0f' % o for o in offs)} | median |offset| "
          f"{med:.0f} ms -> {'OK' if med <= 150 else 'TOO FAR, fix the timing (align.py --nudge-ms / --bpm / --start)'}")
    print("contact sheet ->", a.sheet, "(open it and look)")


if __name__ == "__main__":
    main()

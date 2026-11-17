#!/usr/bin/env python3
"""render.py: draw the scrolling sheet music band over the video.

  python render.py WORK_DIR OUT.mp4 [options]
  python render.py WORK_DIR still.png --still 3.5     (one picture at 3.5 s, to check the look fast)

Look options (all optional):
  --band-top 0.07      top of the white band, as a share of the video height (0 = very top)
  --band-height 0.16   height of the band, as a share of the video height
  --staff 0.40         staff height as a share of the band height (bigger = bigger notes, fewer bars on screen)
  --bars 0             show about this many bars across the screen instead (overrides --staff)
  --playhead 0.30      where the red line sits, as a share of the width from the left
  --band-color "#ffffff"  --line-color "#e0262b"  --line-width 4  --opacity 1.0  --corner 22
  --out-width 0        scale the finished video to this width (0 = keep the size), e.g. 540 for a small share copy
"""
import argparse, os, subprocess, sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import die, find_tool, load_json  # noqa: E402


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


class Band:
    def __init__(self, work, a):
        self.N = load_json(os.path.join(work, "notes.json"))
        self.T = load_json(os.path.join(work, "timeline.json"))
        W, H = self.T["w"], self.T["h"]
        self.W, self.H = W, H
        self.top = int(round(a.band_top * H))
        self.bh = int(round(a.band_height * H))
        g = self.N["geometry"]
        k = 1.0 / self.N["svg_units_per_png_px"]          # SVG units -> strip PNG pixels
        strip = Image.open(os.path.join(work, self.N["strip_png"])).convert("RGBA")
        # crop to the inked rows (staff, ledger lines, bar numbers, accents), keep the paper white
        alpha = np.array(strip)[:, :, 3]
        ink = np.array(strip.convert("L")) < 200
        rows = np.where(((alpha > 40) & ink).any(1))[0] if (alpha < 250).any() else np.where(ink.any(1))[0]
        pad = int(g["gap"] * k)
        y0, y1 = max(0, rows.min() - pad), min(strip.size[1], rows.max() + pad)
        staff_h = (g["staff_bottom"] - g["staff_top"]) * k
        if a.bars:
            bl = g["barlines"]
            bw = float(np.median(np.diff(bl))) * k if len(bl) > 2 else strip.size[0] / max(1, len(self.N["bars"]) - 1)
            s = (W * (1 - 2 * a.margin)) / (a.bars * bw)
        else:
            s = a.staff * self.bh / staff_h
        s = min(s, (self.bh * 0.96) / (y1 - y0))       # never taller than the band
        self.scale = s
        paper = Image.new("RGBA", strip.size, "#ffffff" if a.dark else a.band_color)
        paper.alpha_composite(strip)
        crop = paper.crop((0, y0, strip.size[0], y1)).convert("RGB")
        if a.dark:   # white paper/black ink -> dark band/light ink
            g_ = np.array(crop.convert("L")).astype(np.float32)[:, :, None] / 255.0
            bgc = np.array(hex_rgb(a.band_color), np.float32); inkc = np.array(hex_rgb(a.ink_color), np.float32)
            crop = Image.fromarray((bgc * g_ + inkc * (1 - g_)).astype(np.uint8), "RGB")
        self.strip = crop.resize((max(1, int(crop.size[0] * s)), max(1, int(crop.size[1] * s))), Image.LANCZOS)
        self.strip_y = (self.bh - self.strip.size[1]) // 2
        # where the staff centre lands inside the band (for the red line length)
        cy = ((g["staff_top"] + g["staff_bottom"]) / 2 * k - y0) * s + self.strip_y
        self.line_half = max(staff_h * s * 1.25, 0.32 * self.bh)
        self.line_cy = cy
        self.px = a.playhead * W
        self.a = a
        # scroll path: note x (in scaled strip pixels) against time
        atk = self.T["attacks"]
        ts = np.array([n["t"] for n in atk]); xs = np.array([n["x"] * k * s for n in atk])
        spb = 60.0 / self.T["bpm"]
        last = atk[-1]
        end_x = xs[-1] + max(last["ql"], 1.0) * (xs[-1] - xs[0]) / max(1e-6, (last["q"] - atk[0]["q"]))
        ts = np.r_[ts, ts[-1] + max(last["ql"], 1.0) * spb]
        xs = np.r_[xs, end_x]
        v0 = (xs[1] - xs[0]) / max(1e-3, ts[1] - ts[0]) if len(xs) > 1 else 100
        self.ts, self.xs, self.v0 = ts, xs, v0
        self._card()

    def scroll_x(self, t):
        if t <= self.ts[0]:
            return self.xs[0] - (self.ts[0] - t) * self.v0
        if t >= self.ts[-1]:
            return self.xs[-1] + (t - self.ts[-1]) * (self.xs[-1] - self.xs[-2]) / max(1e-3, self.ts[-1] - self.ts[-2])
        return float(np.interp(t, self.ts, self.xs))

    def _card(self):
        a = self.a
        m = int(a.margin * self.W)
        mask = Image.new("L", (self.W, self.bh), 0)
        ImageDraw.Draw(mask).rounded_rectangle((m, 0, self.W - m - 1, self.bh - 1), radius=a.corner, fill=255)
        self.mask = mask

    def frame(self, t):
        """the band (W x band height, RGBA) at time t."""
        a = self.a
        canvas = Image.new("RGB", (self.W, self.bh), a.band_color)
        sx = self.scroll_x(t)
        off = self.px - sx                             # strip pixel sx sits under the red line
        io = int(np.floor(off)); frac = off - io
        x_from = max(0, -io); x_to = min(self.strip.size[0], self.W - io + 1)
        if x_to > x_from:
            piece = self.strip.crop((x_from, 0, x_to, self.strip.size[1]))
            if frac > 1e-3:   # sub-pixel shift keeps slow scrolling smooth
                piece = piece.transform(piece.size, Image.AFFINE, (1, 0, -frac, 0, 1, 0), Image.BILINEAR,
                                        fillcolor=hex_rgb(a.band_color))
            canvas.paste(piece, (io + x_from, self.strip_y))
        d = ImageDraw.Draw(canvas)
        lx = self.px
        d.rectangle((lx - a.line_width / 2, self.line_cy - self.line_half, lx + a.line_width / 2 - 1,
                     self.line_cy + self.line_half), fill=a.line_color)
        out = canvas.convert("RGBA")
        alpha = self.mask.point(lambda v: int(v * a.opacity))
        out.putalpha(alpha)
        return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("work"); ap.add_argument("out")
    ap.add_argument("--still", type=float, help="write one PNG at this time instead of a video")
    ap.add_argument("--dark", action="store_true"); ap.add_argument("--ink-color", default="#ececf0")
    ap.add_argument("--band-top", type=float, default=0.07)
    ap.add_argument("--band-height", type=float, default=0.16)
    ap.add_argument("--staff", type=float, default=0.40)
    ap.add_argument("--bars", type=float, default=0)
    ap.add_argument("--playhead", type=float, default=0.30)
    ap.add_argument("--margin", type=float, default=0.03, help="side gap of the card, share of width")
    ap.add_argument("--band-color", default="#ffffff")
    ap.add_argument("--line-color", default="#e0262b")
    ap.add_argument("--line-width", type=int, default=4)
    ap.add_argument("--opacity", type=float, default=1.0)
    ap.add_argument("--corner", type=int, default=22)
    ap.add_argument("--out-width", type=int, default=0)
    ap.add_argument("--crf", type=int, default=18)
    a = ap.parse_args()
    B = Band(a.work, a)
    T = B.T
    ffmpeg = find_tool("ffmpeg")

    if a.still is not None:
        r = subprocess.run([ffmpeg, "-v", "error", "-ss", str(a.still), "-i", T["video"], "-frames:v", "1",
                            "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True)
        if r.returncode or not r.stdout:
            die("could not read a frame at %.2f s" % a.still)
        import io
        bg = Image.open(io.BytesIO(r.stdout)).convert("RGBA")
        bg.alpha_composite(B.frame(a.still), (0, B.top))
        bg.convert("RGB").save(a.out)
        print("still ->", a.out)
        return

    n = int(round(T["duration"] * T["fps"]))
    vf = f"[0:v][1:v]overlay=0:{B.top}:eof_action=pass,format=yuv420p"
    if a.out_width:
        vf += f",scale={a.out_width}:-2:flags=lanczos"
    cmd = [ffmpeg, "-v", "error", "-y", "-i", T["video"],
           "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{B.W}x{B.bh}", "-framerate", T["fps_str"], "-i", "-",
           "-filter_complex", vf + "[v]", "-map", "[v]", "-map", "0:a?",
           "-c:v", "libx264", "-preset", "medium", "-crf", str(a.crf), "-c:a", "aac", "-b:a", "160k",
           "-movflags", "+faststart", "-shortest", a.out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(n):
        p.stdin.write(B.frame(i / T["fps"]).tobytes())
        if i % 60 == 0:
            print(f"\r  frame {i}/{n}", end="", flush=True)
    p.stdin.close()
    if p.wait():
        die("ffmpeg failed while writing the video")
    print(f"\rdone: {n} frames -> {a.out}")


if __name__ == "__main__":
    main()

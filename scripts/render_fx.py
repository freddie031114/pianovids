#!/usr/bin/env python3
"""render_fx.py: animated sheet-music band (for Reels / Shorts) over the video.

  python render_fx.py WORK OUT.mp4 [--still 28.7] [--clip 25.5 31] [--title "..." --subtitle "..."] [--safe-zones]

Effects, all driven by the timing in WORK/timeline.json and the loudness of the audio:
  - each notehead lights up gold when it sounds and fades back (about half a second)
  - notes already played sit a little dimmer than the ones coming
  - trill lines shimmer while the trill sounds
  - a thin warm playhead with a soft glow that pulses with each note, stronger when the playing is louder
  - the score fades out at the left/right edges; the dark band blends into the video below it
  - an optional title card that fades in and out at the start
  - --fade-out START [END]: the whole band fades away (e.g. --fade-out 45 for the last seconds of a video)
The band sits below the top ~10% of the screen, where the Instagram/TikTok header goes. --safe-zones draws those UI
areas on a still so you can check nothing important is hidden.
"""
import argparse, io, os, re, subprocess, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
for p_ in (HERE, os.path.join(HERE, "scripts")):
    sys.path.insert(0, p_)
from common import find_tool, load_json, read_audio  # noqa: E402


GRADES = {
    # warm highlights, cooler shadows, softer greens, gentle highlight roll-off, light vignette and grain:
    # sits next to warm off-white notes with gold glow on a near-black band
    "nocturne": ("eq=contrast=1.08:brightness=-0.005:saturation=0.86:gamma=1.07,"
                 "curves=r='0/0.005 0.5/0.5 1/0.965':g='0/0.005 0.5/0.49 1/0.935':b='0/0.02 0.5/0.47 1/0.88',"
                 "colorbalance=rs=-0.03:bs=0.05:rm=0.035:bm=-0.035:rh=0.045:bh=-0.055,"
                 "vignette=angle=PI/3.2:mode=forward,noise=alls=3:allf=t"),
    "none": "null",
}


def hexrgb(h):
    h = h.lstrip("#"); return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], np.float32)


def nums(s):
    return [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", s)]


def svg_items(svg, cls):
    """bounding boxes (x0, y0, x1, y1) in SVG units of every <path class=cls>."""
    out = []
    for m in re.finditer(r'<path class="%s"([^>]*)>' % cls, svg):
        attrs = m.group(1)
        tr = re.search(r'transform="matrix\(([^)]*)\)"', attrs)
        d = re.search(r'\sd="([^"]+)"', attrs)
        if not d:
            continue
        a, b, c, dd, e, f = nums(tr.group(1)) if tr else (1, 0, 0, 1, 0, 0)
        v = nums(d.group(1))
        xs = [a * v[i] + c * v[i + 1] + e for i in range(0, len(v) - 1, 2)]
        ys = [b * v[i] + dd * v[i + 1] + f for i in range(0, len(v) - 1, 2)]
        if xs:
            out.append((min(xs), min(ys), max(xs), max(ys)))
    return out


def gauss_patch(sx, sy):
    rx, ry = int(3 * sx) + 1, int(3 * sy) + 1
    yy, xx = np.mgrid[-ry:ry + 1, -rx:rx + 1].astype(np.float32)
    return np.exp(-0.5 * ((xx / sx) ** 2 + (yy / sy) ** 2)), rx, ry


def add_patch(dst, patch, rx, ry, cx, cy, w):
    H, W = dst.shape
    x0, y0 = int(round(cx)) - rx, int(round(cy)) - ry
    xa, ya = max(0, x0), max(0, y0)
    xb, yb = min(W, x0 + patch.shape[1]), min(H, y0 + patch.shape[0])
    if xb > xa and yb > ya:
        dst[ya:yb, xa:xb] += w * patch[ya - y0:yb - y0, xa - x0:xb - x0]


class FX:
    def __init__(self, work, a):
        self.a = a
        N = load_json(os.path.join(work, "notes.json")); T = load_json(os.path.join(work, "timeline.json"))
        self.T = T
        self.W, self.H = T["w"], T["h"]
        W, H = self.W, self.H
        k = 1.0 / N["svg_units_per_png_px"]                      # SVG units -> strip PNG px
        strip = Image.open(os.path.join(work, N["strip_png"])).convert("RGBA")
        paper = Image.new("RGBA", strip.size, "white"); paper.alpha_composite(strip)
        ink = 1.0 - np.asarray(paper.convert("L"), np.float32) / 255.0
        rows = np.where((ink > 0.25).any(1))[0]
        g = N["geometry"]; gap = g["gap"]
        pad = int(1.2 * gap * k)
        y0, y1 = max(0, rows.min() - pad), min(ink.shape[0], rows.max() + pad)
        # layout
        self.top = int(a.safe_top * H)                             # score area starts below the app header
        self.bh = int(a.band_height * H)
        s = a.fill * self.bh / (y1 - y0)
        self.s = s
        h_s = int(round((y1 - y0) * s)); w_s = int(round(ink.shape[1] * s))
        im = Image.fromarray((ink[y0:y1] * 255).astype(np.uint8)).resize((w_s, h_s), Image.LANCZOS)
        self.pad = W                                               # empty margin so the strip can scroll fully in/out
        self.ink = np.zeros((h_s, w_s + 2 * self.pad), np.float32)
        self.ink[:, self.pad:self.pad + w_s] = np.asarray(im, np.float32) / 255.0
        self.ink_y = self.top + (self.bh - h_s) // 2                 # where the strip's top row lands on screen
        self.panel_bot = self.top + self.bh + int(0.01 * H)
        self.fade_h = int(0.045 * H)
        self.title_h = int(0.16 * H) if a.title else 0
        self.LH = self.panel_bot + self.fade_h + self.title_h        # overlay height
        self.gap_px = gap * k * s
        to_px = lambda xsvg: xsvg * k * s + self.pad
        to_py = lambda ysvg: (ysvg * k - y0) * s + self.ink_y
        # scroll path (strip px, padded) against time
        atk = T["attacks"]
        self.at = np.array([n["t"] for n in atk]); ax_svg = np.array([n["x"] for n in atk])
        xs = to_px(ax_svg)
        last = atk[-1]; spb = 60.0 / T["bpm"]
        ts = np.r_[self.at, self.at[-1] + max(last["ql"], 1.0) * spb]
        xs = np.r_[xs, xs[-1] + max(last["ql"], 1.0) * (xs[-1] - xs[0]) / max(1e-6, last["q"] - atk[0]["q"])]
        self.ts, self.xs = ts, xs
        self.px = a.playhead * W
        # noteheads -> the beat position they belong to
        svg = open(os.path.join(work, "strip.svg")).read()
        heads = svg_items(svg, "Note")
        self.heads = []
        for bx0, by0, bx1, by1 in heads:
            cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
            j = int(np.argmin(np.abs(ax_svg - cx)))
            small = (bx1 - bx0) < 0.9 * gap                          # grace note
            self.heads.append((to_px(cx), to_py(cy), float(self.at[j]) - (0.06 if small else 0.0), small))
        # trill lines: group the wavy segments into lines
        segs = sorted(svg_items(svg, "TrillSegment"), key=lambda b: (round(b[1] / gap), b[0]))
        lines = []
        for b in segs:
            if lines and abs(b[1] - lines[-1][1]) < gap and b[0] - lines[-1][2] < 2 * gap:
                L = lines[-1]; lines[-1] = [L[0], min(L[1], b[1]), max(L[2], b[2]), max(L[3], b[3])]
            else:
                lines.append(list(b))
        self.trills = []
        for bx0, by0, bx1, by1 in lines:
            if bx1 - bx0 < 3 * gap:
                continue                                              # the little "tr" sign on its own
            X0, X1 = to_px(bx0), to_px(bx1)
            t0 = float(np.interp(X0, xs, ts)); t1 = float(np.interp(X1, xs, ts))
            self.trills.append((X0, X1, to_py(by0) - 0.4 * self.gap_px, to_py(by1) + 0.4 * self.gap_px, t0, t1))
        # loudness envelope (per video frame)
        x = read_audio(T["video"]); sr = 22050
        hop = int(sr / T["fps"]); n = len(x) // hop
        rms = np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(1) + 1e-12)
        db = 20 * np.log10(rms)
        lo, hi = np.percentile(db, 20), np.percentile(db, 98)
        self.loud = np.clip((db - lo) / max(1e-6, hi - lo), 0, 1)
        # precomputed shapes
        gp = self.gap_px
        self.glow_patch = gauss_patch(1.6 * gp, 1.3 * gp)
        self.tint_patch = gauss_patch(0.75 * gp, 0.6 * gp)
        self.col_base = hexrgb(a.ink_color); self.col_gold = hexrgb(a.glow_color); self.col_bg = hexrgb(a.band_color)
        self.col_line = hexrgb(a.line_color)
        # edge fade and played-dim, per screen column
        cols = np.arange(W, dtype=np.float32)
        e = a.edge_fade * W
        self.edge = np.clip(cols / e, 0, 1) ** 1.5 * np.clip((W - 1 - cols) / e, 0, 1) ** 1.5
        self.dim = a.played_dim + (1 - a.played_dim) / (1 + np.exp(-(cols - self.px + 6) / 18.0))
        # vertical alpha of the band
        rr = np.arange(self.LH, dtype=np.float32)
        self.bg_alpha = np.where(rr < self.panel_bot, a.band_opacity,
                                 a.band_opacity * np.clip(1 - (rr - self.panel_bot) / self.fade_h, 0, 1) ** 2)
        self.title_img = self.make_title() if a.title else None
        # speed: only the rows around the score change from frame to frame; the rest (dark band, fade, empty title area)
        # is the same in every frame, so it is built once and each frame computes just the live block
        margin = int(5 * self.gap_px) + 2
        self.dy0 = max(0, self.ink_y - margin)
        self.dy1 = min(self.LH, self.ink_y + self.ink.shape[0] + margin)
        self.base = np.zeros((self.LH, W, 4), np.uint8)
        self.base[..., :3] = np.clip(self.col_bg, 0, 255).astype(np.uint8)
        self.base[..., 3] = np.clip(self.bg_alpha * 255, 0, 255).astype(np.uint8)[:, None]

    def make_title(self):
        a = self.a; W = self.W
        img = Image.new("RGBA", (W, self.title_h), (0, 0, 0, 0))
        fdir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        try:
            f1 = ImageFont.truetype(os.path.join(fdir, "georgia.ttf"), int(0.0215 * self.H))
            f2 = ImageFont.truetype(os.path.join(fdir, "georgiai.ttf"), int(0.0175 * self.H))
        except OSError:
            f1 = f2 = ImageFont.load_default()
        d = ImageDraw.Draw(img)
        y = int(0.03 * self.H)
        for text, font, col in ((a.title, f1, tuple(int(v) for v in self.col_base)),
                                (a.subtitle, f2, tuple(int(v) for v in self.col_gold))):
            if not text:
                continue
            w = d.textlength(text, font=font)
            d.text(((W - w) / 2, y), text, font=font, fill=col + (255,))
            y += int(font.size * 1.5)
        shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        alpha = img.split()[3].filter(ImageFilter.GaussianBlur(10))
        shadow.putalpha(alpha.point(lambda v: min(255, int(v * 2.2))))
        out = Image.new("RGBA", img.size, (0, 0, 0, 0)); out.alpha_composite(shadow); out.alpha_composite(img)
        return np.asarray(out, np.float32) / 255.0

    def scroll(self, t):
        ts, xs = self.ts, self.xs
        if t <= ts[0]:
            return xs[0]                     # hold: the first note sits on the playhead until it sounds
        if t >= ts[-1]:
            return xs[-1] + (t - ts[-1]) * (xs[-1] - xs[-2]) / max(1e-3, ts[-1] - ts[-2])
        return float(np.interp(t, ts, xs))

    def frame(self, t):
        a = self.a; W, LH = self.W, self.LH
        d0, d1 = self.dy0, self.dy1; H = d1 - d0                      # the live block of rows
        off = int(round(self.scroll(t) - self.px))                    # strip column under screen column 0
        ink = np.zeros((H, W), np.float32)
        h_s = self.ink.shape[0]; iy = self.ink_y - d0
        lo, hi = max(0, off), min(self.ink.shape[1], off + W)
        if hi > lo:
            ink[iy:iy + h_s, lo - off:hi - off] = self.ink[:, lo:hi]
        tint = np.zeros((H, W), np.float32); glow = np.zeros((H, W), np.float32)
        gp, gr, gy = self.glow_patch; tp, tr, ty = self.tint_patch
        for hx, hy, ta, small in self.heads:
            dt = t - ta
            if dt < -0.05 or dt > 1.6:
                continue
            I = (1 - abs(dt) / 0.05) if dt < 0 else np.exp(-dt / a.decay)
            if small:
                I *= 0.7
            sx = hx - off
            if -40 < sx < W + 40:
                add_patch(tint, tp, tr, ty, sx, hy - d0, I)
                add_patch(glow, gp, gr, gy, sx, hy - d0, 0.55 * I)
        for X0, X1, Y0, Y1, t0, t1 in self.trills:                   # shimmer along the wavy line
            if not (t0 - 0.2 < t < t1 + 0.3):
                continue
            env = min(1.0, (t - t0 + 0.2) / 0.3, (t1 + 0.3 - t) / 0.4)
            span = X1 - X0
            u = X0 + ((t - t0) * a.shimmer_speed * self.gap_px) % (span + 6 * self.gap_px) - 3 * self.gap_px
            cols = np.arange(W, dtype=np.float32) + off
            band = np.exp(-0.5 * ((cols - u) / (2.2 * self.gap_px)) ** 2) * ((cols >= X0) & (cols <= X1))
            r0, r1 = int(max(0, Y0 - d0)), int(min(H, Y1 - d0))
            tint[r0:r1] += 0.9 * env * band[None, :]
            glow[r0:r1] += 0.18 * env * band[None, :]
        np.clip(tint, 0, 1, out=tint); np.clip(glow, 0, 1, out=glow)
        bright = (self.dim * self.edge)[None, :]
        inkA = ink * np.maximum(bright, tint * self.edge[None, :])
        col = self.col_base[None, None, :] * (1 - tint[..., None]) + self.col_gold[None, None, :] * tint[..., None]
        # playhead with a pulse
        fi = min(len(self.loud) - 1, int(t * self.T["fps"]))
        recent = self.at[(self.at <= t) & (self.at > t - 1.0)]
        pulse = float(np.clip(np.exp(-(t - recent) / 0.28).sum(), 0, 1)) if len(recent) else 0.0
        P = (0.25 + 0.75 * pulse) * (0.45 + 0.55 * self.loud[fi])
        cols = np.arange(W, dtype=np.float32)
        pg = np.exp(-0.5 * ((cols - self.px) / (0.55 * self.gap_px)) ** 2)
        y_a, y_b = iy - int(0.6 * self.gap_px), iy + h_s + int(0.6 * self.gap_px)
        vmask = np.zeros(H, np.float32); vmask[max(0, y_a):y_b] = 1
        vmask = np.convolve(vmask, np.ones(9) / 9, mode="same")
        glow += (0.10 + 0.35 * P) * pg[None, :] * vmask[:, None]
        line = (np.abs(cols - self.px) <= a.line_width / 2).astype(np.float32)[None, :] * vmask[:, None]
        # compose: background, glow, ink, line
        bgA = self.bg_alpha[d0:d1, None] * np.ones((1, W), np.float32)
        rgb = np.broadcast_to(self.col_bg, (H, W, 3)).astype(np.float32).copy()
        gA = np.clip(glow * 0.75, 0, 1)[..., None]
        rgb = rgb * (1 - gA) + self.col_gold * gA
        iA = np.clip(inkA, 0, 1)[..., None]
        rgb = rgb * (1 - iA) + col * iA
        lA = (line * (0.75 + 0.25 * P))[..., None]
        rgb = rgb * (1 - lA) + self.col_line * lA
        A = np.clip(np.maximum(bgA, np.maximum(iA[..., 0], gA[..., 0])), 0, 1)
        out = self.base.copy()
        out[d0:d1, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
        out[d0:d1, :, 3] = (A * 255).astype(np.uint8)
        if a.fade_out:                                              # the band fades away towards the end
            f0 = a.fade_out[0]; f1 = a.fade_out[1] if len(a.fade_out) > 1 else self.T["duration"]
            m = float(np.clip((f1 - t) / max(1e-3, f1 - f0), 0.0, 1.0))
            if m < 1.0:
                out[..., 3] = (out[..., 3] * m).astype(np.uint8)
        if self.title_img is not None:
            fade = float(np.clip(min((t - a.title_in) / 0.8, (a.title_out - t) / 0.9), 0, 1))
            if fade > 0:
                ti = self.title_img; y0 = self.panel_bot + self.fade_h; y1 = y0 + ti.shape[0]
                ta_ = ti[..., 3:4] * fade
                seg = out[y0:y1].astype(np.float32)
                seg[..., :3] = seg[..., :3] * (1 - ta_) + ti[..., :3] * 255 * ta_
                seg[..., 3] = np.maximum(seg[..., 3], ta_[..., 0] * 255)
                out[y0:y1] = seg.astype(np.uint8)
        return out


def draw_safe_zones(img):
    W, H = img.size
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0)); d = ImageDraw.Draw(ov)
    red = (255, 40, 40, 70)
    d.rectangle((0, 0, W, int(0.095 * H)), fill=red)                  # header: "Reels", camera
    d.rectangle((int(0.86 * W), int(0.47 * H), W, int(0.86 * H)), fill=red)   # like / comment / share column
    d.rectangle((0, int(0.80 * H), W, H), fill=red)                   # caption, music, account name
    img.alpha_composite(ov)
    return img


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("work"); ap.add_argument("out")
    ap.add_argument("--still", type=float); ap.add_argument("--clip", type=float, nargs=2)
    ap.add_argument("--safe-zones", action="store_true")
    ap.add_argument("--safe-top", type=float, default=0.10, help="score starts this far down (share of height)")
    ap.add_argument("--band-height", type=float, default=0.22)
    ap.add_argument("--fill", type=float, default=0.94, help="how much of the band height the score uses")
    ap.add_argument("--playhead", type=float, default=0.30)
    ap.add_argument("--band-color", default="#111115"); ap.add_argument("--band-opacity", type=float, default=0.94)
    ap.add_argument("--ink-color", default="#ede6d6"); ap.add_argument("--glow-color", default="#f2c46b")
    ap.add_argument("--line-color", default="#f7e7c4"); ap.add_argument("--line-width", type=float, default=3)
    ap.add_argument("--played-dim", type=float, default=0.55)
    ap.add_argument("--edge-fade", type=float, default=0.08)
    ap.add_argument("--decay", type=float, default=0.45, help="seconds for a lit note to fade")
    ap.add_argument("--shimmer-speed", type=float, default=14.0, help="staff spaces per second")
    ap.add_argument("--title", default=""); ap.add_argument("--subtitle", default="")
    ap.add_argument("--title-in", type=float, default=0.3); ap.add_argument("--title-out", type=float, default=4.5)
    ap.add_argument("--fade-out", type=float, nargs="+", default=None, metavar="SECONDS",
                    help="fade the whole score band to nothing: START [END] in video seconds (END defaults to the end of the video)")
    ap.add_argument("--grade", default="nocturne", help="colour grade of the footage: nocturne, none, or an ffmpeg filter chain")
    ap.add_argument("--crf", type=int, default=17)
    ap.add_argument("--preset", default="medium", help="x264 speed/size trade-off (veryfast ... slow)")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1), help="pieces rendered in parallel (1 = off)")
    a = ap.parse_args()
    F = FX(a.work, a); T = F.T
    ffmpeg = find_tool("ffmpeg")
    if a.still is not None:
        r = subprocess.run([ffmpeg, "-v", "error", "-ss", str(a.still), "-i", T["video"], "-frames:v", "1",
                            "-vf", GRADES.get(a.grade, a.grade), "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True)
        bg = Image.open(io.BytesIO(r.stdout)).convert("RGBA")
        bg.alpha_composite(Image.fromarray(F.frame(a.still), "RGBA"), (0, 0))
        if a.safe_zones:
            bg = draw_safe_zones(bg)
        bg.convert("RGB").save(a.out); print("still ->", a.out); return
    t0, t1 = (a.clip if a.clip else (0.0, T["duration"]))
    fps = T["fps"]; i0 = int(round(t0 * fps)); i1 = int(round(t1 * fps))
    n = i1 - i0
    jobs = max(1, min(a.jobs, n // 90))
    if jobs <= 1:
        render_chunk(F, a, i0, i1, a.out, with_audio=True)
        print(f"done: {n} frames -> {a.out}")
        return
    import multiprocessing as mp, tempfile, shutil, time
    tmp = tempfile.mkdtemp(prefix="fx_chunks_")
    bounds = [i0 + (n * k) // jobs for k in range(jobs + 1)]
    work = [(a.work, vars(a), bounds[k], bounds[k + 1], os.path.join(tmp, f"part{k:02d}.mp4")) for k in range(jobs)]
    t_start = time.time()
    print(f"rendering {n} frames in {jobs} parallel pieces ...", flush=True)
    with mp.get_context("spawn").Pool(jobs) as pool:
        done = 0
        for _ in pool.imap_unordered(_worker, work):
            done += 1
            print(f"  piece {done}/{jobs} finished ({time.time() - t_start:.0f}s)", flush=True)
    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w") as fh:
        for w in work:
            fh.write("file '" + w[4].replace(chr(92), "/") + "'" + chr(10))
    cmd = [ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
           "-ss", f"{i0 / fps:.4f}", "-t", f"{n / fps:.4f}", "-i", T["video"],
           "-map", "0:v", "-map", "1:a?", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart", "-shortest", a.out]
    if subprocess.run(cmd).returncode:
        sys.exit("joining the pieces failed (pieces are in %s)" % tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"done: {n} frames -> {a.out}  ({time.time() - t_start:.0f}s)")


def render_chunk(F, a, i0, i1, out, with_audio):
    """frames i0..i1 of the video with the band laid over them, written to `out` (audio only if with_audio)."""
    T = F.T; fps = T["fps"]; ffmpeg = find_tool("ffmpeg")
    # cut by FRAME NUMBER and restamp to exactly N/fps, so footage and overlay can never end up a frame apart
    # (cutting by -ss/-t on a phone video with uneven timestamps dropped the overlay for one frame at piece edges)
    vf = (f"[0:v]trim=start_frame={i0}:end_frame={i1},setpts=N/({fps}*TB),{GRADES.get(a.grade, a.grade)}[g];"
          f"[g][1:v]overlay=0:0:eof_action=pass,format=yuv420p[v]")
    cmd = [ffmpeg, "-v", "error", "-y", "-i", T["video"],
           "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{F.W}x{F.LH}", "-framerate", T["fps_str"], "-i", "-",
           "-filter_complex", vf, "-map", "[v]"]
    if with_audio:
        cmd += ["-map", "0:a?", "-af", f"atrim=start={i0 / fps:.4f}:end={i1 / fps:.4f},asetpts=PTS-STARTPTS",
                "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]
    cmd += ["-frames:v", str(i1 - i0), "-c:v", "libx264", "-preset", a.preset, "-crf", str(a.crf),
            "-g", "30", "-movflags", "+faststart", out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(i0, i1):
        p.stdin.write(F.frame(i / fps).tobytes())
        if with_audio and (i - i0) % 60 == 0:
            print(f"  frame {i - i0}/{i1 - i0}", flush=True)
    p.stdin.close()
    if p.wait():
        sys.exit("ffmpeg failed while writing " + out)


def _worker(job):
    work, argd, i0, i1, out = job
    a = argparse.Namespace(**argd)
    F = FX(work, a)
    render_chunk(F, a, i0, i1, out, with_audio=False)
    return out


if __name__ == "__main__":
    main()

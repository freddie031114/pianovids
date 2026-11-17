#!/usr/bin/env python3
"""scan_flicker.py: find frames where the score band disappears or jumps for a moment (a "flicker").

  python scan_flicker.py VIDEO [--top 150] [--band-y 520 --band-h 120] [--threshold 2.0]

Every frame is measured twice, in strips of the overlay area that are empty of notes (above the staves and just under
them), so only the band itself moves them. A frame whose value jumps away from BOTH neighbours is reported with its
frame number and time. Also checks that the frame times are steady (1/fps) from start to end, which is what goes wrong
when separately encoded pieces are copy-joined. Run it on every finished render before telling the user it is done.
Exit code 0 = clean, 1 = problems found. (With --fade-out the band is meant to vanish at the end: that is a slow
change, not a one-frame jump, so it is not reported.)
"""
import argparse, os, re, subprocess, sys, tempfile
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import find_tool  # noqa: E402


def strip_means(video, y, h, tool):
    # the measurement file gets a plain relative name inside a temp folder: a Windows path (C:\...) breaks ffmpeg's
    # filter syntax because of the colon
    with tempfile.TemporaryDirectory() as d:
        flt = f"crop=iw:{h}:0:{y},signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=m.txt"
        r = subprocess.run([tool, "-v", "error", "-i", os.path.abspath(video), "-vf", flt, "-f", "null", "-"],
                           capture_output=True, text=True, cwd=d)
        if r.returncode:
            sys.exit("ffmpeg failed: " + r.stderr[-400:])
        return np.array([float(m) for m in re.findall(r"YAVG=([\d.]+)", open(os.path.join(d, "m.txt")).read())])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--top", type=int, default=150, help="height of the empty strip at the very top of the band")
    ap.add_argument("--band-y", type=int, default=520); ap.add_argument("--band-h", type=int, default=120)
    ap.add_argument("--threshold", type=float, default=2.0)
    a = ap.parse_args()
    ff = find_tool("ffmpeg"); fp = find_tool("ffprobe")
    t = strip_means(a.video, 0, a.top, ff); b = strip_means(a.video, a.band_y, a.band_h, ff)
    n = min(len(t), len(b)); bad = []
    for i in range(n):
        nb = [j for j in (i - 1, i + 1) if 0 <= j < n]
        dt = abs(t[i] - np.mean([t[j] for j in nb])); db = abs(b[i] - np.mean([b[j] for j in nb]))
        if dt > a.threshold or db > a.threshold:
            bad.append(i)
    # a one-frame dropout shows as a trio (before, the frame, after); report the middle one
    mids = [i for i in bad if (i - 1 in bad and i + 1 in bad) or (i - 1 not in bad and i + 1 not in bad)]
    out = subprocess.run([fp, "-v", "error", "-select_streams", "v", "-show_entries", "frame=best_effort_timestamp_time",
                          "-of", "csv=p=0", os.path.abspath(a.video)], capture_output=True, text=True).stdout.split()
    ts = np.array([float(x.strip(",")) for x in out if x.strip(",")]); steps = np.diff(ts)
    fps = float((len(ts) - 1) / (ts[-1] - ts[0])) if len(ts) > 1 else 30.0
    odd = [i for i, s in enumerate(steps) if abs(s - 1 / fps) > 0.35 / fps]
    print(f"{n} frames scanned")
    print("band dropouts/jumps at frames:", [(int(i), round(i / fps, 2)) for i in sorted(set(mids))] or "none")
    print("irregular frame times at frames:", [(int(i), round(float(ts[i]), 3)) for i in odd[:15]] or "none")
    sys.exit(1 if (mids or odd) else 0)


if __name__ == "__main__":
    main()

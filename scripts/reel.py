#!/usr/bin/env python3
"""reel.py: the whole piano / Reels pipeline.

  python reel.py make   VIDEO SCORE --from-bar 140 --to-bar 171 [--from-beat 1] [--first-t 0] [--out FILE]
                        [--title "..." --subtitle "..."] [--work DIR] [--stretch 1.2] [--pedal]
                        [render options, e.g. --fade-out 45   (band fades away from 45 s to the end)]
      ONE GO: engrave -> time against the audio -> proof pictures -> render in parallel pieces -> flicker scan.
      Start it in the background and look at WORK/proof.png as soon as it exists; if it is wrong, stop and fix.
      Output defaults to VIDEO_reel.mp4 next to the video.

  python reel.py prep   (same arguments, no --out)   everything up to the proof pictures (about 40 s)
  python reel.py render WORK OUT.mp4 [render options]   render + flicker scan on a prepared WORK folder
  python reel.py clip   WORK OUT.mp4 --clip 25.5 31     a few seconds only, to check a look change quickly

Writes WORK/proof.png (opening, busiest passage, ending), WORK/proof_safe.png (app UI areas shaded) and prints a
"start check" for the first note. Every other script is still usable on its own. Use the Python doctor.py prints.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def sh(script, *args, check=True):
    cmd = [sys.executable, os.path.join(HERE, script)] + [str(a) for a in args]
    print("  $", script, " ".join(str(a) for a in args)[:150], flush=True)
    r = subprocess.run(cmd)
    if check and r.returncode:
        sys.exit(f"{script} failed (exit {r.returncode})")
    return r.returncode


def auto_title(score):
    """title and composer read from the MusicXML itself (movement-title / work-title, creator type=composer)."""
    from trim_score import load
    root = load(score).getroot()
    title = (root.findtext("movement-title") or root.findtext("work/work-title") or "").strip()
    composer = ""
    for c in root.iter("creator"):
        if c.get("type") == "composer" and (c.text or "").strip():
            composer = c.text.strip()
            break
    if not title:
        title = os.path.splitext(os.path.basename(score))[0].replace("-", " ").replace("_", " ").title()
    return title, composer


def prep(a):
    work = a.work or os.path.splitext(a.video)[0] + "-reel"
    os.makedirs(work, exist_ok=True)
    if a.title == "auto":                         # --title auto: take them from the score file
        a.title, sub = auto_title(a.score)
        a.subtitle = a.subtitle or sub
        print(f"title card: {a.title!r} / {a.subtitle!r}")
    print("1/4 engraving the score cut")
    eg = ["--from-bar", a.from_bar, "--to-bar", a.to_bar, "--stretch", a.stretch]
    if a.from_beat != 1:
        eg += ["--from-beat", a.from_beat]
    if a.width:
        eg += ["--width", a.width]
    if a.pedal:
        eg += ["--pedal"]
    sh("engrave_grand.py", a.score, work, *eg)
    print("2/4 timing it against the audio (about a minute)")
    al = []
    if a.first_t is not None:
        al += ["--first-t", a.first_t]
    sh("align_grand.py", a.video, work, a.score, *al)
    with open(os.path.join(work, "reel.json"), "w") as fh:
        json.dump({"title": a.title, "subtitle": a.subtitle, "video": os.path.abspath(a.video)}, fh)
    print("3/4 proof pictures")
    import numpy as np
    from PIL import Image
    T = json.load(open(os.path.join(work, "timeline.json")))
    ts = np.array([n["t"] for n in T["attacks"]])
    dur = T["duration"]
    busiest = max(np.arange(1.0, max(1.1, ts.max() - 1.0), 0.5), key=lambda c: ((ts > c - 1.5) & (ts < c + 1.5)).sum())
    shots = [("open", min(max(0.3, (a.first_t or 0) + 0.2), dur)), ("busy", float(busiest)), ("end", max(0.0, min(dur, ts.max()) - 0.5))]
    opts = ["--title", a.title, "--subtitle", a.subtitle] if a.title else []
    crops = []
    for name, t in shots:
        out = os.path.join(work, f"proof_{name}.png")
        sh("render_fx.py", work, out, "--still", f"{t:.2f}", *opts)
        im = Image.open(out).convert("RGB")
        crops.append(im.crop((0, int(0.09 * im.size[1]), im.size[0], int(0.36 * im.size[1]))))
    w, h = crops[0].size
    sheet = Image.new("RGB", (w, 3 * h + 20), "#444")
    for i, c in enumerate(crops):
        sheet.paste(c, (0, i * (h + 10)))
    sheet.resize((w * 2 // 3, (3 * h + 20) * 2 // 3), Image.LANCZOS).save(os.path.join(work, "proof.png"))
    sh("render_fx.py", work, os.path.join(work, "proof_safe.png"), "--still", "2.0", "--safe-zones", *opts)
    print("4/4 done")
    print(f"\nWORK={work}\nLOOK AT: {os.path.join(work, 'proof.png')}  (and proof_safe.png)")
    return work


def render(a, rest, clip=False):
    cfg = {}
    p = os.path.join(a.work, "reel.json")
    if os.path.exists(p):
        cfg = json.load(open(p))
    opts = list(rest)
    if "--title" not in opts and cfg.get("title"):
        opts += ["--title", cfg["title"], "--subtitle", cfg.get("subtitle", "")]
    sh("render_fx.py", a.work, a.out, *opts)
    if clip:
        return
    print("checking for flicker and timing problems")
    code = sh("scan_flicker.py", a.out, check=False)
    probe = subprocess.run([sys.executable, "-c",
                            "import sys; sys.path.insert(0, %r); import common; print(common.probe(sys.argv[1]))" % HERE, a.out],
                           capture_output=True, text=True)
    print(probe.stdout.strip())
    print("RESULT:", "clean, safe to deliver" if code == 0 else "PROBLEMS FOUND - do not deliver; see above")
    sys.exit(code)


def add_prep_args(p):
    p.add_argument("video"); p.add_argument("score")
    p.add_argument("--from-bar", type=int, required=True); p.add_argument("--to-bar", type=int, required=True)
    p.add_argument("--from-beat", type=float, default=1.0)
    p.add_argument("--first-t", type=float, default=None)
    p.add_argument("--work", default=None)
    p.add_argument("--title", default=""); p.add_argument("--subtitle", default="")
    p.add_argument("--stretch", type=float, default=1.2); p.add_argument("--width", type=float, default=0)
    p.add_argument("--pedal", action="store_true")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    add_prep_args(sub.add_parser("prep"))
    m = sub.add_parser("make")
    add_prep_args(m)
    m.add_argument("--out", default=None)
    for name in ("render", "clip"):
        q = sub.add_parser(name, add_help=False)
        q.add_argument("work"); q.add_argument("out")
    a, rest = ap.parse_known_args()
    if a.cmd == "make":
        work = prep(a)
        out = a.out or os.path.splitext(a.video)[0] + "_reel.mp4"
        print("\nrendering ->", out, "(extra options such as --fade-out 45 go to the renderer)", flush=True)
        render(argparse.Namespace(work=work, out=out), rest, clip="--clip" in rest)
    elif a.cmd == "prep":
        prep(a)
    else:
        render(a, rest, clip=(a.cmd == "clip"))


if __name__ == "__main__":
    main()

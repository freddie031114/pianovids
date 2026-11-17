#!/usr/bin/env python3
"""engrave_grand.py: draw a FULL score cut (all staves, as engraved) on one long line -> WORK/strip.png + notes.json.

  python engrave_grand.py SCORE WORK --from-bar 67 --to-bar 75 [--start-width 20]

Keeps whole bars with everything visible (trill marks and lines, grace notes, slurs, pedal, text). Hidden playback-only
notes are dropped. Note positions come from MuseScore's own position export (.spos), one per beat position, so chords,
several voices and two staves are fine. Then: align_grand.py, render_fx.py (reel.py chains all of it).
"""
import argparse, os, re, sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for extra in ("scripts",):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), extra))
from common import die, find_mscore, run, save_json  # noqa: E402
from engrave import write_style, outputs, nums  # noqa: E402
from build_grand import build  # noqa: E402

DPI = 360
STEP = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def read_ticks(xml):
    """every beat position that has a visible note or rest: (bar index, q, top midi or 0)."""
    part = ET.parse(xml).getroot().find("part")
    div, beats, btype = 1, 4, 4
    ticks = {}; base = 0.0; lens = []
    for bi, m in enumerate(part.findall("measure")):
        pos = 0; prev_on = 0; maxpos = 0
        for e in m:
            if e.tag == "attributes":
                if e.findtext("divisions"): div = int(e.findtext("divisions"))
                if e.find("time/beats") is not None:
                    beats = int(e.findtext("time/beats")); btype = int(e.findtext("time/beat-type"))
            elif e.tag in ("backup", "forward"):
                pos += (-1 if e.tag == "backup" else 1) * int(e.findtext("duration"))
                maxpos = max(maxpos, pos)
            elif e.tag == "note" and e.find("grace") is None:
                d = int(e.findtext("duration") or 0); chord = e.find("chord") is not None
                on = prev_on if chord else pos
                if not chord:
                    prev_on = pos; pos += d
                maxpos = max(maxpos, pos)
                p = e.find("pitch")
                midi = 0
                if p is not None:
                    midi = 12 * (int(p.findtext("octave")) + 1) + STEP[p.findtext("step")] + int(float(p.findtext("alter") or 0))
                q = round(base + on / div, 4)
                ticks.setdefault(q, [bi, 0])
                ticks[q][1] = max(ticks[q][1], midi)
        mlen = maxpos / div if m.get("implicit") == "yes" else 4.0 * beats / btype   # a pick-up bar is as long as its contents
        lens.append(mlen); base += mlen
    qs = sorted(ticks)
    merged = []                       # positions a hair apart (playback nudges) are one beat position
    for q in qs:
        if merged and q - merged[-1][1] < 0.012:
            merged[-1][2] = max(merged[-1][2], ticks[q][1]); continue
        merged.append([ticks[q][0], q, ticks[q][1]])
    return merged, base, lens


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("score"); ap.add_argument("out")
    ap.add_argument("--from-bar", type=int, required=True); ap.add_argument("--to-bar", type=int, required=True)
    ap.add_argument("--part", type=int, default=1)
    ap.add_argument("--from-beat", type=float, default=1.0, help="start part-way into the first bar (3 = beat 3): the rest of that bar becomes a pick-up")
    ap.add_argument("--start-width", type=float, default=0, help="page width in inches to start from (it grows until the music fits on one line)")
    ap.add_argument("--pedal", action="store_true", help="keep the pedal marks (left out by default)")
    ap.add_argument("--width", type=float, default=0, help="exact page width in inches (skips the search; used to reproduce an earlier spacing)")
    ap.add_argument("--stretch", type=float, default=1.0, help="more than 1 = wider note spacing than the tightest fit")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    xml = os.path.join(a.out, "line.musicxml")
    build(a.score, xml, a.from_bar, a.to_bar, a.part, a.pedal, a.from_beat)
    ticks, total_q, lens = read_ticks(xml)
    starts = [sum(lens[:i]) for i in range(len(lens))]
    nstaff_lines = 5 * int(ET.parse(xml).getroot().findtext("part/measure/attributes/staves") or 1)
    mscore = find_mscore(); style = os.path.join(a.out, "strip.mss")
    width, height = (a.start_width or max(10.0, 2.2 * (a.to_bar - a.from_bar + 1))), 8.0
    for _ in range(1 if a.width else 40):
        if a.width:
            width = a.width
        write_style(style, width, height)
        for f in outputs(a.out, "strip", "svg"):
            os.remove(os.path.join(a.out, f))
        run([mscore, "-S", style, "-o", os.path.join(a.out, "strip.svg"), xml], timeout=300)
        pages = outputs(a.out, "strip", "svg")
        svg = open(os.path.join(a.out, pages[0])).read() if pages else ""
        if len(pages) == 1 and svg.count('class="StaffLines"') == nstaff_lines:
            break
        width *= 1.07
    else:
        die("MuseScore kept breaking the music over several lines")
    if a.stretch > 1.0 and not a.width:
        width *= a.stretch
        write_style(style, width, height)
        for f in outputs(a.out, "strip", "svg"):
            os.remove(os.path.join(a.out, f))
        run([mscore, "-S", style, "-o", os.path.join(a.out, "strip.svg"), xml], timeout=300)
        pages = outputs(a.out, "strip", "svg")
    os.replace(os.path.join(a.out, pages[0]), os.path.join(a.out, "strip.svg"))
    for ext, extra in (("png", ["-r", str(DPI)]), ("spos", []), ("mpos", [])):
        for f in outputs(a.out, "strip", ext):
            os.remove(os.path.join(a.out, f))
        run([mscore, "-S", style] + extra + ["-o", os.path.join(a.out, "strip." + ext), xml], timeout=300)
        got = outputs(a.out, "strip", ext)
        if not got:
            die("MuseScore did not write the ." + ext)
        os.replace(os.path.join(a.out, got[0]), os.path.join(a.out, "strip." + ext))
    t = open(os.path.join(a.out, "strip.svg")).read()
    vb = nums(re.search(r'viewBox="([^"]+)"', t).group(1))
    lines = [nums(m.group(1)) for m in re.finditer(r'<polyline class="StaffLines"[^>]*points="([^"]+)"', t)]
    ys = sorted({round(l[1], 2) for l in lines})
    gap = ys[1] - ys[0]
    barlines = sorted({round(nums(m.group(1))[0], 1) for m in re.finditer(r'<polyline class="BarLine"[^>]*points="([^"]+)"', t)})
    sp = open(os.path.join(a.out, "strip.spos")).read()
    seg = {int(m.group(1)): float(m.group(2)) for m in re.finditer(r'<element id="(\d+)" x="([-+\d.eE]+)"', sp)}   # long strips print big x in scientific notation (1.2e+06)
    ev = [(int(m.group(1)), int(m.group(2))) for m in re.finditer(r'<event elid="(\d+)" position="(\d+)"', sp)]
    mp = open(os.path.join(a.out, "strip.mpos")).read()
    bar_ms = [int(v) for v in re.findall(r'<event elid="\d+" position="(\d+)"', mp)]
    k = 12.0                                 # .spos units per SVG unit; MuseScore plays the file at 120 = 500 ms per quarter
    by_bar = {}
    for elid, ms in ev:
        bi = max(i for i, b in enumerate(bar_ms) if ms >= b - 1)
        by_bar.setdefault(bi, []).append((elid, ms - bar_ms[bi]))
    attacks = []
    for bi in sorted(by_bar):
        items = by_bar[bi]
        bar_q = lens[bi]
        length = (bar_ms[bi + 1] - bar_ms[bi]) if bi + 1 < len(bar_ms) else bar_q * 500
        if abs(length - bar_q * 500) <= 5:
            offs = [ms / 500.0 for _, ms in items]
        else:                                # a fermata stretched this bar: take the written positions instead
            mine = [q - starts[bi] for b, q, _ in ticks if b == bi]
            if len(mine) == len(items):
                offs = mine
            else:
                offs = [ms / length * bar_q for _, ms in items]
        for (elid, _), o in zip(items, offs):
            attacks.append({"q": round(starts[bi] + o, 3), "midi": 0, "x": round(seg[elid] / k + 0.6 * gap, 2), "bar": bi})
    attacks = [n for n in attacks if n["q"] < total_q - 1e-6]   # filler positions past the last bar line
    attacks.sort(key=lambda n: n["q"])
    for i, n in enumerate(attacks):
        n["ql"] = round((attacks[i + 1]["q"] if i + 1 < len(attacks) else total_q) - n["q"], 4)
    for n in attacks:                        # top written pitch at that position, when the file has one there
        near = [t for t in ticks if abs(t[1] - n["q"]) < 0.02]
        if near:
            n["midi"] = max(t[2] for t in near)
    xs = [n["x"] for n in attacks]
    if any(b <= a_ for a_, b in zip(xs, xs[1:])):
        die("positions are not left-to-right; cannot build the scroll")
    from engrave import _png_width
    save_json({"strip_png": "strip.png", "svg_units_per_png_px": vb[2] / _png_width(os.path.join(a.out, "strip.png")),
               "geometry": {"view_w": vb[2], "view_h": vb[3], "staff_top": ys[0], "staff_bottom": ys[-1], "gap": gap,
                            "x0": min(l[0] for l in lines), "x1": max(l[2] for l in lines), "barlines": barlines},
               "tempo_qpm": None, "bars": [{"q": starts[i]} for i in range(len(starts))],
               "attacks": attacks, "heads": attacks, "from_bar": a.from_bar, "to_bar": a.to_bar, "from_beat": a.from_beat},
              os.path.join(a.out, "notes.json"))
    print(f"engraved bars {a.from_bar}..{a.to_bar} on one line ({width:.1f} in wide), {len(attacks)} beat positions -> {a.out}")


if __name__ == "__main__":
    main()

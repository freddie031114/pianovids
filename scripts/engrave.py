#!/usr/bin/env python3
"""engrave.py: score file -> one long line of sheet music (strip.png) + where every note sits on it (notes.json).

  python engrave.py SCORE OUT_DIR [--part 1] [--clef treble|bass|alto|tenor] [--bar-width 1.1]

SCORE can be .musicxml / .xml / .mxl / .mscz / .mid (MuseScore converts the others to MusicXML first).
Only ONE staff is drawn. A score with several instruments: pick one with --part (1 = top instrument).
"""
import argparse, os, re, sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import die, find_mscore, run, save_json  # noqa: E402

STEP = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
CLEFS = {"treble": ("G", "2"), "bass": ("F", "4"), "alto": ("C", "3"), "tenor": ("C", "4")}
DPI = 360  # MuseScore SVG units are 1/360 inch, so a 360 dpi PNG lines up 1:1 with the SVG


def to_musicxml(score, out):
    ext = os.path.splitext(score)[1].lower()
    if ext in (".musicxml", ".xml"):
        return score
    dst = os.path.join(out, "converted.musicxml")
    run([find_mscore(), "-o", dst, score], timeout=300)
    return dst


def strip_ns(root):
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def clean_score(src, dst, part_no, clef):
    """keep ONE part, drop titles/credits/forced line breaks/grace notes, optional clef change."""
    tree = ET.parse(src)
    root = strip_ns(tree.getroot())
    if root.tag != "score-partwise":
        die("only part-wise MusicXML is supported (MuseScore exports this by default)")
    parts = root.findall("part")
    if not parts:
        die("no parts found in the score")
    if not 1 <= part_no <= len(parts):
        die(f"--part {part_no}: the score has {len(parts)} part(s)")
    keep = parts[part_no - 1]
    pl = root.find("part-list")
    for sp in list(pl):
        if sp.tag == "score-part" and sp.get("id") != keep.get("id"):
            pl.remove(sp)
        elif sp.tag == "part-group":
            pl.remove(sp)
    for p in parts:
        if p is not keep:
            root.remove(p)
    for tag in ("work", "movement-title", "identification", "credit", "defaults"):  # "defaults" = the file's own page size
        for el in root.findall(tag):
            root.remove(el)
    for sp in pl.findall("score-part"):
        pn = sp.find("part-name")
        if pn is not None:
            pn.text = ""; pn.set("print-object", "no")
        for t in ("part-abbreviation", "part-name-display", "part-abbreviation-display"):
            for el in sp.findall(t):
                sp.remove(el)
    staves = keep.find("measure/attributes/staves")
    if staves is not None and staves.text and int(staves.text) > 1:
        die("this part is drawn on 2 staves (piano/harp). The basic skill draws ONE staff: export just the line "
            "you play (one staff) from MuseScore, or use a single-line part.")
    for m in keep.findall("measure"):
        for pr in m.findall("print"):
            for a in ("new-system", "new-page"):
                pr.attrib.pop(a, None)
        for n in list(m):
            if n.tag == "note" and n.find("grace") is not None:
                m.remove(n)          # grace notes are left out (they are not timed beats)
        if clef:
            sign, line = CLEFS[clef]
            for c in m.iter("clef"):
                c.find("sign").text = sign
                ln = c.find("line")
                if ln is None:
                    ln = ET.SubElement(c, "line")
                ln.text = line
                oc = c.find("clef-octave-change")
                if oc is not None:
                    c.remove(oc)
    tree.write(dst, encoding="utf-8", xml_declaration=True)
    return keep


def read_notes(part):
    """every printed notehead: onset q (in quarter notes from the start), length, sounding midi, tie continuation, bar."""
    divisions, transpose, pos = 1, 0, 0.0
    heads, bars, tempo = [], [], None
    for bi, m in enumerate(part.findall("measure")):
        bars.append({"number": m.get("number"), "q": pos})
        last_onset = pos
        mstart = pos
        for el in m:
            if el.tag == "attributes":
                d = el.find("divisions")
                if d is not None:
                    divisions = float(d.text)
                tr = el.find("transpose")
                if tr is not None:
                    transpose = int(float(tr.findtext("chromatic", "0"))) + 12 * int(float(tr.findtext("octave-change", "0")))
            elif el.tag == "direction":
                snd = el.find("sound")
                if snd is not None and snd.get("tempo") and tempo is None:
                    tempo = float(snd.get("tempo"))
                pm = el.find("direction-type/metronome/per-minute")
                if pm is not None and tempo is None:
                    try:
                        tempo = float(pm.text)
                    except (TypeError, ValueError):
                        pass
            elif el.tag == "sound" and el.get("tempo") and tempo is None:
                tempo = float(el.get("tempo"))
            elif el.tag == "backup":
                pos -= float(el.findtext("duration", "0")) / divisions
            elif el.tag == "forward":
                pos += float(el.findtext("duration", "0")) / divisions
            elif el.tag == "note":
                if el.find("grace") is not None:
                    continue
                dur = float(el.findtext("duration", "0")) / divisions
                onset = last_onset if el.find("chord") is not None else pos
                if el.find("rest") is None and el.find("pitch") is not None:
                    p = el.find("pitch")
                    midi = 12 * (int(p.findtext("octave")) + 1) + STEP[p.findtext("step")] + int(float(p.findtext("alter", "0")))
                    tie_stop = any(t.get("type") == "stop" for t in el.findall("tie"))
                    heads.append({"q": round(onset, 4), "ql": round(dur, 4), "midi": midi + transpose,
                                  "cont": tie_stop, "bar": bi})
                if el.find("chord") is None:
                    last_onset = pos
                    pos += dur
        # a pickup/short bar keeps its real length; a full bar never ends before its notes
        pos = max(pos, mstart)
    bars.append({"number": "end", "q": pos})
    return heads, bars, tempo


def write_style(path, width_in, height_in):
    with open(path, "w") as fh:
        fh.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<museScore version="4.40"><Style>
<pageWidth>{width_in:.2f}</pageWidth><pageHeight>{height_in:.2f}</pageHeight><pagePrintableWidth>{width_in - 0.4:.2f}</pagePrintableWidth>
<pageEvenLeftMargin>0.2</pageEvenLeftMargin><pageOddLeftMargin>0.2</pageOddLeftMargin>
<pageEvenTopMargin>0.4</pageEvenTopMargin><pageEvenBottomMargin>0.2</pageEvenBottomMargin>
<pageOddTopMargin>0.4</pageOddTopMargin><pageOddBottomMargin>0.2</pageOddBottomMargin>
<pageTwosided>0</pageTwosided><spatium>1.75</spatium><showHeader>0</showHeader><showFooter>0</showFooter>
<showMeasureNumber>1</showMeasureNumber><measureNumberSystem>0</measureNumberSystem>
<enableIndentationOnFirstSystem>0</enableIndentationOnFirstSystem><hideInstrumentNameIfOneInstrument>1</hideInstrumentNameIfOneInstrument>
<lastSystemFillLimit>0</lastSystemFillLimit><createMultiMeasureRests>0</createMultiMeasureRests>
</Style></museScore>""")


def outputs(out, stem, ext):
    return sorted(f for f in os.listdir(out) if re.match(rf"{stem}(-\d+)?\.{ext}$", f))


def engrave_one_line(xml, out, nbars, bar_in):
    """make MuseScore draw everything on ONE line: widen the page until it fits on one page with one staff."""
    mscore = find_mscore()
    style = os.path.join(out, "strip.mss")
    width, height = max(8.0, nbars * bar_in + 1.5), 3.2
    for _ in range(25):
        write_style(style, width, height)
        for f in outputs(out, "strip", "svg"):
            os.remove(os.path.join(out, f))
        run([mscore, "-S", style, "-o", os.path.join(out, "strip.svg"), xml], timeout=300)
        pages = outputs(out, "strip", "svg")
        svg = open(os.path.join(out, pages[0])).read() if pages else ""
        if len(pages) == 1 and svg.count('class="StaffLines"') == 5:
            break
        width *= 1.08
    else:
        die("MuseScore kept breaking the music over several lines")
    os.replace(os.path.join(out, pages[0]), os.path.join(out, "strip.svg"))
    for f in outputs(out, "strip", "png"):
        os.remove(os.path.join(out, f))
    run([mscore, "-S", style, "-r", str(DPI), "-o", os.path.join(out, "strip.png"), xml], timeout=300)
    pngs = outputs(out, "strip", "png")
    if not pngs:
        die("MuseScore did not write a PNG")
    os.replace(os.path.join(out, pngs[0]), os.path.join(out, "strip.png"))
    return os.path.join(out, "strip.svg"), os.path.join(out, "strip.png")


def nums(s):
    return [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", s)]


def svg_geometry(svg_path):
    t = open(svg_path).read()
    vb = nums(re.search(r'viewBox="([^"]+)"', t).group(1))
    lines = []
    for m in re.finditer(r'<polyline class="StaffLines"[^>]*points="([^"]+)"', t):
        v = nums(m.group(1)); lines.append((v[0], v[1], v[2]))
    ys = sorted(l[1] for l in lines)
    gap = (ys[-1] - ys[0]) / 4
    heads = []
    for m in re.finditer(r'<path class="Note"([^>]*)>', t):
        attrs = m.group(1)
        tr = re.search(r'transform="matrix\(([^)]*)\)"', attrs)
        d = re.search(r'\sd="([^"]+)"', attrs)
        a, b, c, dd, e, f = nums(tr.group(1)) if tr else (1, 0, 0, 1, 0, 0)
        if d:  # exact horizontal middle of the notehead glyph
            v = nums(d.group(1))
            xs = [a * v[i] + c * v[i + 1] + e for i in range(0, len(v) - 1, 2)]
            cx = (min(xs) + max(xs)) / 2
        else:
            cx = e + 0.59 * gap
        heads.append((cx, f))
    barlines = sorted({round(nums(m.group(1))[0], 1)
                       for m in re.finditer(r'<polyline class="BarLine"[^>]*points="([^"]+)"', t)})
    return {"view_w": vb[2], "view_h": vb[3], "staff_top": ys[0], "staff_bottom": ys[-1], "gap": gap,
            "x0": min(l[0] for l in lines), "x1": max(l[2] for l in lines), "heads": heads, "barlines": barlines}


def match_heads(notes, heads):
    """the n-th printed note (left to right) gets the n-th notehead; chords take their heads together."""
    if len(notes) != len(heads):
        die(f"matching failed: {len(notes)} notes in the file but {len(heads)} noteheads drawn")
    hs = sorted(heads, key=lambda h: (h[0], h[1]))
    groups = {}
    for n in notes:
        groups.setdefault(n["q"], []).append(n)
    k = 0
    for q in sorted(groups):
        g = groups[q]
        xs = [hs[k + i][0] for i in range(len(g))]
        for n in g:
            n["x"] = round(min(xs), 2)
        k += len(g)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("score"); ap.add_argument("out")
    ap.add_argument("--part", type=int, default=1, help="which instrument (1 = top one)")
    ap.add_argument("--clef", choices=sorted(CLEFS), help="draw in this clef instead of the printed one")
    ap.add_argument("--bar-width", type=float, default=1.1, help="inches per bar (smaller = tighter, more bars on screen)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    src = to_musicxml(os.path.abspath(a.score), a.out)
    clean = os.path.join(a.out, "line.musicxml")
    part = clean_score(src, clean, a.part, a.clef)
    notes, bars, tempo = read_notes(part)
    if not notes:
        die("no notes found in that part")
    svg, png = engrave_one_line(clean, a.out, len(bars) - 1, a.bar_width)
    geo = svg_geometry(svg)
    match_heads(notes, geo["heads"])
    attacks = []
    for q in sorted({n["q"] for n in notes if not n["cont"]}):
        g = [n for n in notes if n["q"] == q and not n["cont"]]
        top = max(g, key=lambda n: n["midi"])
        attacks.append({"q": q, "ql": top["ql"], "midi": top["midi"], "x": top["x"], "bar": top["bar"]})
    save_json({"strip_png": "strip.png", "svg_units_per_png_px": geo["view_w"] / _png_width(png),
               "geometry": {k: v for k, v in geo.items() if k != "heads"}, "tempo_qpm": tempo,
               "bars": bars, "attacks": attacks, "heads": notes}, os.path.join(a.out, "notes.json"))
    print(f"engraved {len(bars) - 1} bars, {len(notes)} noteheads, {len(attacks)} attacks"
          f"{', tempo %g' % tempo if tempo else ', no tempo marking (align.py will measure it)'} -> {a.out}")


def _png_width(png):
    from PIL import Image
    with Image.open(png) as im:
        return im.size[0]


if __name__ == "__main__":
    main()

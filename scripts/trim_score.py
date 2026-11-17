#!/usr/bin/env python3
"""trim_score.py: cut a score down to the bars you play, and to ONE line of ONE staff (for the single-line path).

  python trim_score.py SCORE OUT.musicxml --from-bar 67 --from-beat 3 --to-bar 75 [--staff 1] [--voice 1] [--clef treble]

SCORE can be .musicxml/.xml/.mxl. --from-beat starts part-way into the first bar (beat 3 of bar 67 becomes a pick-up
bar). --staff 1 = right hand, 2 = left hand. --voice picks the melody voice (usually 1). Grace notes are dropped, and so
are slurs/ties/trill lines whose other end was cut away (they make MuseScore refuse the file). For a full piano score
use engrave_grand.py instead; this script is for cutting one staff out for engrave.py.
"""
import argparse, copy, sys, zipfile
import xml.etree.ElementTree as ET

DROP_NOTATIONS = ("slur", "tied", "glissando", "slide", "arpeggiate")


def load(path):
    if path.lower().endswith(".mxl"):
        z = zipfile.ZipFile(path)
        name = None
        if "META-INF/container.xml" in z.namelist():
            c = ET.fromstring(z.read("META-INF/container.xml"))
            for r in c.iter("rootfile"):
                name = r.get("full-path")
                break
        name = name or next(n for n in z.namelist() if n.endswith(".xml") and not n.startswith("META-INF"))
        return ET.ElementTree(ET.fromstring(z.read(name)))
    return ET.parse(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("score"); ap.add_argument("out")
    ap.add_argument("--from-bar", type=int, required=True)
    ap.add_argument("--from-beat", type=float, default=1)
    ap.add_argument("--to-bar", type=int, required=True)
    ap.add_argument("--staff", type=int, default=1)
    ap.add_argument("--voice", default="1")
    ap.add_argument("--part", type=int, default=1)
    ap.add_argument("--clef", choices=["treble", "bass"], default=None)
    a = ap.parse_args()
    tree = load(a.score)
    root = tree.getroot()
    part = root.findall("part")[a.part - 1]
    for p in root.findall("part"):
        if p is not part:
            root.remove(p)
    pl = root.find("part-list")
    for sp in list(pl):
        if sp.tag == "part-group" or (sp.tag == "score-part" and sp.get("id") != part.get("id")):
            pl.remove(sp)
    # "defaults" is the file's own page size; left in, it overrides the wide one-line page and MuseScore wraps the music
    for tag in ("defaults", "credit", "work", "movement-title", "identification"):
        for el in root.findall(tag):
            root.remove(el)
    for sp in pl.findall("score-part"):
        pn = sp.find("part-name")
        if pn is not None:
            pn.text = ""
            pn.set("print-object", "no")

    div, key, beats, btype = 1, 0, 4, 4
    out = []
    for m in part.findall("measure"):
        num = m.get("number", "")
        at = m.find("attributes")
        if at is not None:
            if at.findtext("divisions"):
                div = int(at.findtext("divisions"))
            if at.find("key/fifths") is not None:
                key = int(at.findtext("key/fifths"))
            if at.find("time/beats") is not None:
                beats = int(at.findtext("time/beats")); btype = int(at.findtext("time/beat-type"))
        if not num.isdigit() or not a.from_bar <= int(num) <= a.to_bar:
            continue
        n = int(num)
        beat_len = div * 4 // btype
        start_pos = int(round((a.from_beat - 1) * beat_len)) if n == a.from_bar else 0
        nm = copy.deepcopy(m)
        pos = 0; prev_on = 0
        for e in list(nm):
            if e.tag in ("backup", "forward"):
                pos += (-1 if e.tag == "backup" else 1) * int(e.findtext("duration"))
                nm.remove(e)
            elif e.tag == "note":
                chord = e.find("chord") is not None
                on = prev_on if chord else pos
                d = int(e.findtext("duration") or 0)
                if not chord:
                    prev_on = pos; pos += d
                if (e.find("grace") is not None or e.findtext("staff", "1") != str(a.staff)
                        or e.findtext("voice", "1") != a.voice or on < start_pos):
                    nm.remove(e)
                    continue
                for x in e.findall("staff"):
                    e.remove(x)
                for t in e.findall("tie"):
                    e.remove(t)
                for nt in e.findall("notations"):
                    for ch in list(nt):
                        if ch.tag in DROP_NOTATIONS:
                            nt.remove(ch)
                        elif ch.tag == "ornaments":
                            for w in ch.findall("wavy-line"):
                                ch.remove(w)
                    if len(nt) == 0:
                        e.remove(nt)
            elif e.tag in ("direction", "print", "barline", "harmony", "attributes"):
                nm.remove(e)
        at2 = ET.Element("attributes")
        ET.SubElement(at2, "divisions").text = str(div)
        k = ET.SubElement(at2, "key"); ET.SubElement(k, "fifths").text = str(key)
        t = ET.SubElement(at2, "time"); ET.SubElement(t, "beats").text = str(beats); ET.SubElement(t, "beat-type").text = str(btype)
        if not out:
            sign, line = ("F", "4") if a.clef == "bass" else ("G", "2")
            c = ET.SubElement(at2, "clef"); ET.SubElement(c, "sign").text = sign; ET.SubElement(c, "line").text = line
        nm.insert(0, at2)
        if n == a.from_bar and start_pos > 0:
            nm.set("implicit", "yes"); nm.set("number", "0")
        nm.attrib.pop("width", None)
        out.append(nm)
    if not out:
        sys.exit("no bars found in that range")
    for mm in list(part.findall("measure")):
        part.remove(mm)
    for mm in out:
        part.append(mm)
    tree.write(a.out, encoding="utf-8", xml_declaration=True)
    print(f"wrote {len(out)} bars (bars {a.from_bar}..{a.to_bar}, staff {a.staff}, voice {a.voice}) -> {a.out}")


if __name__ == "__main__":
    main()

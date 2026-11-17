"""build_grand.py SCORE OUT.musicxml FROM_BAR TO_BAR : whole bars, both staves, everything visible kept as engraved.
Hidden (print-object="no") playback notes become silent gaps. Spanner ends whose other end is outside are dropped."""
import sys, copy, zipfile, re
import xml.etree.ElementTree as ET


def load(path):
    if path.lower().endswith(".mxl"):
        z = zipfile.ZipFile(path)
        name = next(n for n in z.namelist() if n.endswith(".xml") and not n.startswith("META-INF"))
        return ET.ElementTree(ET.fromstring(z.read(name)))
    return ET.parse(path)


def make_pickup(m, start, mlen, staves):
    """keep only what happens from `start` (in divisions) to the end of the bar and make it a short pick-up bar:
    every (staff, voice) line is rebuilt from the cut onward; a staff left with nothing gets a visible rest."""
    pos = 0; prev_on = 0
    groups = {}; order = []
    for e in list(m):
        if e.tag in ("backup", "forward"):
            pos += (-1 if e.tag == "backup" else 1) * int(e.findtext("duration"))
        elif e.tag == "note":
            chord = e.find("chord") is not None
            on = prev_on if chord else pos
            d = int(e.findtext("duration") or 0)
            if not chord:
                prev_on = pos; pos += d
            key = (e.findtext("staff", "1"), e.findtext("voice", "1"))
            if key not in groups:
                groups[key] = []; order.append(key)
            groups[key].append((on, d, e, chord))
    kept = {}
    for key in order:
        rows = [r for r in groups[key] if r[0] >= start and not (r[2].find("grace") is not None and r[0] < start)]
        if rows:
            kept[key] = rows
    keep_dirs = [e for e in m if e.tag == "direction" and e.find("direction-type/wedge") is not None]
    for e in list(m):
        if e.tag in ("note", "backup", "forward", "direction"):
            m.remove(e)
    seq = []
    first = True
    for key in order:
        if key not in kept:
            continue
        if not first:
            b = ET.Element("backup"); ET.SubElement(b, "duration").text = str(cursor - start); seq.append(b)
        first = False
        cursor = start
        for on, d, e, chord in kept[key]:
            if not chord and on > cursor:
                f = ET.Element("forward"); ET.SubElement(f, "duration").text = str(on - cursor); seq.append(f); cursor = on
            seq.append(e)
            if not chord:
                cursor = on + d
        last_cursor = cursor
    for st in range(1, staves + 1):                       # empty staff: show a rest for the pick-up
        if not any(k[0] == str(st) for k in kept):
            if seq:
                b = ET.Element("backup"); ET.SubElement(b, "duration").text = str(last_cursor - start); seq.append(b)
            r = ET.Element("note"); ET.SubElement(r, "rest")
            ET.SubElement(r, "duration").text = str(mlen - start)
            ET.SubElement(r, "voice").text = str(4 + st); ET.SubElement(r, "type").text = "half"
            ET.SubElement(r, "staff").text = str(st)
            seq.append(r); last_cursor = mlen
    for d in keep_dirs:
        typ = d.find("direction-type/wedge").get("type")
        if typ == "stop":
            seq.append(d)
        else:
            seq.insert(0, d)
    for e in seq:
        m.append(e)
    m.set("implicit", "yes"); m.set("number", "0")
    return m


def build(src, dst, b0, b1, part_no=1, pedal=False, from_beat=1.0):
    tree = load(src); root = tree.getroot()
    part = root.findall("part")[part_no - 1]
    for tag in ("defaults", "credit", "work", "movement-title", "identification"):
        for el in root.findall(tag):
            root.remove(el)
    for sp in root.find("part-list").findall("score-part"):
        pn = sp.find("part-name")
        if pn is not None:
            pn.text = ""; pn.set("print-object", "no")
        for t in ("part-abbreviation", "part-name-display", "part-abbreviation-display"):
            for el in sp.findall(t):
                sp.remove(el)
    div, key, beats, btype, staves = 1, 0, 4, 4, 1
    clefs = {}
    out = []
    for m in part.findall("measure"):
        num = m.get("number", "")
        inside = num.isdigit() and b0 <= int(num) <= b1
        if inside and not out:
            state = (div, key, beats, btype, staves, copy.deepcopy(clefs))
        for at in m.findall("attributes"):
            if at.findtext("divisions"): div = int(at.findtext("divisions"))
            if at.find("key/fifths") is not None: key = int(at.findtext("key/fifths"))
            if at.find("time/beats") is not None:
                beats = int(at.findtext("time/beats")); btype = int(at.findtext("time/beat-type"))
            if at.findtext("staves"): staves = int(at.findtext("staves"))
            for c in at.findall("clef"):
                clefs[c.get("number", "1")] = copy.deepcopy(c)
        if inside:
            out.append(copy.deepcopy(m))
    if not out:
        sys.exit("no bars in that range")
    div0, key0, beats0, btype0, staves0, clefs0 = state
    if from_beat > 1.0:
        start_pos = int(round((from_beat - 1) * div0 * 4 / btype0))
        out[0] = make_pickup(out[0], start_pos, div0 * 4 * beats0 // btype0, staves0)
    for m in out:
        m.attrib.pop("width", None)
        for e in list(m):
            if e.tag == "print":
                m.remove(e)
            elif e.tag == "direction":
                words = [w.text or "" for w in e.iter("words")]
                if words and all(re.fullmatch(r"\s*\d+(\.\d+)?\s*", w) for w in words):
                    m.remove(e)          # bare numbers = hidden playback tempo marks
                elif e.find("direction-type/metronome") is not None and not words:
                    m.remove(e)          # "quarter = 140" marks added for playback: stacked up, they bury the notes
                    # (Chopin and most classical scores print words like "Presto con fuoco" and no metronome marks)
        # a trill line that ends on a hidden note: end it on the last visible note of that staff sounding by then
        pos = 0; prev_on = 0; vis = []; moved = []
        for e in list(m):
            if e.tag in ("backup", "forward"):
                pos += (-1 if e.tag == "backup" else 1) * int(e.findtext("duration"))
            elif e.tag == "note" and e.find("grace") is None:
                d = int(e.findtext("duration") or 0); chord = e.find("chord") is not None
                on = prev_on if chord else pos
                if not chord:
                    prev_on = pos; pos += d
                if e.get("print-object") == "no":
                    for w in e.iter("wavy-line"):
                        if w.get("type") == "stop":
                            moved.append((on, e.findtext("staff", "1"), w.get("number", "1")))
                elif e.find("rest") is None:
                    vis.append((on, e.findtext("staff", "1"), e))
        for on, st, num in moved:
            cands = [v for v in vis if v[1] == st and v[0] <= on]
            if cands:
                tgt = max(cands, key=lambda v: v[0])[2]
                nt = tgt.find("notations")
                if nt is None: nt = ET.SubElement(tgt, "notations")
                orn = nt.find("ornaments")
                if orn is None: orn = ET.SubElement(nt, "ornaments")
                if not any(w.get("type") == "stop" for w in orn.findall("wavy-line")):
                    ET.SubElement(orn, "wavy-line", {"type": "stop", "number": num})
        for e in list(m):
            if e.tag == "note" and e.get("print-object") == "no":
                i = list(m).index(e); m.remove(e)
                if e.find("chord") is None and e.find("grace") is None:
                    f = ET.Element("forward"); ET.SubElement(f, "duration").text = e.findtext("duration")
                    m.insert(i, f)
    # first bar gets the full running attributes
    at = ET.Element("attributes")
    ET.SubElement(at, "divisions").text = str(div0)
    k = ET.SubElement(at, "key"); ET.SubElement(k, "fifths").text = str(key0)
    t = ET.SubElement(at, "time"); ET.SubElement(t, "beats").text = str(beats0); ET.SubElement(t, "beat-type").text = str(btype0)
    ET.SubElement(at, "staves").text = str(staves0)
    for n in sorted(clefs0):
        at.append(clefs0[n])
    out[0].insert(0, at)
    # trill lines: printed, a wavy line covers the trilled note. Files often end it on a hidden playback note much
    # later, so re-end every line on its own note. Grace notes lose stray trill marks ("tr tr tr").
    seq = {}; base = 0
    for m in out:
        pos = 0; prev_on = 0; mlen = 0
        for e in m:
            if e.tag in ("backup", "forward"):
                pos += (-1 if e.tag == "backup" else 1) * int(e.findtext("duration"))
            elif e.tag == "note":
                if e.find("grace") is not None:
                    for nt in e.findall("notations"):
                        for orn in nt.findall("ornaments"):
                            for x in list(orn):
                                if x.tag in ("trill-mark", "wavy-line"):
                                    orn.remove(x)
                    continue
                d = int(e.findtext("duration") or 0); chord = e.find("chord") is not None
                on = prev_on if chord else pos
                if not chord:
                    prev_on = pos; pos += d
                mlen = max(mlen, pos)
                for orn in e.iter("ornaments"):
                    for w in list(orn.findall("wavy-line")):
                        if w.get("type") == "stop":
                            orn.remove(w)
                if not chord:
                    seq.setdefault((e.findtext("staff", "1"), e.findtext("voice", "1")), []).append((base + on, base + on + d, e))
        base += mlen
    line_no = 0
    for notes_ in seq.values():
        for i, (on, end, e) in enumerate(notes_):
            for orn in e.iter("ornaments"):
                for w in orn.findall("wavy-line"):
                    if w.get("type") != "start":
                        continue
                    w.set("number", "1")               # MuseScore's importer only copes with number 1
                    # MuseScore draws the line through the whole note that carries the stop, so stop on the
                    # trilled note itself: the line then covers exactly that note, as printed
                    ET.SubElement(orn, "wavy-line", {"type": "stop", "number": w.get("number", "1")})
    # spanners: drop a stop that has no start inside the cut, and a start that never stops
    def spanners(tag):
        ev = []
        for m in out:
            for parent in m.iter():
                for ch in list(parent):
                    if ch.tag == tag and ch.get("type") in ("start", "stop", "crescendo", "diminuendo", "up", "down"):
                        ev.append((parent, ch))
        return ev
    for tag in ("wavy-line", "slur", "wedge", "octave-shift", "glissando", "slide", "bracket", "dashes"):
        open_ = {}
        for parent, ch in spanners(tag):
            n = ch.get("number", "1")
            if ch.get("type") == "stop":
                if n in open_: del open_[n]
                else: parent.remove(ch)
            else:
                open_[n] = (parent, ch)
        for parent, ch in open_.values():
            parent.remove(ch)
    if not pedal:                     # pedal marks are often doubled up for playback and pile on top of each other
        for m in out:
            for d in list(m.findall("direction")):
                if d.find("direction-type/pedal") is not None:
                    m.remove(d)
    first_pedal = True
    for m in out:
        for d in list(m.findall("direction")):
            p = d.find("direction-type/pedal")
            if p is not None:
                if first_pedal and p.get("type") in ("stop", "change", "continue"):
                    m.remove(d)
                first_pedal = False
    for d in [d for m in out for d in m.findall("direction")]:
        dt = d.findall("direction-type")
        if dt and all(len(x) == 0 for x in dt):
            for m in out:
                if d in list(m): m.remove(d)
    for m in out:                     # constant playback tempo, so MuseScore's event times are plain beat counts
        for snd in m.iter("sound"):
            snd.attrib.pop("tempo", None)
    for mm in list(part.findall("measure")):
        part.remove(mm)
    for mm in out:
        part.append(mm)
    tree.write(dst, encoding="utf-8", xml_declaration=True)
    print("wrote", len(out), "bars ->", dst)


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), from_beat=float(sys.argv[5]) if len(sys.argv) > 5 else 1.0)

#!/usr/bin/env python3
"""transcribe.py: NO score file? Write down ONE melodic line from the video's sound -> melody.musicxml.

  python transcribe.py VIDEO OUT_DIR [--bpm 100] [--time 4/4] [--clef auto|treble|bass|alto|tenor]
                                     [--low 40 --high 96] [--start S --end S] [--transpose 0]

Works for one instrument or one voice playing alone. Chords, piano, a band or loud backing music will not
come out right: get the score file for those. --transpose shifts the written notes (e.g. +2 for Bb trumpet,
+9 for Eb alto sax) because the sound is concert pitch. Always LOOK at the result and fix wrong notes in
MuseScore before rendering (open melody.musicxml, fix, save as .musicxml, run engrave.py on it).
"""
import argparse, os, sys
from xml.sax.saxutils import escape

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import onset_flux, peaks, read_audio, spectrum, yin  # noqa: E402

MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
FLAT = [("C", 0), ("D", -1), ("D", 0), ("E", -1), ("E", 0), ("F", 0), ("G", -1), ("G", 0), ("A", -1), ("A", 0), ("B", -1), ("B", 0)]
SHARP = [("C", 0), ("C", 1), ("D", 0), ("D", 1), ("E", 0), ("F", 0), ("F", 1), ("G", 0), ("G", 1), ("A", 0), ("A", 1), ("B", 0)]
FIFTHS = {0: 0, 7: 1, 2: 2, 9: 3, 4: 4, 11: 5, 6: 6, 5: -1, 10: -2, 3: -3, 8: -4, 1: -5}
TYPES = {16: ("whole", 0), 12: ("half", 1), 8: ("half", 0), 6: ("quarter", 1), 4: ("quarter", 0), 3: ("eighth", 1),
         2: ("eighth", 0), 1: ("16th", 0)}
CLEFS = {"treble": ("G", 2), "bass": ("F", 4), "alto": ("C", 3), "tenor": ("C", 4)}


def notes_from_audio(x, lo, hi):
    f0, conf, t = yin(x)
    S, t2, hz, lvl = spectrum(x)
    n = min(len(f0), len(lvl))
    f0, conf, t, lvl = f0[:n], conf[:n], t[:n], lvl[:n]
    flux = onset_flux(S)[:n]
    midi = 69 + 12 * np.log2(np.maximum(f0, 1) / 440)
    floor = max(np.percentile(lvl, 10), lvl.max() - 40)
    ok = (lvl > floor + 8) & (conf > 0.75) & (midi > lo - 0.5) & (midi < hi + 0.5)
    m = np.where(ok, np.round(midi), 0).astype(int)
    sm = m.copy()                                      # a pitch must hold for a few frames
    for i in range(2, len(m) - 2):
        v, c = np.unique(m[i - 2:i + 3], return_counts=True); sm[i] = v[c.argmax()]
    on = set(peaks(flux, min_gap=6, rel=2.5).tolist())
    notes, i = [], 0
    while i < len(sm):
        if sm[i] == 0:
            i += 1; continue
        j = i
        while j + 1 < len(sm) and sm[j + 1] == sm[i] and (j + 1 not in on or j + 1 - i < 6):
            j += 1
        if t[j] - t[i] >= 0.06:
            notes.append([float(t[i]), float(t[j] + 0.01), int(sm[i])])
        i = j + 1
    return notes


def fit_tempo(onsets, bpm=None):
    """quarter-note tempo and grid start that put the note starts closest to a 16th-note grid."""
    on = np.array(onsets)
    best = None
    for b in ([bpm] if bpm else np.arange(60, 181, 0.5)):
        g = 60 / b / 4
        for ph in np.linspace(0, g, 12, endpoint=False):
            r = ((on - on[0] - ph) / g) % 1
            err = np.minimum(r, 1 - r).mean() + 0.002 * abs(b - 100) / 40
            if best is None or err < best[0]:
                best = (err, b, on[0] + ph)
    return best[1], best[2]


def split(total, avail_list=(16, 12, 8, 6, 4, 3, 2, 1)):
    out = []
    while total > 0:
        d = next(v for v in avail_list if v <= total); out.append(d); total -= d
    return out


def write_xml(path, events, beats, beat_type, fifths, clef, title):
    """events: list of (start16, len16, midi or None). divisions = 4 per quarter (16th grid)."""
    bar16 = beats * 16 // beat_type
    spell = FLAT if fifths < 0 else SHARP
    # fill rests, split across bar lines with ties
    pieces = []  # (bar, len16, midi, tie_start, tie_stop)
    cur = 0
    seq = []
    for s, l, m in events:
        if s > cur:
            seq.append((cur, s - cur, None))
        seq.append((s, l, m)); cur = s + l
    end = -(-cur // bar16) * bar16
    if end > cur:
        seq.append((cur, end - cur, None))
    for s, l, m in seq:
        chunks = []
        pos, left = s, l
        while left > 0:
            room = bar16 - pos % bar16
            take = min(room, left)
            for d in split(take):
                chunks.append((pos // bar16, d)); pos += d
            left -= take
        for k, (b, d) in enumerate(chunks):
            pieces.append((b, d, m, m is not None and k < len(chunks) - 1, m is not None and k > 0))
    nbars = end // bar16
    sign, line = CLEFS[clef]
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<score-partwise version="3.1"><work><work-title>%s</work-title></work>' % escape(title),
           '<part-list><score-part id="P1"><part-name>Melody</part-name></score-part></part-list><part id="P1">']
    for b in range(nbars):
        out.append(f'<measure number="{b + 1}">')
        if b == 0:
            out.append(f"<attributes><divisions>4</divisions><key><fifths>{fifths}</fifths></key>"
                       f"<time><beats>{beats}</beats><beat-type>{beat_type}</beat-type></time>"
                       f"<clef><sign>{sign}</sign><line>{line}</line></clef></attributes>")
        for pb, d, m, ts, tp in pieces:
            if pb != b:
                continue
            typ, dot = TYPES[d]
            if m is None:
                out.append(f"<note><rest/><duration>{d}</duration><type>{typ}</type>{'<dot/>' * dot}</note>")
                continue
            step, alter = spell[m % 12]
            ties = ('<tie type="stop"/>' if tp else "") + ('<tie type="start"/>' if ts else "")
            nots = ('<tied type="stop"/>' if tp else "") + ('<tied type="start"/>' if ts else "")
            out.append(f"<note><pitch><step>{step}</step>{f'<alter>{alter}</alter>' if alter else ''}"
                       f"<octave>{m // 12 - 1}</octave></pitch><duration>{d}</duration>{ties}<type>{typ}</type>"
                       f"{'<dot/>' * dot}{f'<notations>{nots}</notations>' if nots else ''}</note>")
        out.append("</measure>")
    out.append("</part></score-partwise>")
    with open(path, "w") as fh:
        fh.write("\n".join(out))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video"); ap.add_argument("out")
    ap.add_argument("--bpm", type=float); ap.add_argument("--time", default="4/4")
    ap.add_argument("--clef", default="auto", choices=["auto"] + sorted(CLEFS))
    ap.add_argument("--low", type=int, default=34); ap.add_argument("--high", type=int, default=98)
    ap.add_argument("--start", type=float, default=0.0); ap.add_argument("--end", type=float)
    ap.add_argument("--transpose", type=int, default=0)
    ap.add_argument("--title", default="Melody")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    x = read_audio(a.video)
    notes = [n for n in notes_from_audio(x, a.low, a.high) if n[0] >= a.start and (a.end is None or n[0] < a.end)]
    if len(notes) < 3:
        sys.exit("ERROR: could not hear a clear single melody. A score file is needed for this video.")
    # octave slips (the pitch tracker jumps an octave on one note): fold back toward both neighbours
    for k in range(1, len(notes) - 1):
        m, lo_, hi_ = notes[k][2], notes[k - 1][2], notes[k + 1][2]
        for sh in (-12, 12):
            if abs(m - lo_) >= 10 and abs(m - hi_) >= 10 and abs(m + sh - lo_) <= 7 and abs(m + sh - hi_) <= 7:
                notes[k][2] = m + sh
    bpm, t0 = fit_tempo([n[0] for n in notes], a.bpm)
    # keep the rhythm readable: if most notes came out as 16ths or shorter, the beat is twice as fast
    gaps = np.diff([n[0] for n in notes])
    while not a.bpm and len(gaps) and np.median(gaps) / (60 / bpm / 4) < 1.6 and bpm * 2 <= 200:
        bpm *= 2
    g = 60 / bpm / 4
    ev, last_end = [], 0
    for k, (s, e, m) in enumerate(notes):
        q = max(last_end, int(round((s - t0) / g)))
        nxt = int(round((notes[k + 1][0] - t0) / g)) if k + 1 < len(notes) else q + max(1, int(round((e - s) / g)))
        held = max(1, int(round((e - s) / g)))
        ln = (nxt - q if nxt - q <= max(held + 2, 8) else held) if nxt > q else 1   # small gaps = legato
        if ev and q < ev[-1][0] + ev[-1][1]:
            continue
        ev.append((q, ln, m + a.transpose)); last_end = q + ln
    pcs = np.bincount([m % 12 for _, _, m in ev], weights=[l for _, l, _ in ev], minlength=12)
    tonic = int(np.argmax([np.corrcoef(np.roll(MAJOR, k), pcs)[0, 1] for k in range(12)]))
    fifths = FIFTHS[tonic]
    med = float(np.median([m for _, _, m in ev]))
    clef = a.clef if a.clef != "auto" else ("bass" if med < 57 else "treble")
    beats, beat_type = (int(v) for v in a.time.split("/"))
    path = os.path.join(a.out, "melody.musicxml")
    write_xml(path, ev, beats, beat_type, fifths, clef, a.title)
    print(f"{len(ev)} notes, ~{bpm:g} bpm, key {fifths:+d} (sharps +, flats -), {clef} clef -> {path}\n"
          "Check the notes (open it in MuseScore) before rendering: the sound gives the pitches, the rhythm is a guess.")


if __name__ == "__main__":
    main()

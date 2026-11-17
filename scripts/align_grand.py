#!/usr/bin/env python3
"""align_grand.py: timing for a full-score cut made by engrave_grand.py -> WORK/timeline.json.

  python align_grand.py VIDEO WORK SCORE [--from-beat 3] [--lead 1.3] [--no-refine]

Pass 1: pitch-class dynamic time warping of the whole score against the audio (follows rubato, trills, pedal).
Pass 2: every melody note (top staff, voice 1) is pulled to the moment its own pitch starts sounding, searched only
close to where pass 1 put it, in order. Fast runs get their real note-by-note timing from this.
--from-beat: the playing starts on this beat of the first bar; taken from engrave_grand.py when it was used with --from-beat.
"""
import argparse, math, os, sys
import xml.etree.ElementTree as ET
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "scripts"))
from common import load_json, probe, read_audio, salience, save_json, spectrum  # noqa: E402
from build_grand import load  # noqa: E402

STEP = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
G = 16


def all_notes(path, part_no, b0, b1):
    """(q_on, q_off, midi, staff, voice, hidden, trill) for bars b0..b1, q counted from the start of bar b0."""
    part = load(path).getroot().findall("part")[part_no - 1]
    div, beats, btype = 1, 4, 4
    out = []; base = 0.0
    for m in part.findall("measure"):
        for at in m.findall("attributes"):
            if at.findtext("divisions"): div = int(at.findtext("divisions"))
            if at.find("time/beats") is not None:
                beats = int(at.findtext("time/beats")); btype = int(at.findtext("time/beat-type"))
        num = m.get("number", "")
        if not num.isdigit() or not b0 <= int(num) <= b1:
            continue
        pos = 0; prev_on = 0
        for e in m:
            if e.tag in ("backup", "forward"):
                pos += (-1 if e.tag == "backup" else 1) * int(e.findtext("duration"))
            elif e.tag == "note" and e.find("grace") is None:
                d = int(e.findtext("duration") or 0); chord = e.find("chord") is not None
                on = prev_on if chord else pos
                if not chord:
                    prev_on = pos; pos += d
                p = e.find("pitch")
                if p is not None:
                    midi = 12 * (int(p.findtext("octave")) + 1) + STEP[p.findtext("step")] + int(float(p.findtext("alter") or 0))
                    out.append((base + on / div, base + (on + d) / div, midi, e.findtext("staff", "1"), e.findtext("voice", "1"),
                                e.get("print-object") == "no", e.find(".//trill-mark") is not None))
        base += 4.0 * beats / btype
    return out, base


def dtw(notes, total_q, q0, x):
    nF = int((total_q - q0) * G)
    C = np.zeros((nF, 12), np.float32)
    for qa, qb, mi, *_ in notes:
        if qb <= q0:
            continue
        qa, qb = max(qa, q0) - q0, qb - q0
        for f in range(int(qa * G), min(nF, int(math.ceil(qb * G)))):
            age = f / G - qa
            w = 0.25 + 2.0 * math.exp(-age * 6) + 0.5 * math.exp(-age / 3.0)
            for h, wh in ((1, 1), (2, .6), (3, .4), (4, .25)):
                C[f, int(round(mi + 12 * math.log2(h))) % 12] += w * wh
    C = np.sqrt(C); C /= np.linalg.norm(C, axis=1, keepdims=True) + 1e-9
    Sg, tt, hz, _ = spectrum(x)
    sel = (hz > 90) & (hz < 2200)
    pc = np.round(12 * np.log2(hz[sel] / 440.0) + 69).astype(int) % 12
    A = np.zeros((len(tt), 12), np.float32)
    for k in range(12):
        A[:, k] = np.log1p(Sg[:, sel][:, pc == k].sum(1) * 0.05)
    K = 10; nA = len(tt) // K
    A = A[:nA * K].reshape(nA, K, 12).mean(1); ta = tt[:nA * K].reshape(nA, K).mean(1)
    A -= A.min(1, keepdims=True); A /= np.linalg.norm(A, axis=1, keepdims=True) + 1e-9
    cost = 1 - C @ A.T
    steps = [(1, 1, 1.0), (1, 2, 1.15), (2, 1, 1.15), (1, 3, 1.4), (3, 1, 1.4)]
    INF = 1e9
    D = np.full((nF, nA), INF, np.float32); B = np.zeros((nF, nA, 2), np.int16); D[0] = cost[0]
    for i in range(1, nF):
        best = np.full(nA, INF, np.float32); arg = np.zeros((nA, 2), np.int16)
        for di, dj, wgt in steps:
            if i - di < 0:
                continue
            prev = np.full(nA, INF, np.float32); prev[dj:] = D[i - di, :nA - dj]
            cand = prev + cost[i] * wgt * di
            better = cand < best
            best[better] = cand[better]; arg[better] = (di, dj)
        D[i] = best; B[i] = arg
    j = int(np.argmin(D[-1])); i = nF - 1; path = [(i, j)]
    while i > 0:
        di, dj = B[i, j]; i -= int(di); j -= int(dj); path.append((i, j))
    path = path[::-1]
    pi = np.array([p[0] for p in path], float); pj = np.array([p[1] for p in path], float)
    tf = np.interp(np.arange(nF), pi, pj) * 0.1 + ta[0]
    return tf, Sg, tt, hz


def refine(mel, T, Sg, tt, hz, win=1.0):
    """mel: [(q, [midis])] in order. Returns (q, t, strength): where the written pitches start sounding, near T(q)."""
    dt = float(tt[1] - tt[0]); nT = len(tt)
    sal = {}

    def onset(mi):
        if mi not in sal:
            # the note's FUNDAMENTAL only: with overtones added, a note an octave higher looks like this one
            kf = int(round(440.0 * 2 ** ((mi - 69) / 12.0) / (hz[1] - hz[0])))
            s = np.sqrt(Sg[:, max(0, kf - 1):kf + 2].max(1)).astype(np.float32)
            s = np.log1p(s / (np.median(s) + 1e-9))
            o = np.zeros_like(s); o[3:-3] = np.maximum(0, s[5:-1] - s[1:-5])
            sal[mi] = o
        return sal[mi]
    cand = []
    for q, mids in mel:
        t0 = T(q); lo = max(0, int((t0 - win) / dt)); hi = min(nT - 1, int((t0 + win) / dt))
        fr = np.arange(lo, hi + 1)
        o = np.mean([onset(mi)[fr] for mi in mids], axis=0)
        o = o / (o.max() + 1e-9)
        sc = o - 0.3 * ((tt[fr] - t0) / win) ** 2
        cand.append((fr, sc, o))
    # in-order choice: note k must come at least 25 ms after note k-1
    mingap = max(1, int(0.025 / dt))
    best_prev = None; back = []
    for k, (fr, sc, o) in enumerate(cand):
        if best_prev is None:
            tot = sc.copy(); bp = np.full(len(fr), -1)
        else:
            pfr, ptot = best_prev
            run = np.maximum.accumulate(ptot); arg = np.zeros(len(pfr), int)
            for i in range(1, len(pfr)):
                arg[i] = i if ptot[i] >= run[i - 1] else arg[i - 1]
            idx = np.searchsorted(pfr, fr - mingap, side="right") - 1
            ok = idx >= 0
            tot = np.where(ok, sc + run[np.clip(idx, 0, None)], -1e6)
            bp = np.where(ok, arg[np.clip(idx, 0, None)], -1)
        back.append(bp); best_prev = (fr, tot)
    i = int(np.argmax(best_prev[1])); picks = [0] * len(cand)
    for k in range(len(cand) - 1, -1, -1):
        picks[k] = i; i = int(back[k][i]) if back[k][i] >= 0 else 0
    out = []
    for (q, _), (fr, sc, o), i in zip(mel, cand, picks):
        out.append((q, float(tt[fr[i]]), float(o[i])))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video"); ap.add_argument("work"); ap.add_argument("score")
    ap.add_argument("--from-beat", type=float, default=None,
                    help="beat of the first bar where the playing starts (default: what engrave_grand.py used)")
    ap.add_argument("--lead", type=float, default=1.3, help="seconds per quarter for beats before the playing starts")
    ap.add_argument("--part", type=int, default=1)
    ap.add_argument("--nudge-ms", type=float, default=0)
    ap.add_argument("--no-refine", action="store_true")
    ap.add_argument("--first-t", type=float, default=None,
                    help="force the first note to start at this video time in seconds (0 when the player is already "
                         "playing in frame 0; a very soft start is otherwise placed where it gets loud)")
    a = ap.parse_args()
    N = load_json(os.path.join(a.work, "notes.json")); atk = N["attacks"]
    notes, total_q = all_notes(a.score, a.part, N["from_bar"], N["to_bar"])
    from_beat = a.from_beat if a.from_beat is not None else N.get("from_beat", 1.0)
    q0 = from_beat - 1.0
    # an engraving that starts part-way into the first bar (a pick-up bar) counts its beats from 0; the score file
    # counts from the start of the bar, so drawn positions are shifted by the beats that were cut off
    shift = N.get("from_beat", 1.0) - 1.0
    x = read_audio(a.video)
    tf, Sg, tt, hz = dtw(notes, total_q, q0, x)
    fidx = np.arange(len(tf))

    def T(q):
        if q < q0:
            return float(tf[0] - (q0 - q) * a.lead)
        return float(np.interp((q - q0) * G, fidx, tf))

    print(f"pass 1: the music sits between {tf[0]:.2f}s and {tf[-1]:.2f}s of the {len(x) / 22050:.2f}s video")
    w = 2205                                                          # start check: loudness over the first seconds
    db = [10 * np.log10(float((x[i * w:(i + 1) * w] ** 2).mean()) + 1e-12) for i in range(min(40, len(x) // w))]
    step = [f"{db[min(len(db) - 1, int(s * 10))]:.0f}" for s in (0, 0.5, 1, 2, 3, 4)]
    print(f"start check: loudness at 0 / 0.5 / 1 / 2 / 3 / 4 s = {' / '.join(step)} dB; the aligner puts the first note at "
          f"{tf[0]:.2f}s. If the player is already playing in frame 0 and the sound is rising from the start "
          f"(a soft trill or a crescendo), the first note really starts at 0: pass --first-t 0.")
    anchors = []
    if not a.no_refine:
        hidden = [(n[0], n[1]) for n in notes if n[5]]              # written-out trills (playback-only notes)
        def trilled(n):
            return n[6] or any(h0 < n[1] - 1e-6 and h1 > n[0] + 1e-6 for h0, h1 in hidden)
        groups = {"1": {}, "2": {}}
        for n in notes:
            if n[5] or n[0] < q0 or n[3] not in groups:
                continue
            if n[3] == "1" and trilled(n):
                continue                                             # a trill re-attacks its pitch all the time: no clear start
            groups[n[3]].setdefault(round(n[0], 4), set()).add(n[2])
        rh = sorted((q, [max(m)]) for q, m in groups["1"].items())   # right hand: the top note
        # a pitch that keeps coming back (written-out trill) has no single clear start either; one return (a turn) is fine
        rh = [(q, m) for q, m in rh if sum(m2 == m and 0 < abs(q2 - q) < 0.4 for q2, m2 in rh) < 2]
        lh = sorted((q, sorted(m)) for q, m in groups["2"].items())  # left hand: every note of the chord
        Sl, tl, hzl, _ = spectrum(x, n_fft=4096)                     # finer pitch grid for the low notes
        got = {}; rq = []
        for name, ev, (S_, t_, hz_) in (("right hand", rh, (Sg, tt, hz)), ("left hand", lh, (Sl, tl, hzl))):
            if not ev:
                continue
            ref = [(q, t, s_) for q, t, s_ in refine(ev, T, S_, t_, hz_) if s_ > 0.5]
            print(f"pass 2, {name}: {len(ref)} of {len(ev)} positions locked")
            if name == "right hand":
                rq = [q for q, _, _ in ref]
            else:      # inside a fast right-hand passage the right hand's own notes are the finer guide
                ref = [r for r in ref if not (any(0 < r[0] - q <= 0.2 for q in rq) and any(0 < q - r[0] <= 0.2 for q in rq))
                       and not any(abs(q - r[0]) < 1e-3 for q in rq)]
            for q, t, s_ in ref:
                if q not in got or s_ > got[q][1]:
                    got[q] = (t, s_)
        anchors = sorted((q, t) for q, (t, s_) in got.items())
        keep = []                                                    # in order, and no wild tempo jumps
        for q, t in anchors:
            while keep and t <= keep[-1][1]:
                keep.pop()
            keep.append((q, t))
        anchors = keep
        for _ in range(3):                                           # drop anchors that imply an absurd local speed
            sl = [(anchors[i + 1][1] - anchors[i][1]) / (anchors[i + 1][0] - anchors[i][0]) for i in range(len(anchors) - 1)]
            med = float(np.median(sl))
            bad = {i + 1 for i, v in enumerate(sl) if v < med / 6}
            if not bad:
                break
            anchors = [a_ for i, a_ in enumerate(anchors) if i not in bad]
        moved = [abs(t - T(q)) for q, t in anchors]
        print(f"pass 2: {len(anchors)} anchors; median shift {1000 * float(np.median(moved)):.0f} ms, largest {1000 * max(moved):.0f} ms")
    if anchors:
        aq = np.array([q for q, _ in anchors]); ad = np.array([t - T(q) for q, t in anchors])
        delta = lambda q: float(np.interp(q, aq, ad))
    else:
        delta = lambda q: 0.0
    last = -1e9
    for n in atk:
        qs = n["q"] + shift
        t = T(qs) + (delta(qs) if qs >= q0 else delta(q0))
        if anchors:                      # between two anchors less than a beat apart: a straight line is the best guess
            i = int(np.searchsorted(aq, qs))
            if 0 < i < len(aq) and aq[i] - aq[i - 1] <= 1.0:
                t = float(np.interp(qs, aq[i - 1:i + 1], [anchors[i - 1][1], anchors[i][1]]))
        t += a.nudge_ms / 1000.0
        t = max(t, last + 0.005); last = t
        n["t"] = round(t, 3); n["evidence"] = 1.0
    if a.first_t is not None:                                        # pin the opening: positions before the first sounding note too
        first_q = min(n["q"] for n in atk if n["q"] + shift >= q0 - 1e-6)
        for n in atk:
            if n["q"] <= first_q + 1e-6:
                n["t"] = round(a.first_t, 3)
        for k in range(1, len(atk)):                                 # keep times strictly increasing
            if atk[k]["t"] <= atk[k - 1]["t"]:
                atk[k]["t"] = round(atk[k - 1]["t"] + 0.005, 3)
    info = probe(a.video)
    spb = float(np.median([(atk[k + 1]["t"] - atk[k]["t"]) / (atk[k + 1]["q"] - atk[k]["q"])
                           for k in range(len(atk) - 1) if atk[k + 1]["q"] > atk[k]["q"]]))
    save_json({"video": os.path.abspath(a.video), "w": info["w"], "h": info["h"], "fps": info["fps"],
               "fps_str": info["fps_str"], "duration": info["duration"], "bpm": round(60 / spb, 1), "attacks": atk,
               "anchors": anchors}, os.path.join(a.work, "timeline.json"))
    for b in sorted({n["bar"] for n in atk}):
        print(f"  bar {N['from_bar'] + b} starts {[n for n in atk if n['bar'] == b][0]['t']:.2f}s")


if __name__ == "__main__":
    main()

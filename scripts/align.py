#!/usr/bin/env python3
"""align.py: find WHEN each written note is played in the video -> timeline.json.

  python align.py VIDEO WORK_DIR [--bpm 120] [--start 1.5] [--end 20] [--nudge-ms 0]

How: the audio is scanned for note starts (sudden new energy) and for pitch changes. Every written note is then
matched, in order, to one of those moments, choosing the path that best keeps the written rhythm (steady tempo
allowed to drift) AND hears the right pitch right after each start. Tempo is measured from the playing when
the score has no tempo marking (or use --bpm, quarter notes per minute).
"""
import argparse, os, sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import HOP, SR, load_json, onset_flux, peaks, probe, read_audio, salience, save_json, spectrum  # noqa: E402

FR = SR / HOP  # analysis frames per second


def pitch_share(S, hz, midis):
    """for each written pitch, its share of the harmonic strength among all written pitches, per frame."""
    sal = np.stack([salience(S, hz, m) for m in midis])
    return sal / (sal.sum(0, keepdims=True) + 1e-6)


def viterbi(notes, cand_t, E, spb):
    """pick one candidate time per note, in order. Cost: rhythm stretch (log ratio to the written gap) squared,
    plus tempo changes between neighbours; reward: evidence E[note, candidate]."""
    J, C = E.shape
    LAM, MU = 1.5, 0.8
    score = E[0].copy()
    back = np.zeros((J, C), int)
    prev_lr = np.zeros(C)
    D = cand_t[None, :] - cand_t[:, None]           # D[i, k] = t_k - t_i
    for j in range(1, J):
        gap = max((notes[j]["q"] - notes[j - 1]["q"]) * spb, 0.04)
        valid = D > max(0.035, 0.3 * gap)
        lr = np.where(valid, np.log(np.maximum(D, 1e-3) / gap), 0.0)
        pen = LAM * lr ** 2
        if j >= 2:
            pen = pen + MU * (lr - prev_lr[:, None]) ** 2
        tot = score[:, None] - pen
        tot[~valid] = -1e9
        back[j] = tot.argmax(0)
        score = tot.max(0) + E[j]
        prev_lr = lr[back[j], np.arange(C)]
    path = [int(score.argmax())]
    for j in range(J - 1, 0, -1):
        path.append(int(back[j, path[-1]]))
    return path[::-1], float(score.max())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video"); ap.add_argument("work")
    ap.add_argument("--bpm", type=float, help="tempo in quarter notes per minute (otherwise measured)")
    ap.add_argument("--start", type=float, default=0.0, help="first note is played after this many seconds")
    ap.add_argument("--end", type=float, default=None, help="last note is played before this many seconds")
    ap.add_argument("--nudge-ms", type=float, default=0.0, help="shift every note later (+) or earlier (-)")
    a = ap.parse_args()

    N = load_json(os.path.join(a.work, "notes.json"))
    notes = N["attacks"]
    info = probe(a.video)
    x = read_audio(a.video)
    S, t, hz, lvl = spectrum(x)
    flux = onset_flux(S)
    midis = sorted({n["midi"] for n in notes})
    share = pitch_share(S, hz, midis)
    loud = lvl > max(np.percentile(lvl, 10), lvl.max() - 40) + 8
    playing = np.where(loud)[0]
    if not len(playing):
        sys.exit("ERROR: the video sounds silent")

    # candidate note starts: onset peaks + places where the strongest written pitch changes
    cand = set(peaks(flux, min_gap=3, rel=0.7).tolist())
    best = share.argmax(0)
    for i in range(2, len(best) - 3):
        if best[i] != best[i - 1] and (best[i:i + 3] == best[i]).all() and loud[i]:
            cand.add(i)
    t_lo = max(a.start, t[playing[0]] - 0.15)
    t_hi = a.end if a.end else t[-1]
    cand = np.array(sorted(c for c in cand if t_lo <= t[c] <= t_hi and c < len(t) - 6))
    cand_t = t[cand]

    # evidence that note j starts at candidate c
    fz = flux / (np.percentile(flux[loud], 90) + 1e-9)
    J = len(notes)
    E = np.zeros((J, len(cand)))
    for j, n in enumerate(notes):
        r = midis.index(n["midi"])
        for ci, c in enumerate(cand):
            w = slice(c + 2, c + 9)
            hear = float((share[r, w] * loud[w]).mean())
            ev = 1.2 * hear + 0.5 * min(1.0, fz[c])
            if j > 0 and notes[j - 1]["midi"] != n["midi"]:
                ev -= 0.6 * float((share[midis.index(notes[j - 1]["midi"]), w] * loud[w]).mean())
            elif j > 0:
                ev += 0.4 * min(1.0, fz[c])      # same pitch again: the restart is the only clue
            E[j, ci] = ev
    # notes written before the video starts or after it ends: "off-screen" places every 20 ms with small evidence,
    # so a score that runs past the clip does not squeeze the last notes into the wrong spots
    pre = np.arange(t_lo - 3.0, t_lo - 0.05, 0.02)
    post = np.arange(t_hi + 0.05, t_hi + 3.0, 0.02)
    cand_t = np.r_[pre, cand_t, post]
    E = np.c_[np.full((J, len(pre)), 0.1), E, np.full((J, len(post)), 0.1)]
    cand = np.r_[np.full(len(pre), -1), cand, np.full(len(post), -1)]

    # tempo: given, from the score, or measured (try many, keep the one that explains the audio best)
    span_q = notes[-1]["q"] - notes[0]["q"]
    if a.bpm:
        trial = [60.0 / a.bpm]
    elif N.get("tempo_qpm"):
        base = 60.0 / N["tempo_qpm"]
        trial = list(base * np.exp(np.linspace(-0.25, 0.25, 15)))
    else:
        trial = list(np.exp(np.linspace(np.log(0.2), np.log(1.6), 70)))
    results = []
    for spb in trial:
        path, sc = viterbi(notes, cand_t, E, spb)
        results.append((sc, spb, path))
    sc, spb, path = max(results, key=lambda r: r[0])
    ts = cand_t[path]
    # refine tempo from what was found and run once more
    if len(ts) > 2 and not a.bpm:
        spb2 = float(np.median([(ts[k + 1] - ts[k]) / (notes[k + 1]["q"] - notes[k]["q"])
                                for k in range(len(ts) - 1) if notes[k + 1]["q"] > notes[k]["q"]]))
        path2, sc2 = viterbi(notes, cand_t, E, spb2)
        if sc2 >= sc:
            spb, path, ts = spb2, path2, cand_t[path2]
    ts = ts + a.nudge_ms / 1000.0

    for n, tt, c in zip(notes, ts, path):
        n["t"] = round(float(tt), 3)
        n["evidence"] = round(float(E[notes.index(n), c]), 3)
    weak = [n for n in notes if n["evidence"] < 0.25 and t_lo <= n["t"] <= t_hi]
    off = [n for n in notes if not t_lo <= n["t"] <= t_hi]
    out = {"video": os.path.abspath(a.video), "w": info["w"], "h": info["h"], "fps": info["fps"],
           "fps_str": info["fps_str"], "duration": info["duration"], "bpm": round(60 / spb, 1), "attacks": notes}
    save_json(out, os.path.join(a.work, "timeline.json"))
    print(f"aligned {J} notes, tempo ~{60 / spb:.0f} bpm (quarter), first note {ts[0]:.2f}s, last {ts[-1]:.2f}s; "
          f"{len(off)} written note(s) fall outside the video; {len(weak)} note(s) with weak evidence" + (": bars " + ", ".join(sorted({str(n['bar'] + 1) for n in weak}))
                                                      if weak else ""))


if __name__ == "__main__":
    main()

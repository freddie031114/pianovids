"""Shared helpers: find the tools, read audio, small audio analysis (numpy only)."""
import glob, json, os, shutil, subprocess, sys

import numpy as np

SR = 22050          # audio sample rate used for analysis
HOP = 220           # ~10 ms per analysis frame


def die(msg):
    sys.exit("ERROR: " + msg)


def mscore_log_hint():
    """MuseScore prints nothing when it fails (exit 40 / 1320). The reason is in its newest log: return the ERROR lines."""
    home = os.path.expanduser("~")
    dirs = [os.path.join(os.environ.get("LOCALAPPDATA", ""), "MuseScore", "MuseScore4", "logs"),
            os.path.join(home, "Library", "Application Support", "MuseScore", "MuseScore4", "logs"),
            os.path.join(home, ".local", "share", "MuseScore", "MuseScore4", "logs")]
    for d in dirs:
        logs = sorted(glob.glob(os.path.join(d, "*.log")), key=os.path.getmtime) if os.path.isdir(d) else []
        if logs:
            lines = [l.strip() for l in open(logs[-1], errors="replace") if "ERROR" in l and "plugins.json" not in l]
            return "\nMuseScore log (" + logs[-1] + "):\n  " + "\n  ".join(l[:300] for l in lines[-4:])
    return ""


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        name = str(cmd[0]).lower()
        hint = mscore_log_hint() if ("musescore" in name or "mscore" in name) else ""
        die("command failed: " + " ".join(map(str, cmd))[:400] + "\n" + (r.stderr or "")[-1500:] + hint)
    return r


def find_tool(name):
    """ffmpeg / ffprobe: PATH first, then the places installers put them (winget and Homebrew do not always update PATH
    for an already-open shell, which used to need a manual PATH export)."""
    p = shutil.which(name)
    if p:
        return p
    exe = name + (".exe" if os.name == "nt" else "")
    roots = []
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        roots += [os.path.join(local, "Microsoft", "WinGet", "Packages"), os.path.join(local, "Microsoft", "WinGet", "Links"),
                  "C:/ffmpeg", "C:/Program Files/ffmpeg", os.path.expanduser("~/scoop/shims")]
    else:
        roots += ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/snap/bin"]
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        deep = "Packages" in root or "ffmpeg" in root.lower()
        hits = glob.glob(os.path.join(root, "**", exe), recursive=True) if deep else glob.glob(os.path.join(root, exe))
        if hits:
            os.environ["PATH"] = os.path.dirname(hits[0]) + os.pathsep + os.environ.get("PATH", "")
            return hits[0]
    die(f"{name} not found. Install it (macOS: brew install ffmpeg; Windows: winget install Gyan.FFmpeg) and try again.")


def find_mscore():
    """MuseScore 4 command line. Set MSCORE=/path/to/mscore to override."""
    env = os.environ.get("MSCORE")
    if env and os.path.exists(env):
        return env
    cands = [
        "/Applications/MuseScore 4.app/Contents/MacOS/mscore",
        os.path.expanduser("~/Applications/MuseScore 4.app/Contents/MacOS/mscore"),
        r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe",
        r"C:\Program Files (x86)\MuseScore 4\bin\MuseScore4.exe",
    ]
    cands += glob.glob("/Applications/MuseScore*.app/Contents/MacOS/mscore")
    for c in cands:
        if os.path.exists(c):
            return c
    for n in ("mscore4portable", "musescore4", "mscore4", "mscore", "musescore"):
        p = shutil.which(n)
        if p:
            return p
    die("MuseScore 4 not found. Install it free from https://musescore.org (or set MSCORE=/path/to/mscore).")


def probe(video):
    """width, height, fps (as string and float), duration of a video."""
    out = run([find_tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
               "stream=width,height,r_frame_rate:format=duration", "-of", "json", video]).stdout
    j = json.loads(out)
    s = j["streams"][0]
    num, den = (int(v) for v in s["r_frame_rate"].split("/"))
    return {"w": int(s["width"]), "h": int(s["height"]), "fps_str": s["r_frame_rate"], "fps": num / den,
            "duration": float(j["format"]["duration"])}


def read_audio(video):
    """mono float32 samples at SR from any video/audio file."""
    r =subprocess.run([find_tool("ffmpeg"), "-v", "error", "-i", video, "-vn", "-ac", "1", "-ar", str(SR),
                        "-f", "s16le", "-"], capture_output=True)
    if r.returncode or not r.stdout:
        die("could not read the audio of " + video)
    return np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0


def frames(x, n_fft=2048):
    n = max(1, 1 + (len(x) - n_fft) // HOP)
    idx = np.arange(n_fft)[None, :] + HOP * np.arange(n)[:, None]
    fr = x[np.minimum(idx, len(x) - 1)]
    t = (np.arange(n) * HOP + n_fft / 2) / SR
    return fr, t


def spectrum(x, n_fft=2048):
    """magnitude spectrogram (frames x bins), frame times, bin frequencies, level in dB."""
    fr, t = frames(x, n_fft)
    win = np.hanning(n_fft).astype(np.float32)
    S = np.abs(np.fft.rfft(fr * win, axis=1)).astype(np.float32)
    hz = np.fft.rfftfreq(n_fft, 1 / SR)
    lvl = 10 * np.log10((fr.astype(np.float64) ** 2).mean(1) + 1e-12)
    return S, t, hz, lvl


def onset_flux(S):
    """spectral flux on log magnitude: how much new energy starts in each frame."""
    L = np.log1p(30 * S[:, :1200])
    f = np.r_[0, np.maximum(L[1:] - L[:-1], 0).sum(1)]
    return f


def peaks(f, min_gap=4, rel=1.0):
    """local maxima of f above rel * median, at least min_gap frames apart (strongest wins)."""
    thr = rel * np.median(f) + 1e-9
    cand = np.where((f[1:-1] >= f[:-2]) & (f[1:-1] > f[2:]) & (f[1:-1] > thr))[0] + 1
    keep = []
    for i in sorted(cand, key=lambda i: -f[i]):
        if all(abs(i - k) >= min_gap for k in keep):
            keep.append(i)
    return np.array(sorted(keep), int)


def salience(S, hz, midi):
    """harmonic strength of one pitch in every frame (sum of its first 6 harmonics)."""
    f0 = 440.0 * 2 ** ((midi - 69) / 12.0)
    df = hz[1] - hz[0]
    acc = np.zeros(S.shape[0], np.float32)
    for h, w in zip(range(1, 7), (1.0, 0.8, 0.65, 0.5, 0.4, 0.3)):
        k = int(round(f0 * h / df))
        if k + 2 >= S.shape[1]:
            break
        acc += w * np.sqrt(S[:, max(0, k - 1):k + 2].max(1))
    return acc


def yin(x, fmin=50.0, fmax=1200.0, thresh=0.15, n_fft=2048):
    """pitch track: f0 in Hz (0 = unvoiced) and confidence per 10 ms frame (YIN)."""
    fr, t = frames(x, n_fft)
    fr = fr - fr.mean(1, keepdims=True)
    W = n_fft // 2
    tmin, tmax = int(SR / fmax), min(int(SR / fmin), W - 1)
    F = np.fft.rfft(fr, 2 * n_fft, axis=1)
    ac = np.fft.irfft(F * np.conj(F), axis=1)[:, :tmax + 2]
    e = np.cumsum(fr ** 2, 1)
    e0 = e[:, W - 1]
    tau = np.arange(tmax + 2)
    shifted = e[:, W - 1 + tau] - np.where(tau > 0, e[:, np.maximum(tau - 1, 0)], 0)
    d = e0[:, None] + shifted - 2 * ac
    d[:, 0] = 0
    cm = d[:, 1:] * np.arange(1, tmax + 2) / (np.cumsum(d[:, 1:], 1) + 1e-12)
    cm = np.concatenate([np.ones((len(cm), 1)), cm], 1)
    f0 = np.zeros(len(cm)); conf = np.zeros(len(cm))
    for i in range(len(cm)):
        c = cm[i, tmin:tmax + 1]
        k = np.where(c < thresh)[0]
        j = k[0] if len(k) else int(np.argmin(c))
        while j + 1 < len(c) and c[j + 1] < c[j]:
            j += 1
        f0[i] = SR / (j + tmin)
        conf[i] = 1 - min(1.0, c[j])
    return f0, conf, t


def load_json(p):
    with open(p) as fh:
        return json.load(fh)


def save_json(obj, p):
    with open(p, "w") as fh:
        json.dump(obj, fh, indent=1)

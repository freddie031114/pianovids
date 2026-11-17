#!/usr/bin/env python3
"""doctor.py: one command that gets this skill ready (or says exactly what is missing).

  python doctor.py            (any Python 3.9+; uses only the standard library)

  1. makes a private Python environment in  ~/.sheet-music-venv  (outside the skill folder, which may be read-only, and
     shared by every chat) and installs numpy + Pillow into it
  2. finds ffmpeg, ffprobe and MuseScore 4 (also where installers put them when PATH was not refreshed)
  3. prints the last line   PY=<path of the python to use for every other script>

Missing ffmpeg/MuseScore are reported with the install command; ask the user before installing anything.
Exit code 0 = ready, 1 = something missing.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(os.path.expanduser("~"), ".sheet-music-venv")
VPY = os.path.join(VENV, "Scripts", "python.exe") if os.name == "nt" else os.path.join(VENV, "bin", "python")


def ensure_venv():
    ok = os.path.exists(VPY) and subprocess.run([VPY, "-c", "import numpy, PIL"], capture_output=True).returncode == 0
    if ok:
        return True
    if sys.version_info < (3, 9):
        print("Python 3.9 or newer is needed (Windows: winget install Python.Python.3.12; macOS: brew install python)")
        return False
    if not os.path.exists(VPY):
        print("creating private environment in", VENV)
        subprocess.run([sys.executable, "-m", "venv", VENV], check=True)
    print("installing numpy and Pillow (one time) ...")
    r = subprocess.run([VPY, "-m", "pip", "install", "-q", "-r", os.path.join(HERE, "requirements.txt")])
    return r.returncode == 0


def check():
    """runs INSIDE the private environment, so it can use common.py."""
    sys.path.insert(0, HERE)
    import common  # noqa: E402
    bad = 0
    for name, finder, hint in (
            ("ffmpeg", lambda: common.find_tool("ffmpeg"), "winget install Gyan.FFmpeg   (macOS: brew install ffmpeg)"),
            ("ffprobe", lambda: common.find_tool("ffprobe"), "comes with ffmpeg"),
            ("MuseScore 4", common.find_mscore, "free from https://musescore.org (or set MSCORE=path)")):
        try:
            print(f"{name:12s} OK  {finder()}")
        except SystemExit:
            print(f"{name:12s} MISSING  -> {hint}")
            bad += 1
    print(f"{'cpu cores':12s} {os.cpu_count()}  (parallel render pieces: {max(1, (os.cpu_count() or 2) - 1)})")
    return bad


def main():
    if "--check" in sys.argv:
        sys.exit(1 if check() else 0)
    if not ensure_venv():
        print("could not set up the Python environment")
        sys.exit(1)
    bad = subprocess.run([VPY, os.path.abspath(__file__), "--check"]).returncode
    print("PY=" + VPY)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

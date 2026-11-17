---
name: sheet-music-basic
description: Makes the finished video in one go - scrolling sheet music over a musician's vertical video, moving in time with the playing. Handles piano (full two-hand score exactly as printed, trills, grace notes, tempo changes) with an animated, colour-graded Instagram Reels / TikTok / Shorts look (notes light up, glowing playhead, title card, fade-out), and also a plain single-line strip (white band, red line) for one instrument. Use when the user says "put sheet music on my video", "add sheet music", "scrolling sheet music", "sheet music reel", "animated sheet music", "make a video with the score", "/sheet-music-basic", or gives a video plus a score file (MusicXML) and the bars played. Piano is fully supported: never say it only does one line.
---

# Sheet Music (scrolling score over a video)

Free from Tim Maines / Creator Conservatory, extended for piano and Reels. The user is a musician, not a programmer:
plain words, never ask them to run commands, never ask them to choose between tools. Be decisive: choose the defaults
below and build the whole video in one go. Ask only for something you cannot get any other way (the video, the score).
How things work and what went wrong before: `gotchas.md` in this folder.

`$SK` = this skill's folder (the "Base directory" shown when the skill loaded). `$PY` = the Python that doctor prints.

## 1. Read the request, then check you can reach the files

From the user's message take: the video path, the score file, the bars played ("bars 140-171", "beats 3 and 4 of bar 67
to the end of 75"), and any wishes: fade-out ("starts at 45 s"), title, dark/light, which part of the video.
- **Piano / two staves -> always the full two-hand score** (Path A). Do not offer a right-hand-only version unless the
  user asks for one. Single melodic line (voice, trumpet, violin ...) and a plain look -> Path B.
- **Cannot open the video path** (the session has no access to the user's computer, e.g. a cloud session, or the file is
  not there): say it ONCE, in one or two sentences: the file is on their computer, so either attach the video to the
  chat or run this chat in the desktop app on their computer (local session). Do not conclude the skill cannot do the
  job and do not present alternatives; carry on as soon as the file is reachable. A video in Google Drive: ask them to
  save it locally (it would arrive as base64 and swamp the session).
- **Score**: a MusicXML file (.musicxml/.mxl; a MuseScore .mscz exported to MusicXML works). If they only have a PDF,
  say a MusicXML is needed (IMSLP / MuseScore.com export) and wait. Never transcribe piano from sound.

## 2. Setup (one command)

`python "$SK/scripts/doctor.py"` - builds a private environment in `~/.sheet-music-venv`, finds ffmpeg and MuseScore 4,
prints `PY=...`. If something is missing it says what to install: ask before installing (winget takes minutes: run it in
the background).

## 3. Path A - piano and Reels: one command

```
"$PY" "$SK/scripts/reel.py" make VIDEO SCORE --from-bar 140 --to-bar 171 --title auto --out FINAL.mp4 [options]
```
Run it in the background (about 2 minutes of prep, then about 7 minutes of render per minute of video; parallel pieces).
It engraves the score cut, times it against the audio, writes proof pictures, renders, and scans the result for flicker.
Options to add when the request or the start check calls for them:

| Situation | Option |
|---|---|
| video starts part-way into the first bar ("beats 3 and 4") | `--from-beat 3` |
| player is already playing in frame 0 and the sound is rising from the start (soft trill, crescendo) | `--first-t 0` |
| "fade out at 45 s" (band fades to nothing by the end) | `--fade-out 45` (or `--fade-out 45 52`) |
| title card: take it from the file / give it yourself | `--title auto`; if the file has no title or composer give both: `--title "Ballade No. 2 in F major, Op. 38" --subtitle "Frederic Chopin"` |
| keep pedal marks | `--pedal` |
| render only a few seconds to check | `--clip 25.5 31` |

While it runs, **open `WORK/proof.png` as soon as it exists** (opening, busiest passage, ending) and `proof_safe.png`
(Instagram/TikTok UI shaded). Check: trills printed as `tr~~~`, grace notes present, nothing wrapped or cut, red line on
the right notes, the band clear of hands, the first note on the playhead at frame 0. Also read the **start check** line
that prep prints (loudness over the first seconds): if the sound is already rising from 0-1 s the first note starts at 0:
re-run with `--first-t 0`. You cannot listen: verify one run against the audio (a pitch that happens once, see
`gotchas.md`). If anything is wrong, stop the render, fix, re-run; otherwise let it finish.

The end of the command must say `RESULT: clean, safe to deliver`. A file that fails the flicker scan must not be delivered.

### Look options (add to the same command; they go to the renderer)

| They say | Option |
|---|---|
| "too dark / bright", "warmer", "no colour grade" | `--grade none` or an ffmpeg filter chain (default `nocturne`) |
| "glow longer / shorter" | `--decay 0.45` (seconds a played note glows) |
| "less dimming of played notes" | `--played-dim 0.55` (1 = none) |
| colours | `--glow-color "#f2c46b"`, `--ink-color`, `--band-color`, `--line-color` |
| "band bigger / lower" | `--band-height 0.22`, `--fill`, `--safe-top 0.10` |
| "playhead further left / thicker" | `--playhead 0.30`, `--line-width 3` |
| "no title" / title timing | leave out `--title`; `--title-in 0.3 --title-out 4.5` |
| faster render | `--jobs N` (default cores-1), `--preset veryfast` |
| "notes early / late" | `align_grand.py ... --nudge-ms 80` then `reel.py render WORK OUT` |

Step by step instead (same result): `reel.py prep` (to the proof pictures), `reel.py render WORK OUT.mp4`, `reel.py clip
WORK clip.mp4 --clip a b`.

When it is done: give the path, say what is shown (both hands, as printed, with trills and grace notes), that you could
not listen and ask them to watch it once, and offer a shorter cut that opens on the best moment and a cover frame.

## 4. Path B - one melodic line (basic, plain look)

1. Work folder next to the video. With a score file (best): `"$PY" "$SK/scripts/engrave.py" SCORE WORK [--part N] [--clef treble|bass|alto|tenor] [--bar-width 1.1]`
   (the file must contain ONLY the bars played; cut it with `trim_score.py SCORE OUT --from-bar A --to-bar B --staff 1`).
   No score file: `transcribe.py VIDEO WORK [--time 3/4] [--clef bass] [--transpose 2] [--bpm 96]`, then
   `engrave.py WORK/melody.musicxml WORK`; pitches come from the sound, the rhythm is a best guess (open
   `melody.musicxml` in MuseScore to fix it). Chords, piano, a band or backing track: ask for the score file.
2. Time it: `"$PY" "$SK/scripts/align.py" VIDEO WORK [--start S] [--end S] [--bpm N]` (`--start` if there is talking first).
3. Proof: `render.py WORK still.png --still 3.0`, look; then `render.py WORK OUT.mp4 [options]` (`--band-top 0.07`,
   `--band-height 0.16`, `--staff 0.4`, `--bars 4`, `--dark --band-color "#15161a"`, `--margin 0 --corner 0`, `--band-color`,
   `--line-color`, `--opacity`, `--playhead`, `--line-width`, `--out-width 540`).
4. Check: `check.py WORK OUT.mp4 WORK/check.jpg` and OPEN the contact sheet (frame 0 not black, line on the notehead,
   nothing covered); median offset over 150 ms or a visible miss: `--start`, `--bpm`, `--nudge-ms`, trim the score to
   the played bars, align and render again. Also run `scan_flicker.py OUT.mp4`.

## 5. Always

- Check the finished file yourself (flicker scan, frame count and length match the source, a few full-size frames).
- Say plainly what is not drawn or not verified. Grace notes are drawn in Path A only; repeats are not unrolled (the
  file must be the notes in played order); timing is good to a few tenths of a second and trill bars are anchored on
  clean left-hand notes.
- Write files with the Write/Edit tools, not shell heredocs with escapes. Use stills and short clips while iterating.
- Machine speed varies: say "about" and measure, do not promise.

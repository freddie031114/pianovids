# Gotchas and how things work (read when something goes wrong, or before changing a finished video)

Everything here was learned the hard way on a real piece (Chopin Nocturne Op. 62 No. 1, trill section, rubato, soft start).

## Where this skill lives (it was lost once)
- Durable copy: `~/.claude/skills/sheet-music-basic/` (local Claude Code and the desktop Code tab). The plugin copy that
  chats list (`...\rpm\...\plugin_*\skills\sheet-music-basic`) is re-synced from the account's plugin catalog and can be
  overwritten; session folders under `local-agent-mode-sessions\skills-plugin\` are deleted with the session. A cloud
  chat sees only the catalog version, so improvements must be re-uploaded there (keep a zip of this folder).
- A cloud session cannot read the user's computer: the video must be attached, or the chat run locally.

## MuseScore refuses a file (silent failure)
- It prints nothing. Exit code 40 (Git Bash) / 1320 (PowerShell). `common.run()` appends the newest MuseScore log's
  ERROR lines to the failure message; the log lives in `%LOCALAPPDATA%\MuseScore\MuseScore4\logs`.
- "Element outside measure ... measure N": a spanner (slur, tie, wedge, trill line) lost one end when bars were cut.
  `build_grand.py` re-ends trill lines and drops other orphans. "Incomplete measure": a bad bar in the source file.
- The file's own `<defaults>` (page size) overrides the wide one-line page, so the music wraps onto several pages.
  `build_grand.py` / `trim_score.py` / `engrave.py` strip it ("kept breaking the music over several lines" = this, or a
  bar with 30+ fast notes that needs `--stretch` / `--bar-width`).
- Playback tempo marks ("quarter = 140" `metronome` directions, plus bare-number words) are dropped: stacked up they
  bury the notes (seen in a Ballade file), and classical scores print words like "Presto con fuoco" instead.
- Long strips (a 32-bar presto is 80+ inches wide) make MuseScore write big numbers in scientific notation in the
  `.spos` files (`x="1.00087e+06"`); every number-reading regex must accept `e+06` or elements go missing
  (`KeyError` in `engrave_grand.py`).
- Downloaded MusicXML often carries hidden playback-only notes (`print-object="no"`: a written-out trill as 32nds in a
  second voice) and dozens of stacked pedal marks. Both are dropped; the printed `tr~~~` stays.

## Trill lines
- MuseScore draws the wavy line through the whole note that carries its STOP. Ending it on the next note overshoots;
  ending it on a hidden playback note a bar later runs it far past. The stop goes on the trilled note itself.
- The importer only copes with `wavy-line number="1"`. Put each stop BEFORE the next start on the same note.
- Grace notes in downloaded files often carry a copy of the trill mark ("tr tr tr"): removed.

## Timing: what the two aligners do and where they fail
- `align.py` (single line): matches heard attacks to written notes in order. A trill floods it with unwritten attacks;
  it can run for many minutes or give nonsense. Use it only for plain one-line playing.
- `align_grand.py` (piano): pass 1 = pitch-class DTW of the WHOLE score (both hands, hidden trill notes included)
  against the audio: follows rubato, good to a few tenths of a second, can be most of a second off in a fast run.
  Pass 2 = locks notes to the onset of their own FUNDAMENTAL (overtones make an octave-up note look like the note below)
  within +-1 s of pass 1, in order. Right hand: notes that are not trilled and whose pitch does not keep returning.
  Left hand: every position (struck once, clean): this is what anchors trill bars. Between anchors < 1 beat apart the
  times are interpolated straight.
- Verify without listening: take a pitch that happens once (the top of a run), find the time of its strongest
  fundamental onset, and check the red line is on that note in a still rendered at that time.
- SOFT STARTS: a note that begins very quietly (pp trill, crescendo hairpin) is placed where it gets LOUD, possibly
  seconds late. Read the "start check" line `align_grand.py` prints (loudness at 0/0.5/1/2/3/4 s). If the player is
  already playing in frame 0 (hands on the keys) use `--first-t 0`. The renderer holds the strip still before the
  first note, so the first note sits on the playhead when the video opens.
- A pick-up start (`--from-beat 3`): the engraving counts beats from 0 at the cut, the score file from the start of the
  bar; `engrave_grand.py` records `from_beat` in notes.json and `align_grand.py` shifts by it automatically.
- `check.py`'s millisecond figure compares against heard attacks; trills and very slow notes break it (it can say
  "TOO FAR" on a good result). Trust stills.

## Rendering and the flicker bug
- Frames are built in Python (about 0.16 s per 1080x1920 frame after the row-caching speed-up, 0.35 s before) and
  piped to ffmpeg; `render_fx.py` renders `--jobs` pieces at once (default cores-1) and joins them without re-encoding.
  Measured: a 57 s video took about 7 minutes on an 8-core laptop; the parallel speed-up is smaller than the core
  count suggests (each piece decodes the source from the start, and encoding competes for the same cores).
- FLICKER: pieces cut from the source with `-ss/-t` (timestamps) could end up a frame apart from the overlay on phone
  video (uneven timestamps): one frame with NO score, at piece joins. Pieces are now cut by FRAME NUMBER
  (`trim=start_frame=..:end_frame=..,setpts=N/(fps*TB)`). Never go back to `-ss` for piece boundaries.
- `scan_flicker.py FINAL.mp4` finds such frames (and steps in the frame timestamps). Run it on every final render;
  `reel.py make/render` does it automatically.
- Do NOT patch bad frames by copy-splicing re-rendered segments into a finished file. Segments encoded at different
  times have different B-frame delay: `inpoint/outpoint` act differently on pts and dts (extra or missing frames, e.g.
  1747 instead of 1705), the timestamps at each seam garble, the duration shrinks and audio drifts. Just re-render.
- Windows: `file=C:/x/y.txt` inside an ffmpeg filter breaks on the colon (write a relative name with `cwd` = temp
  folder). `ffprobe` takes one input file. Delete a stale output BEFORE starting a "notify when the file appears"
  watcher on the same path. MuseScore SVG/PNG/`.spos` come out of `-o strip.svg` as `strip-1.svg` (page number).

## Changing a finished video cheaply
The overlay is baked into every frame, so any drawing change needs a re-render. What is cheap:
- Timing is the expensive part, drawing is cheap: keep `timeline.json`, re-engrave, copy `t` values across by beat
  position (`notes.json` and `timeline.json` match on `q`; a pick-up bar shifts `q` by the beats cut off).
- Start part-way into the first bar: `engrave_grand.py --from-beat 3` (pick-up bar; an empty staff gets a rest).
- MuseScore stretches each line to fill the page: removing content widens ALL remaining spacing, so every frame changes
  by a few pixels. `--width INCHES` reproduces an earlier spacing (secant search, 3-4 engraves).
- Cheap splice (head from a new render + tail copied from the old file) works only when both come from one consistent
  pipeline and frame counts/timestamps check out afterwards; otherwise re-render everything.

## Colour and look
- `--grade nocturne` (warm highlights, cooler shadows, softer greens, highlight roll-off, light vignette, grain)
  suits warm-on-black scores; check a full-size before/after still (skin tones, white keys); lift `gamma` a little
  because phones are viewed dim. `--grade none` or any ffmpeg filter chain also work.
- Reels/Shorts UI covers roughly the top 10%, the right-hand button column and the bottom 20%: `--safe-zones` shades
  them on a still. Score band starts at `--safe-top 0.10`.
- `--fade-out START [END]` fades the whole band (alpha) to nothing; the flicker scan treats it as a slow change.
- Skip particles, bouncing and zooms with classical music. Subtle: light-up on play, dim played notes, trill shimmer,
  pulsing playhead, soft edges, title card.

## Environment
- `doctor.py` builds the private venv in `~/.sheet-music-venv`; `common.find_tool` finds ffmpeg where winget/Homebrew
  put it even when PATH is stale. winget installs take minutes (run in the background).
- Write files with the Write/Edit tools, not shell heredocs with escapes: nested escaping corrupted code twice.

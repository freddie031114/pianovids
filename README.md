# Sheet Music Basic (a Claude Code skill)

Puts scrolling sheet music over your playing video, lined up to the notes you play.

- **One instrument or voice:** a plain strip (one staff, white band, red line).
- **Piano:** the full two-hand score as printed (trill marks, grace notes), timed to your playing even with rubato,
  with an animated Instagram Reels / TikTok / Shorts look: notes light up as you play them, a glowing playhead, a colour
  grade that matches the score, an optional title card and fade-out.

1. Install Claude Code, MuseScore 4 (free, musescore.org), python3 and ffmpeg (the skill checks and tells you what is missing).
2. Move the `sheet-music-basic` folder into `~/.claude/skills/` (Windows: `%USERPROFILE%\.claude\skills\`).
3. Open Claude Code (a LOCAL session, on the computer that has your video) and say, for example:
   "put sheet music on my video C:\...\video.mp4 with this score, bars 140-171, fade the score out from 45 s".
4. Claude builds the whole video in one go, shows you a proof picture, and checks the result itself (including a
   frame-by-frame flicker scan).
5. Ask for changes in plain words: "band lower", "warmer", "glow longer", "notes are a bit late".

Developer notes: `SKILL.md` (the procedure), `gotchas.md` (how it works and what went wrong before), `scripts/doctor.py`
(setup check), `scripts/reel.py` (the piano pipeline: make / prep / render / clip).

Free from Tim Maines / Creator Conservatory (basic path); piano/Reels extensions added later.

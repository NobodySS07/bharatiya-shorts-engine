# Bharatiya Shorts Engine

Automated 60-second YouTube Shorts on Itihasa, Purana and Indian history.

## Setup
1. Python 3.11+, and FFmpeg with libass (`ffmpeg -filters | grep subtitles`).
2. `pip install -r requirements.txt`
3. `cp .env.example .env` and add your `FUELIX_API_KEY`.
4. Optional: put a loop at `assets/bg_music.mp3` (auto-ducked under the voice).

## First run
    python smoke_test.py            # text, embeddings, TTS, word timestamps
    python smoke_test.py --images   # + Imagen anchor, Gemini ref-conditioned scene, vision QA
Open the `smoke_*.png` files and confirm the Gemini scene keeps the anchor's face.
Any FAIL tells you which model name or endpoint to change in `config.py`.

    python main.py

## Pipeline
| Stage | Models (fallback order) |
|---|---|
| Topic + script | claude-sonnet-5-5 -> claude-sonnet-4-6 |
| Fact-check judge (different family) | gemini-3.1-pro -> gpt-5.4 |
| Dedup embeddings | text-embedding-3-small |
| Character anchors (1 per main character, max 3) | imagen-4-ultra -> imagen-4 |
| 12 scenes, conditioned on anchors | gemini-3.1-flash-image -> gemini-2.5-flash-image -> imagen-4 (text-only) |
| Visual QA (text/watermark, drift, anatomy) | gemini-3.5-flash -> gpt-5.4-mini |
| Voice | tts-1-hd / onyx |
| Word timestamps | whisper-1 (falls back to proportional timing) |

## Output (per story in `output/NNN_Name/`)
`script_verified.json`, `fact_check_log.json`, `anchor_c*.png`, `scene_01..12.png`,
`voiceover.mp3`, `subtitles.srt`, `timings.json`, `final_short.mp4`, `upload_metadata.json`.
Every file is a checkpoint: rerunning skips anything already valid on disk.

## Statuses in `data/stories.db`
`queued -> scripted -> media_done -> rendered`. A story that fails `MAX_VIDEO_ATTEMPTS`
times is parked as `failed` with `last_error`. To retry it:
`UPDATE stories SET attempts=0, status='queued' WHERE id=N;`

## Hindi (Sarvam voice)
`LANGUAGE = "hi"` in config.py -> Claude writes Hindi narration, Gemini fact-checks it,
Sarvam Bulbul voices it, Whisper times it (language=hi) and captions burn in Devanagari.
Images and everything else stay on Fuel iX.

1. Put `SARVAM_API_KEY=...` in `.env`.
2. `python sarvam_voice_test.py shubh_hi_stories_dramatic`  (one call, ~₹0.6)
3. Optionally `python sarvam_voice_test.py` to audition the full shortlist (~₹5).
4. Put your favourite voice first in `SARVAM_VOICES`.

Captions use an ASS file with complex text shaping so conjuncts render correctly.
If a Devanagari font isn't found, drop a .ttf (e.g. Noto Sans Devanagari) into `assets/fonts/`
and set `SUBTITLE_FONTS["hi"]` to its family name.

## Music & sound effects
Fill `assets/music/` with instrumental tracks and set each `file` in `assets/music/library.json`
(slots without a file are skipped). Check with `python music.py`. A fast model picks one track per
story (`music_choice.json` in the video folder); tracks are loudness-normalised, faded and ducked
under the voice. Optional SFX: `assets/sfx/whoosh.mp3` (scene changes) and `assets/sfx/impact.mp3` (hook).

## Writer bake-off
`python bakeoff.py` writes the first seed story with every model in `BAKEOFF_MODELS`, saved blind as
`bakeoff/<time>/A.txt…`. Choose a winner, then open `KEY.json`.

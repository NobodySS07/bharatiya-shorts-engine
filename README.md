# Bharatiya Shorts Engine

An automated pipeline that writes, fact-checks, illustrates, narrates and renders
60–90 second vertical YouTube Shorts about Itihasa, Purana and Indian history.
Its main mode is Hindi, with Devanagari captions and Sarvam AI narration. English also works.

One command takes a story from topic to a finished `final_short.mp4`, with a ready-to-paste upload kit
(title, description, tags, playlist and captions).

## How it works

```
topic ──► script ──► fact-check ──► Hindi polish ──► character anchors ──► scenes ──► voice ──► captions ──► render
(seed or   (Claude)   (judge from a    (editor model)   (Imagen, 1 per      (Gemini,    (Sarvam  (Whisper word   (FFmpeg: Ken Burns
 AI-picked,           different model                    main character)     conditioned  / TTS)   timestamps)     motion, music, SFX,
 deduped by           family)                                                on anchors,                           burned-in captions)
 embeddings)                                                                 vision QA)
```

| Stage | Models (in fallback order) |
|---|---|
| Topic and script | claude-sonnet-5-5 → claude-sonnet-4-6 |
| Fact-check judge (different model family) | gemini-3.1-pro → gpt-5.4 |
| Hindi editor and fluency judge | gemini-3.1-pro / gpt-5.4 (never grades its own edit) |
| Duplicate check | text-embedding-3-small (cosine similarity ≥ 0.82 counts as a duplicate) |
| Character anchors (up to 4 per video) | imagen-4-ultra → imagen-4 |
| 12–14 scenes, conditioned on the anchors | gemini-3.1-flash-image → gemini-2.5-flash-image → imagen-4 (text only) |
| Visual QA (text, watermarks, character drift, anatomy) | gemini-3.5-flash → gpt-5.4-mini |
| Voice | Sarvam Bulbul (Hindi) / tts-1-hd (English) |
| Word timestamps | whisper-1, with a pause-anchored fallback |
| Music pick | gemini-3.5-flash |

All text, image and audio calls except Sarvam go through the [Fuel iX](https://fuelix.ai)
OpenAI-compatible API. Model chains, thresholds, visual style and seed topics are all set in
[`config.py`](config.py).

### Reliability
- **Checkpointed:** every output file is a checkpoint. If you rerun after a crash or Ctrl-C, the pipeline skips everything that is already valid on disk.
- **Fallbacks and retries:** each stage tries a chain of models. Transient errors (429, 5xx and timeouts) back off and retry.
- **Cost guard:** image calls are capped per run (`MAX_IMAGE_CALLS_PER_RUN`), and a circuit breaker stops a batch after too many failures.
- **Run lock and backups:** only one run can happen at a time, and the SQLite database is backed up automatically.

## Requirements
- Python 3.10+
- FFmpeg built with libass (check with `ffmpeg -filters | grep subtitles`)
- A Fuel iX API key, plus a [Sarvam AI](https://dashboard.sarvam.ai) key for Hindi
- A Devanagari font (Kohinoor Devanagari ships with macOS; on Linux or Windows use Noto Sans Devanagari)

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env        # then fill in FUELIX_API_KEY and SARVAM_API_KEY
python smoke_test.py        # cheap check of text, embeddings, TTS and timestamps
python smoke_test.py --images   # also tests the anchor, a ref-conditioned scene and vision QA (~3 image calls)
```
If any line says FAIL, that tells you which model name or endpoint to change in `config.py`.

## Usage
```bash
python main.py              # produce VIDEOS_TO_GENERATE new videos (finishes interrupted ones first)
```

Topics run in this order: unfinished stories first, then `SEED_TOPICS` from `config.py`, then
topics the AI picks itself.

### Other tools
| Command | Purpose |
|---|---|
| `python regenerate.py --list` | Show every story and its status |
| `python regenerate.py 1` | Re-render story #1 only (no API cost) |
| `python regenerate.py 1 --from voice` | Redo from the given stage onward (`narration`, `voice`, `scenes`, `anchors` or `script`) |
| `python regenerate.py 1 --scene 4 7` | Redo only scenes 4 and 7, then re-render |
| `python upload_kit.py [N \| --all]` | Build `UPLOAD_KIT.txt` plus captions in `upload_queue/` |
| `python bakeoff.py [--edited \| --editors]` | Blind A/B/C/D comparison of writer or editor models |
| `python sarvam_voice_test.py [voice ids]` | Audition Sarvam Hindi voices into `voice_samples/` |
| `python music.py` | Check the music library |

## Output
Each story gets its own folder, `output/NNN_Name/`:

```
script_verified.json   fact_check_log.json   anchor_c*.png   scene_01..14.png
voiceover.mp3          subtitles.srt         timings.json    music_choice.json
final_short.mp4        upload_metadata.json  UPLOAD_KIT.txt
```

Story status is tracked in `data/stories.db`, moving through
`queued → scripted → media_done → rendered`. A story that fails `MAX_VIDEO_ATTEMPTS`
times is parked as `failed`, and the reason is saved in `last_error`. To retry it:
```sql
UPDATE stories SET attempts=0, status='queued' WHERE id=N;
```

## Customising
- **Language:** set `LANGUAGE = "hi"` or `"en"` in `config.py`.
- **Voice:** put your preferred voice first in `SARVAM_VOICES`.
- **Visual style:** edit `TEMPLE_FRESCO_GLOW_STYLE`.
- **Hindi name spellings:** edit `names_hi.json`. The writer is told to use these spellings, and any mismatches are corrected afterwards.
- **Viewer feedback on Hindi style:** add notes to `hindi_feedback.txt`. They are fed to the writer and the editor.
- **Music:** put instrumental tracks in `assets/music/` and list them in `assets/music/library.json`. Tracks are loudness-normalised, faded and lowered (ducked) under the voice.
- **Sound effects (optional):** add `assets/sfx/whoosh.mp3` for scene changes and `assets/sfx/impact.mp3` for the hook.
- **Fonts:** drop a `.ttf` into `assets/fonts/` and set `SUBTITLE_FONTS`.

Settings added in newer versions are filled in from `config_defaults.py`, so an older `config.py` keeps working.

## Project layout
| File | Role |
|---|---|
| `main.py` | Orchestrator: preflight checks, then the resume, seed and AI-topic loop |
| `script_engine.py` | Topic discovery, script writing, validation and the fact-check loop |
| `hindi_style.py`, `names.py` | Hindi editor, style lint and name normalisation |
| `media_generator.py` | Anchors, scenes, visual QA, voiceover and captions |
| `captions.py` | Word alignment and SRT/ASS caption building (handles Devanagari text shaping) |
| `voice.py` | Sarvam TTS (splits long text into chunks, then joins the WAVs) |
| `music.py` | Music library, LLM track pick and loudness normalisation |
| `video_renderer.py` | FFmpeg rendering: motion, audio mix, hook text, call to action, burned-in captions |
| `fuelix_client.py` | API client: chat, embeddings, images, vision, TTS and transcription |
| `db_manager.py` | SQLite story queue, similarity dedup and backups |
| `utils.py` | Retries, fallbacks, atomic writes and the run lock |

## Security
API keys are read only from `.env` or environment variables. `.env` is in `.gitignore`, so it is never committed.
Generated media (`output/`, `upload_queue/`), the database (`data/`), and logs are also excluded.

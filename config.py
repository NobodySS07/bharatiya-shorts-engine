"""
Central configuration for the Bharatiya Shorts Engine.

Secrets: put FUELIX_API_KEY in a `.env` file next to this file (or export it).
It is deliberately NOT hardcoded here so the repo is safe to push.
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv(path: str) -> None:
    """Tiny .env loader (no extra dependency). Existing env vars win."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv(os.path.join(BASE_DIR, ".env"))

# ==========================================
# 0. BATCH & QUEUE
# ==========================================
VIDEOS_TO_GENERATE = 1        # how many NEW finished videos per run
RESUME_UNFINISHED = True      # finish interrupted videos first
MAX_VIDEO_ATTEMPTS = 3        # after this many failed runs a story is parked as 'failed'

# ==========================================
# 0b. LANGUAGE
# ==========================================
LANGUAGE = "hi"               # "hi" = Hindi narration + Devanagari captions, "en" = English

# ==========================================
# 1. FUEL iX API
# ==========================================
FUELIX_BASE_URL = os.environ.get("FUELIX_BASE_URL", "https://api.fuelix.ai/v1")
FUELIX_API_KEY = os.environ.get("FUELIX_API_KEY", "")
REQUEST_TIMEOUT_S = 180       # image calls can be slow

# ==========================================
# 2. MODELS  (each stage = ordered fallback chain)
# ==========================================
# Writer: newest Sonnet on your list for best long-form + JSON reliability.
WRITER_MODELS = ["claude-sonnet-5-5", "claude-sonnet-4-6"]
TOPIC_MODELS = ["claude-sonnet-5-5", "claude-sonnet-4-6"]

# Judge: a DIFFERENT model family than the writer, so it doesn't share blind spots.
JUDGE_MODELS = ["gemini-3.1-pro", "gpt-5.4"]

# Hindi editor (संपादक): a second model polishes the writer's Hindi for flow.
HINDI_EDITOR_MODELS = ["gemini-3.1-pro", "claude-sonnet-5-5"]
MIN_HOOK_SCORE = 8            # judge's score for the first sentence alone (1-10)
MIN_HINDI_FLUENCY = 8         # judge must score Hindi fluency >= this (1-10)
# Hindi is judged by a model family that did NOT edit it (no grading its own homework).
HINDI_JUDGE_MODELS = ["gpt-5.4", "claude-sonnet-5-5"]

# Cheap vision model for image QA (text/watermark, character drift, anatomy).
VISION_QA_MODELS = ["gemini-3.5-flash", "gpt-5.4-mini"]

EMBEDDING_MODEL = "text-embedding-3-small"   # 1536-dim; must never change once DB has rows

# Anchor (character sheet): highest-quality text-to-image.
ANCHOR_IMAGE_MODELS = ["imagen-4-ultra", "imagen-4"]
# Scenes: Gemini image models accept reference images via chat; Imagen is text-only fallback.
SCENE_IMAGE_MODELS_WITH_REF = ["gemini-3.1-flash-image", "gemini-2.5-flash-image"]
SCENE_IMAGE_FALLBACK_TEXT_ONLY = ["imagen-4", "imagen-4-ultra"]

TTS_MODEL = "tts-1-hd"
TTS_VOICE = "onyx"
TTS_SPEED = 1.0               # pacing is controlled after measuring duration (see below)

# Hindi voice: Sarvam AI (separate key from dashboard.sarvam.ai). OpenAI voices
# sound foreign in Hindi, so there is NO silent fallback to them for Hindi.
SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY", "")
SARVAM_URL = "https://api.sarvam.ai/text-to-speech"
# (model, speaker) pairs tried in order. Audition voices with: python sarvam_voice_test.py
# Copy the exact speaker id from the dashboard's </> code view if it differs.
SARVAM_VOICES = [
    ("bulbul:v4-flash", "anand_hi_documentary"),      # "Anand - Narration": clear, low, slow
    ("bulbul:v4-flash", "shubh_hi_stories_dramatic"),
]
SARVAM_PACE = 1.0             # 0.5-2.0. 1.0 = the voice's natural speed (sounds most human)
SARVAM_MAX_PACE = 1.0         # >1.0 lets long voiceovers be re-synthesised faster; 1.0 = never
SARVAM_TEMPERATURE = 0.45     # lower = steadier, more even delivery (less sing-song)
SARVAM_MAX_CHARS = 1500       # per request (API max is 2500); longer text is split by sentence

# Whisper gives WORD timestamps (needed for caption + slide sync).
TRANSCRIBE_MODELS = ["whisper-1"]

# ==========================================
# 3. THRESHOLDS, LIMITS & GUARDS
# ==========================================
SIMILARITY_THRESHOLD = 0.82   # >= this vs. any existing story => duplicate
MAX_TOPIC_RETRIES = 5
MAX_JUDGE_RETRIES = 3
MAX_DB_BACKUPS = 20

# Hindi uses more (shorter) words per minute than English.
# Hindi measured ~2.2 words/s at pace 1.0, so 125-155 words ≈ 60-70 s.
NARRATION_WORDS = {"en": (130, 165), "hi": (125, 155)}
NARRATION_MIN_WORDS, NARRATION_MAX_WORDS = NARRATION_WORDS[LANGUAGE]
# Scene count adapts to narration length: ~1 image per 5-6 s of speech.
MIN_SCENES, MAX_SCENES = 12, 14
WORDS_PER_SCENE = {"en": 13, "hi": 11}   # writer aims for one scene per this many words
MAX_CHARACTERS = 4            # anchors per video (a disguise/other form counts as its own character)
SCENE_MAX_REFS = 2            # named characters (and reference images) per scene image

API_MAX_ATTEMPTS = 4          # per model, for transient errors (429/5xx/timeouts)
API_BACKOFF_BASE_S = 2.0
API_BACKOFF_MAX_S = 45.0

VISUAL_QA_ENABLED = True
MAX_SCENE_REGENERATIONS = 2   # extra tries when QA rejects a scene (identity mix-ups etc.)
MAX_IMAGE_CALLS_PER_RUN = 80  # hard cost ceiling across the whole run

# Natural pacing first: the video stretches to fit the voice (Shorts may run up to 3 min).
ALLOW_SPEEDUP = {"en": True, "hi": False}   # False = never re-pace or time-stretch narration
TARGET_MAX_DURATION_S = 59.0  # only used when ALLOW_SPEEDUP is True
MAX_ATEMPO = 1.12             # never speed narration beyond this (sounds rushed)
MIN_AUDIO_S, MAX_AUDIO_S = 30.0, 120.0  # sanity window for TTS output

# ==========================================
# 4. PATHS
# ==========================================
DB_PATH = os.path.join(BASE_DIR, "data", "stories.db")
BACKUP_DIR = os.path.join(BASE_DIR, "data", "backups")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOCK_PATH = os.path.join(BASE_DIR, "data", ".run.lock")
BG_MUSIC_PATH = os.path.join(BASE_DIR, "assets", "bg_music.mp3")   # legacy single track (fallback)
BG_MUSIC_VOLUME = 0.12

# Music library: assets/music/library.json + audio files. An LLM picks one track per story.
MUSIC_DIR = os.path.join(BASE_DIR, "assets", "music")
MUSIC_LIBRARY = os.path.join(MUSIC_DIR, "library.json")
MUSIC_PICKER_MODELS = ["gemini-3.5-flash", "claude-sonnet-5-5"]
MUSIC_TARGET_LUFS = -20       # every track normalised to the same loudness before mixing
MUSIC_FADE_IN_S, MUSIC_FADE_OUT_S = 1.0, 1.5

# Optional sound effects (leave the files out to disable)
SFX_DIR = os.path.join(BASE_DIR, "assets", "sfx")
SFX_TRANSITION = os.path.join(SFX_DIR, "whoosh.mp3")    # on every scene change
SFX_HOOK = os.path.join(SFX_DIR, "impact.mp3")          # once, at 0 s
SFX_VOLUME = 0.35

# On-screen call to action in the last seconds (no emoji: caption fonts can't draw them)
CTA_TEXT = {"hi": "रोज़ एक नई गाथा  |  सब्सक्राइब करें", "en": "A new story every day  |  Subscribe"}
CTA_SECONDS = 2.6

# Model bake-off (python bakeoff.py)
BAKEOFF_MODELS = ["claude-sonnet-5-5", "gpt-6.1-terra", "gemini-4-argon", "gemini-3.1-pro"]

# ==========================================
# 5. RENDER
# ==========================================
WIDTH, HEIGHT, FPS = 1080, 1920, 30
# Arial has no Devanagari glyphs. Kohinoor Devanagari ships with macOS;
# on Linux/Windows use "Noto Sans Devanagari" or "Nirmala UI".
SUBTITLE_FONTS = {"en": "Arial", "hi": "Kohinoor Devanagari"}
SUBTITLE_FONT = SUBTITLE_FONTS[LANGUAGE]
SUBTITLE_FONT_SIZES = {"en": 104, "hi": 112}     # pixels on a 1920-tall frame
HOOK_TEXT_SECONDS = 2.6       # on-screen hook title at the start (for sound-off scrollers)
HOOK_FONT_SIZE = 128
FONTS_DIR = os.path.join(BASE_DIR, "assets", "fonts")   # optional: drop .ttf files here
SUBTITLE_WORDS_PER_CAPTION = 3

# ==========================================
# 6. VISUAL STYLE
# ==========================================
TEMPLE_FRESCO_GLOW_STYLE = (
    "Vertical 9:16 composition, Temple Fresco Glow aesthetic. "
    "High-budget 2D epic animation style blending traditional ancient Indian temple mural art "
    "with dramatic cel-shaded volumetric lighting. Deep indigo night shadows, glowing celestial gold "
    "and fiery saffron rim lighting, rich vermilion and antique bronze textures, intricate period-accurate "
    "jewelry and authentic ancient Bharatiya armor and weapons, expressive sharp eyes, crisp dark ink linework. "
    "Absolutely no text, letters, captions, logos or watermarks anywhere in the image."
)

# ==========================================
# 7. SEED TOPICS (processed before AI-invented ones)
# `source_tradition` tells the judge what to check against, so Puranic/folk
# stories aren't wrongly measured against the BORI Critical Edition.
# ==========================================
SEED_TOPICS = [
    {
        "character_name": "Lachit Borphukan",
        "story_title": "The Battle of Saraighat: How One Ahom General Crushed the Mughal Navy",
        "story_summary": "Despite severe illness, Ahom general Lachit Borphukan led a brilliant naval campaign on the Brahmaputra in 1671, routing Ram Singh's Mughal fleet at Saraighat.",
        "script_tone": "Fierce, Patriotic & Tactical",
        "source_tradition": "Documented history (Ahom Buranjis, Mughal accounts)",
    },
    {
        "character_name": "Barbarika (Khatu Shyam)",
        "story_title": "Why Shri Krishna Asked for Barbarika's Head Before the Mahabharata War",
        "story_summary": "Possessing three infallible arrows, Barbarika's vow to always fight for the weaker side would have destroyed everyone on Kurukshetra, leading Krishna to ask for the ultimate sacrifice.",
        "script_tone": "Mythic, Reverent & Awe-Inspiring",
        "source_tradition": "Skanda Purana and Rajasthani/Khatu Shyam folk tradition (NOT in BORI Critical Edition)",
    },
    {
        "character_name": "Chhatrapati Sambhaji Maharaj",
        "story_title": "The Lion of Swarajya Who Fought Four Enemies at Once",
        "story_summary": "For nine years Sambhaji Maharaj defended Hindavi Swarajya against the Mughals, Siddis and Portuguese, and chose martyrdom in 1689 rather than surrender his Dharma.",
        "script_tone": "Heroic, Intense & Reverent",
        "source_tradition": "Documented history (Maratha bakhars, Mughal and Portuguese records)",
    },
    {
        "character_name": "Maharshi Dadhichi",
        "story_title": "The Vedic Sage Who Gave His Bones to Forge Indra's Vajra",
        "story_summary": "When the Asura Vritra overpowered the Devas, Sage Dadhichi gave up his body so his bones could be forged into the Vajra that defeated Vritra.",
        "script_tone": "Spiritual, Epic & Sacrificial",
        "source_tradition": "Mahabharata (Vana Parva, Dadhicha episode), Puranas; Rigvedic allusion to Dadhyanc",
    },
]

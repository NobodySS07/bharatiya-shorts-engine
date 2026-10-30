"""Defaults for settings added after your config.py was created.
Anything missing from config.py is filled in from here (and listed once in the log),
so an older config.py keeps working. To change a value, add it to config.py."""
import os

import config

BASE_DIR = config.BASE_DIR

DEFAULTS = {
    "CAPTION_OFFSET_S": 0.0,
    "MIN_WHISPER_COVERAGE": 0.45,
    "MIN_HOOK_TEXT_SCORE": 8,
    "HINDI_EDITOR_ENABLED": False,
    "EDITOR_BAKEOFF_MODELS": ["gemini-3.1-pro", "gemini-4-argon"],
    "NAMES_FILE": os.path.join(BASE_DIR, "names_hi.json"),
    "LANGUAGE": 'hi',
    "HINDI_EDITOR_MODELS": ['gemini-3.1-pro', 'claude-sonnet-5-5'],
    "MIN_HINDI_FLUENCY": 8,
    "HINDI_JUDGE_MODELS": ['gpt-5.4', 'claude-sonnet-5-5'],
    "MIN_HOOK_SCORE": 8,
    "MIN_SCENES": 12,
    "MAX_SCENES": 14,
    "WORDS_PER_SCENE": {'en': 13, 'hi': 11},
    "NARRATION_WORDS": {'en': (130, 165), 'hi': (125, 155)},
    "ALLOW_SPEEDUP": {'en': True, 'hi': False},
    "TARGET_MAX_DURATION_S": 59.0,
    "SARVAM_MAX_PACE": 1.0,
    "MAX_CHARACTERS": 4,
    "SCENE_MAX_REFS": 2,
    "MAX_SCENE_REGENERATIONS": 2,
    "SUBTITLE_FONTS": {'en': 'Arial', 'hi': 'Kohinoor Devanagari'},
    "SUBTITLE_FONT_SIZES": {'en': 104, 'hi': 112},
    "HOOK_TEXT_SECONDS": 2.6,
    "HOOK_FONT_SIZE": 128,
    "MUSIC_PICKER_MODELS": ['gemini-3.5-flash', 'claude-sonnet-5-5'],
    "MUSIC_TARGET_LUFS": -20,
    "MUSIC_FADE_IN_S": 1.0,
    "MUSIC_FADE_OUT_S": 1.5,
    "SFX_VOLUME": 0.35,
    "CTA_TEXT": {'hi': 'रोज़ एक नई गाथा  |  सब्सक्राइब करें', 'en': 'A new story every day  |  Subscribe'},
    "CTA_SECONDS": 2.6,
    "BAKEOFF_MODELS": ['claude-sonnet-5-5', 'gpt-6.1-terra', 'gemini-4-argon', 'gemini-3.1-pro'],
    "VISION_QA_MODELS": ['gemini-3.5-flash', 'gpt-5.4-mini'],
    "MIN_AUDIO_S": 30.0,
    "MAX_AUDIO_S": 120.0,
    "FONTS_DIR": os.path.join(BASE_DIR, "assets", "fonts"),
    "MUSIC_DIR": os.path.join(BASE_DIR, "assets", "music"),
    "MUSIC_LIBRARY": os.path.join(BASE_DIR, "assets", "music", "library.json"),
    "SFX_DIR": os.path.join(BASE_DIR, "assets", "sfx"),
    "SFX_TRANSITION": os.path.join(BASE_DIR, "assets", "sfx", "whoosh.mp3"),
    "SFX_HOOK": os.path.join(BASE_DIR, "assets", "sfx", "impact.mp3"),
}

MISSING = [k for k in DEFAULTS if not hasattr(config, k)]
for _k in MISSING:
    setattr(config, _k, DEFAULTS[_k])
# derived values that older configs computed differently
if "NARRATION_WORDS" in MISSING:
    config.NARRATION_MIN_WORDS, config.NARRATION_MAX_WORDS = config.NARRATION_WORDS[config.LANGUAGE]
if "SUBTITLE_FONTS" in MISSING:
    config.SUBTITLE_FONT = config.SUBTITLE_FONTS[config.LANGUAGE]

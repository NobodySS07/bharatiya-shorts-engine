"""Checkpointed media: character anchors, 12-14 scenes (+ visual QA), voiceover, captions."""
import json
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List

import config
import config_defaults  # noqa: F401  (fills settings missing from older config.py)
import captions
import db_manager as db
import voice
from fuelix_client import (generate_image_text_only, generate_image_with_refs, transcribe_words,
                           vision_check)
from utils import PipelineError, atomic_write_bytes, atomic_write_json, file_ok, log, looks_like_image


def _read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _image_ok(path: str) -> bool:
    return file_ok(path, 10_000) and looks_like_image(_read(path))


# ------------------------------------------------------------------ anchors
def _never(ch: Dict[str, Any]) -> str:
    return f" Avoid: {ch['never']}." if ch.get("never") else ""


def _anchor_prompt(ch: Dict[str, Any]) -> str:
    return (f"{config.TEMPLE_FRESCO_GLOW_STYLE}\n\nCharacter reference sheet of ONE character: "
            f"{ch['name']}. {ch['visual_description']}{_never(ch)}\n"
            "Full-body front view, centered, neutral stance, plain dark indigo background, "
            "even lighting so face, attire and weapon details are clearly readable. "
            "Single figure only, no other people.")


def generate_anchors(script: Dict[str, Any], ws: str) -> Dict[str, str]:
    paths = {}
    for ch in script["characters"]:
        path = os.path.join(ws, f"anchor_{ch['id']}.png")
        if not _image_ok(path):
            log.info(f"Generating anchor for {ch['name']}")
            data = generate_image_text_only(config.ANCHOR_IMAGE_MODELS, _anchor_prompt(ch),
                                            f"anchor {ch['id']}")
            atomic_write_bytes(path, data)
        paths[ch["id"]] = path
    return paths


# ------------------------------------------------------------------ scenes
def _present(script, scene) -> List[Dict[str, Any]]:
    by_id = {c["id"]: c for c in script["characters"]}
    return [by_id[i] for i in scene.get("characters_present", [])[:config.SCENE_MAX_REFS] if i in by_id]


def _scene_prompt(script, scene, with_refs: bool) -> str:
    """Short, structured prompt. Long prompts with several full descriptions make image models
    blend characters, so each character gets only its key features + what it must NOT look like."""
    present = _present(script, scene)
    prompt = scene["image_prompt"]
    for c in script["characters"]:                    # c1 -> name (word-bounded so c1 != c10)
        prompt = re.sub(rf"\b{re.escape(c['id'])}\b", c["name"], prompt)
    lines = [f"Scene: {prompt}"]
    if present:
        lines.append("")
        lines.append("Characters in this image:" if len(present) > 1 else "Character in this image:")
        for k, c in enumerate(present, 1):
            ref = f" (reference image {k})" if with_refs else ""
            feats = c.get("key_features") or c["visual_description"][:300]
            lines.append(f"- {c['name']}{ref}: {feats}.{_never(c)}")
        if with_refs:
            lines.append("Copy each character's face, skin tone, hair, attire and colours from their OWN "
                         "reference image only. Never swap features between characters. "
                         "Do not copy the reference pose or background.")
        if len(present) == 1:
            lines.append("Only this one named character appears; any other people are anonymous background figures.")
    lines += ["", config.TEMPLE_FRESCO_GLOW_STYLE,
              "Vertical 9:16 frame, main subject in the middle 70% (top and bottom are covered by captions)."]
    return "\n".join(lines)


QA_QUESTION = """Images 1..N-1 are character references in this order: {refs}. The LAST image is a generated scene.
Scene description: {desc}
Expected characters: {chars}
Check the LAST image strictly and answer JSON:
{{"has_text_or_watermark": bool,
 "character_matches_reference": bool (each named character looks like THEIR OWN reference),
 "identity_mixup": bool (a character has another character's traits, e.g. wrong skin colour, another's
   crown/feather/weapon, or the wrong character is shown),
 "forbidden_traits_present": bool (any 'Avoid' trait is visible),
 "anatomy_ok": bool (no extra limbs/fingers, no melted faces), "matches_description": bool,
 "approved": bool, "reason": str}}
If there are no references, judge identity from the expected characters' text only."""


def _qa(scene_bytes: bytes, refs: List[bytes], desc: str, n: int, present=()) -> bool:
    chars = "; ".join(f"{c['name']}: {c.get('key_features', '')}{_never(c)}" for c in present) or "none named"
    names = ", ".join(c["name"] for c in present[:len(refs)]) or "none"
    try:
        v = vision_check(refs + [scene_bytes], QA_QUESTION.format(desc=desc[:600], chars=chars, refs=names),
                         f"qa scene {n}")
    except Exception as e:                             # QA is advisory: never block on QA outage
        log.warning(f"Visual QA unavailable for scene {n}: {e}")
        return True
    ok = (bool(v.get("approved")) and not v.get("has_text_or_watermark") and v.get("anatomy_ok", True)
          and not v.get("identity_mixup") and not v.get("forbidden_traits_present"))
    if not ok:
        log.warning(f"Scene {n} failed QA: {v.get('reason')}")
    return ok


def generate_scenes(script: Dict[str, Any], ws: str, anchors: Dict[str, str]) -> List[str]:
    paths = []
    for sc in script["scenes"]:
        n = int(sc["scene_number"])
        path = os.path.join(ws, f"scene_{n:02d}.png")
        paths.append(path)
        if _image_ok(path):
            continue
        present = _present(script, sc)
        refs = [_read(anchors[c["id"]]) for c in present if c["id"] in anchors]
        best = None
        for attempt in range(config.MAX_SCENE_REGENERATIONS + 1):
            log.info(f"Scene {n:02d}/{len(script['scenes'])} (attempt {attempt + 1})")
            try:
                if refs:
                    data = generate_image_with_refs(config.SCENE_IMAGE_MODELS_WITH_REF,
                                                    _scene_prompt(script, sc, True), refs, f"scene {n}")
                else:
                    raise PipelineError("no characters in scene")
            except PipelineError as e:
                if refs:
                    log.warning(f"Reference-conditioned generation failed for scene {n}; "
                                f"falling back to text-only: {str(e)[:150]}")
                data = generate_image_text_only(config.SCENE_IMAGE_FALLBACK_TEXT_ONLY,
                                                _scene_prompt(script, sc, False), f"scene {n} text-only")
            best = data
            if not config.VISUAL_QA_ENABLED or _qa(data, refs, sc["image_prompt"], n, present):
                break
        atomic_write_bytes(path, best)                 # keep last attempt even if QA unhappy
    return paths


# ------------------------------------------------------------------ audio
def probe_duration(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        raise PipelineError(f"ffprobe could not read {path}: {out.stderr[:200]}")


def generate_voiceover(script: Dict[str, Any], ws: str) -> str:
    raw = os.path.join(ws, "voiceover_raw.mp3")
    final = os.path.join(ws, "voiceover.mp3")
    if file_ok(final, 20_000):
        return final
    lang = script.get("language", "en")
    if not file_ok(raw, 20_000):
        log.info(f"Generating voiceover ({lang})")
        atomic_write_bytes(raw, voice.synthesize(script["phonetic_narration"], lang))
    dur = probe_duration(raw)

    # Too long? For Sarvam, re-synthesise ONCE at a faster natural pace (sounds far better
    # than time-stretching). Costs one more voiceover (~₹2.5).
    speedup = config.ALLOW_SPEEDUP.get(lang, True)
    if speedup and lang == "hi" and dur > config.TARGET_MAX_DURATION_S + 1.0:
        pace = min(config.SARVAM_MAX_PACE,
                   config.SARVAM_PACE * dur / (config.TARGET_MAX_DURATION_S - 0.5))
        if pace > config.SARVAM_PACE + 0.02:
            log.info(f"Voiceover {dur:.1f}s is too long; re-synthesising at pace {pace:.2f}")
            atomic_write_bytes(raw, voice.synthesize(script["phonetic_narration"], lang, pace))
            dur = probe_duration(raw)

    if not (config.MIN_AUDIO_S <= dur <= config.MAX_AUDIO_S):
        os.remove(raw)
        raise PipelineError(f"Voiceover is {dur:.1f}s even after re-pacing; the script is too long. "
                            f"Lower NARRATION_WORDS in config.py.")
    if speedup and dur / config.MAX_ATEMPO > config.TARGET_MAX_DURATION_S + 1.0:
        log.warning(f"Voiceover will still be ~{dur / config.MAX_ATEMPO:.0f}s after max speed-up; "
                    "consider a shorter script.")
    if not speedup:
        log.info(f"Voiceover {dur:.1f}s at natural pace (no speed-up); video will match it.")
    if speedup and dur > config.TARGET_MAX_DURATION_S:
        tempo = min(config.MAX_ATEMPO, dur / config.TARGET_MAX_DURATION_S)
        log.info(f"Voiceover {dur:.1f}s -> speeding up x{tempo:.3f}")
        tmp = final + ".part.mp3"
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", raw, "-filter:a",
                            f"atempo={tempo:.4f}", "-b:a", "192k", tmp], capture_output=True, text=True)
        if r.returncode != 0:
            raise PipelineError(f"atempo failed: {r.stderr[-300:]}")
        os.replace(tmp, final)
    else:
        shutil.copyfile(raw, final)
    return final


def detect_pauses(audio: str, min_pause: float = 0.18):
    """Silences in the (clean, music-free) voiceover: [(start, end)], plus speech start/end."""
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", audio, "-af",
                        f"silencedetect=noise=-35dB:d={min_pause}", "-f", "null", "-"],
                       capture_output=True, text=True)
    import re as _re
    st = [float(x) for x in _re.findall(r"silence_start: ([0-9.]+)", r.stderr)]
    en = [float(x) for x in _re.findall(r"silence_end: ([0-9.]+)", r.stderr)]
    dur = probe_duration(audio)
    pauses = list(zip(st, en))
    speech_start = pauses[0][1] if pauses and pauses[0][0] < 0.05 else 0.0
    speech_end = st[-1] if len(st) > len(en) else (pauses[-1][0] if pauses and pauses[-1][1] >= dur - 0.05 else dur)
    inner = [(a, b) for a, b in pauses if a > speech_start + 0.05 and b < speech_end - 0.05]
    return inner, speech_start, speech_end


def generate_captions(script: Dict[str, Any], ws: str, audio: str) -> Dict[str, Any]:
    srt_path = os.path.join(ws, "subtitles.srt")
    timing_path = os.path.join(ws, "timings.json")
    if file_ok(srt_path) and file_ok(timing_path) and file_ok(os.path.join(ws, "subtitles.ass")):
        with open(timing_path, encoding="utf-8") as fh:
            return json.load(fh)
    duration = probe_duration(audio)
    lang = script.get("language", "en")
    expected = len(script["full_narration"].split())
    words, method = None, None
    try:
        heard = transcribe_words(audio, language=lang, hint=script["full_narration"])
        coverage = len(heard) / max(1, expected)
        if coverage < 0.75:
            # the text hint can make Whisper skip passages; try once without it, keep the better one
            plain = transcribe_words(audio, language=lang, hint="")
            if len(plain) > len(heard):
                heard, coverage = plain, len(plain) / max(1, expected)
        log.info(f"Whisper returned {len(heard)} words for {expected} expected ({coverage:.0%}).")
        if coverage < config.MIN_WHISPER_COVERAGE:
            raise ValueError(f"word timestamps cover only {coverage:.0%} of the narration")
        aligned = captions.align(script["full_narration"], script["phonetic_narration"], heard, duration)
        pauses, s0, s1 = detect_pauses(audio)
        words = captions.hybrid(aligned, captions.pause_anchored(script["full_narration"], pauses, s0, s1))
        q = captions.alignment_quality(words)
        log.info(f"Caption timing: {sum(1 for w in words if w.get('anchored'))}/{len(words)} words pinned "
                 f"by Whisper, the rest by {len(pauses)} voice pauses.")
        log.info(f"Caption sync: longest word {q['max_word_s']:.2f}s, longest gap {q['max_gap_s']:.2f}s, "
                 f"{q['long_words']} suspicious words")
        if q["long_words"] > 2 or q["max_word_s"] > 3.0:
            raise ValueError(f"alignment looks wrong ({q})")
        method = "whisper+pauses"
    except Exception as e:
        log.warning(f"Whisper timing not usable ({str(e)[:150]}); using the voice's own pauses instead.")
    if words is None:
        pauses, s0, s1 = detect_pauses(audio)
        words = captions.pause_anchored(script["full_narration"], pauses, s0, s1)
        method = "pause-anchored"
        log.info(f"Caption timing from {len(pauses)} detected pauses (speech {s0:.2f}-{s1:.2f}s).")
    off = float(getattr(config, "CAPTION_OFFSET_S", 0.0) or 0.0)
    if off:                                          # manual nudge: + delays captions, - shows earlier
        words = [dict(w, start=max(0.0, w["start"] + off), end=max(0.05, w["end"] + off)) for w in words]
    srt = captions.build_srt(words, config.SUBTITLE_WORDS_PER_CAPTION)
    atomic_write_bytes(srt_path, srt.encode("utf-8"))
    font = config.SUBTITLE_FONTS.get(script.get("language", "en"), config.SUBTITLE_FONT)
    ass = captions.build_ass(words, font, config.WIDTH, config.HEIGHT,
                             config.SUBTITLE_WORDS_PER_CAPTION,
                             config.SUBTITLE_FONT_SIZES.get(script.get("language", "en"), 96),
                             hook_text=script.get("hook_text", ""),
                             hook_seconds=config.HOOK_TEXT_SECONDS, hook_size=config.HOOK_FONT_SIZE,
                             cta_text=config.CTA_TEXT.get(script.get("language", "en"), ""),
                             cta_seconds=config.CTA_SECONDS, end_time=duration)
    atomic_write_bytes(os.path.join(ws, "subtitles.ass"), ass.encode("utf-8"))
    timings = {"method": method, "audio_duration": duration, "words": words,
               "scene_durations": captions.scene_durations(words, script["scenes"], duration)}
    atomic_write_json(timing_path, timings)
    return timings


# ------------------------------------------------------------------ orchestration
def generate_all_media(topic: Dict[str, Any], script: Dict[str, Any], ws: str) -> Dict[str, Any]:
    anchors = generate_anchors(script, ws)
    scenes = generate_scenes(script, ws, anchors)
    audio = generate_voiceover(script, ws)
    timings = generate_captions(script, ws, audio)
    music_file = None
    try:
        import music
        track = music.pick_track(script, ws)
        if track:
            music_file = music.prepared_path(track)
    except Exception as e:                           # music is a nice-to-have, never fatal
        log.warning(f"Music unavailable for this video: {e}")
    db.update_story(topic["id"], status="media_done")
    return {"scenes": scenes, "audio": audio, "srt": os.path.join(ws, "subtitles.srt"),
            "timings": timings, "music": music_file}

"""Voiceover synthesis. Hindi -> Sarvam Bulbul, English -> Fuel iX tts-1-hd.
Returns MP3 bytes either way."""
import base64
import io
import os
import re
import subprocess
import tempfile
import wave
from typing import List

import httpx

import config
import config_defaults  # noqa: F401  (fills settings missing from older config.py)
from utils import NonRetriable, PipelineError, TransientHTTP, log, retry_call


def synthesize(text: str, language: str, pace: float = None) -> bytes:
    if language == "hi":
        return sarvam_tts(text, pace or config.SARVAM_PACE)
    from fuelix_client import tts
    return tts(text)


# ------------------------------------------------------------------ Sarvam
def _chunks(text: str, limit: int) -> List[str]:
    """Split on sentence ends (। . ! ?) so each request stays under the char limit."""
    sentences = [s.strip() for s in re.split(r"(?<=[।.!?])\s+", text) if s.strip()]
    out, cur = [], ""
    for s in sentences:
        if len(s) > limit:                         # pathological long sentence: split on spaces
            for i in range(0, len(s), limit):
                out.append(s[i:i + limit])
            continue
        if cur and len(cur) + 1 + len(s) > limit:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


# Sarvam's docs name the language field differently on different pages; use whichever works.
_LANG_FIELDS = ["target_language_code", "language_code"]


def _sarvam_request(text: str, model: str, speaker: str, pace: float = None) -> bytes:
    headers = {"api-subscription-key": config.SARVAM_API_KEY, "Content-Type": "application/json"}
    for attempt, field in enumerate(list(_LANG_FIELDS)):
        payload = {
            "text": text, "model": model, "speaker": speaker, field: "hi-IN",
            "pace": round(pace or config.SARVAM_PACE, 2), "temperature": config.SARVAM_TEMPERATURE,
            "speech_sample_rate": 48000, "output_audio_codec": "wav",
        }
        try:
            r = httpx.post(config.SARVAM_URL, json=payload, headers=headers, timeout=120)
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            raise TransientHTTP(f"network: {e}") from e
        if r.status_code in (400, 422) and "language_code" in r.text and attempt == 0:
            _LANG_FIELDS.reverse()                 # remember the working name for later calls
            continue
        break
    if r.status_code == 429 or r.status_code >= 500:
        raise TransientHTTP(f"HTTP {r.status_code}: {r.text[:300]}")
    if r.status_code in (401, 403):
        raise NonRetriable(f"Sarvam auth failed (check SARVAM_API_KEY / credits): {r.text[:300]}")
    if r.status_code != 200:
        raise NonRetriable(f"Sarvam HTTP {r.status_code}: {r.text[:400]}")
    audios = r.json().get("audios") or []
    if not audios:
        raise ValueError("Sarvam returned no audio")
    data = base64.b64decode("".join(audios))
    if len(data) < 5_000 or data[:4] != b"RIFF":
        raise ValueError("Sarvam audio is empty or not WAV")
    return data


def _concat_wavs(parts: List[bytes]) -> bytes:
    params, frames = None, []
    for p in parts:
        with wave.open(io.BytesIO(p)) as w:
            if params is None:
                params = w.getparams()
            elif (w.getnchannels(), w.getsampwidth(), w.getframerate()) != \
                    (params.nchannels, params.sampwidth, params.framerate):
                raise PipelineError("Sarvam chunks came back in different audio formats")
            frames.append(w.readframes(w.getnframes()))
            frames.append(b"\x00" * int(params.framerate * 0.25) * params.sampwidth * params.nchannels)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setparams(params)
        out.writeframes(b"".join(frames[:-1]))     # drop trailing pause
    return buf.getvalue()


def _wav_to_mp3(wav: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "in.wav"), os.path.join(d, "out.mp3")
        open(src, "wb").write(wav)
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-ac", "1", "-ar", "48000",
                            "-b:a", "192k", dst], capture_output=True, text=True)
        if r.returncode != 0:
            raise PipelineError(f"WAV->MP3 failed: {r.stderr[-300:]}")
        return open(dst, "rb").read()


def sarvam_tts(text: str, pace: float = None) -> bytes:
    if not config.SARVAM_API_KEY:
        raise PipelineError("SARVAM_API_KEY missing: add it to .env (get one at dashboard.sarvam.ai).")
    chunks = _chunks(text, config.SARVAM_MAX_CHARS)
    errors = []
    for model, speaker in config.SARVAM_VOICES:
        try:
            parts = [retry_call(lambda c=c: _sarvam_request(c, model, speaker, pace),
                                f"sarvam [{model}/{speaker}] chunk {i + 1}/{len(chunks)}")
                     for i, c in enumerate(chunks)]
            log.info(f"Hindi voiceover: {model} / {speaker}, pace {pace or config.SARVAM_PACE:.2f} "
                     f"({len(chunks)} chunk(s))")
            return _wav_to_mp3(_concat_wavs(parts))
        except (NonRetriable, PipelineError) as e:
            # whole voice fails over together, so one video never mixes two voices
            log.warning(f"Sarvam voice {model}/{speaker} failed, trying next voice: {str(e)[:200]}")
            errors.append(f"{model}/{speaker}: {e}")
    raise PipelineError("All Sarvam voices failed:\n  " + "\n  ".join(errors))

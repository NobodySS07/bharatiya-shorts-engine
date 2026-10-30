"""Thin wrappers over the OpenAI-compatible Fuel iX API.
Every call goes through utils.retry_call / with_fallback."""
import base64
import json
import re
from typing import Any, Dict, List, Optional

import openai
from openai import OpenAI

import config
import config_defaults  # noqa: F401  (fills settings missing from older config.py)
from utils import PipelineError, extract_json, log, looks_like_image, with_fallback, retry_call

if not config.FUELIX_API_KEY:
    raise SystemExit("FUELIX_API_KEY is not set. Put it in .env (see .env.example).")

client = OpenAI(api_key=config.FUELIX_API_KEY, base_url=config.FUELIX_BASE_URL,
                timeout=config.REQUEST_TIMEOUT_S, max_retries=0)  # we handle retries ourselves


class ImageBudget:
    used = 0

    @classmethod
    def spend(cls):
        if cls.used >= config.MAX_IMAGE_CALLS_PER_RUN:
            raise PipelineError(f"Image budget exhausted ({config.MAX_IMAGE_CALLS_PER_RUN} calls this run).")
        cls.used += 1


# ------------------------------------------------------------------ text
def _chat(model: str, system: str, user: Any, temperature: float, json_mode: bool) -> str:
    kwargs: Dict[str, Any] = dict(
        model=model, temperature=temperature,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = client.chat.completions.create(**kwargs)
    except openai.BadRequestError:
        if not json_mode:
            raise
        kwargs.pop("response_format")          # some routed models reject json mode
        resp = client.chat.completions.create(**kwargs)
    if not resp.choices:
        raise ValueError("no choices returned")
    content = resp.choices[0].message.content
    if not content:
        raise ValueError(f"empty content (finish_reason={resp.choices[0].finish_reason})")
    return content


def chat_json(models: List[str], system: str, user: Any, what: str,
              temperature: float = 0.7, validator=None) -> Dict[str, Any]:
    """Chat call that must return a JSON object. `validator(obj)` may raise ValueError
    to force a retry (e.g. schema problems)."""
    def call(model):
        obj = extract_json(_chat(model, system, user, temperature, json_mode=True))
        if not isinstance(obj, dict):
            raise ValueError("expected a JSON object")
        if validator:
            validator(obj)
        return obj
    return with_fallback(models, call, what)


# ------------------------------------------------------------------ embeddings
def embed(text: str) -> List[float]:
    def call():
        resp = client.embeddings.create(model=config.EMBEDDING_MODEL, input=text[:8000])
        vec = resp.data[0].embedding
        if not vec or len(vec) < 64:
            raise ValueError("embedding missing/too short")
        return vec
    return retry_call(call, f"embed [{config.EMBEDDING_MODEL}]")


# ------------------------------------------------------------------ images
def _find_image_bytes(obj: Any) -> Optional[bytes]:
    """Proxies return generated images in different shapes (b64_json, data: URLs inside
    content parts, message.images, inline markdown). Walk the whole response."""
    found: List[bytes] = []

    def visit(x):
        if found:
            return
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("b64_json", "image_base64", "data") and isinstance(v, str) and len(v) > 1000:
                    try:
                        b = base64.b64decode(v.split(",", 1)[-1], validate=False)
                        if looks_like_image(b):
                            found.append(b); return
                    except Exception:
                        pass
                visit(v)
        elif isinstance(x, list):
            for v in x:
                visit(v)
        elif isinstance(x, str) and "data:image" in x:
            m = re.search(r"data:image/[a-z]+;base64,([A-Za-z0-9+/=\s]+)", x)
            if m:
                try:
                    b = base64.b64decode(m.group(1))
                    if looks_like_image(b):
                        found.append(b)
                except Exception:
                    pass

    visit(obj)
    return found[0] if found else None


def _download(url: str) -> bytes:
    import urllib.request
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def generate_image_text_only(models: List[str], prompt: str, what: str) -> bytes:
    """images/generations endpoint (Imagen). Tries a 9:16 size, then the default size;
    the renderer crops to 1080x1920 either way."""
    def call(model):
        ImageBudget.spend()
        last_err = None
        for size in ("1024x1792", "1024x1536", None):
            kwargs = dict(model=model, prompt=prompt, n=1)
            if size:
                kwargs["size"] = size
            try:
                resp = client.images.generate(**kwargs, response_format="b64_json")
            except openai.BadRequestError as e:
                last_err = e
                try:  # some models reject response_format
                    resp = client.images.generate(**kwargs)
                except openai.BadRequestError as e2:
                    last_err = e2
                    continue
            item = resp.data[0] if resp.data else None
            data = None
            if item is not None and getattr(item, "b64_json", None):
                data = base64.b64decode(item.b64_json)
            elif item is not None and getattr(item, "url", None):
                data = _download(item.url)
            if data and looks_like_image(data):
                return data
            raise ValueError("image response had no valid image")
        raise last_err or ValueError("no size accepted")
    return with_fallback(models, call, what)


def generate_image_with_refs(models: List[str], prompt: str, ref_images: List[bytes], what: str) -> bytes:
    """Gemini image models via chat/completions, passing reference images as image_url parts."""
    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for ref in ref_images:
        content.append({"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(ref).decode()}})

    def call(model):
        ImageBudget.spend()
        msgs = [{"role": "user", "content": content}]
        try:
            resp = client.chat.completions.create(
                model=model, messages=msgs, extra_body={"modalities": ["image", "text"]})
        except openai.BadRequestError:
            resp = client.chat.completions.create(model=model, messages=msgs)
        data = _find_image_bytes(resp.model_dump())
        if not data:
            raise ValueError("model replied without an image")
        return data
    return with_fallback(models, call, what)


def vision_check(images: List[bytes], question: str, what: str) -> Dict[str, Any]:
    content: List[Dict[str, Any]] = [{"type": "text", "text": question}]
    for img in images:
        content.append({"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(img).decode()}})
    return chat_json(config.VISION_QA_MODELS,
                     "You are a strict visual QA inspector. Reply with JSON only.",
                     content, what, temperature=0)


# ------------------------------------------------------------------ audio
def tts(text: str) -> bytes:
    def call():
        resp = client.audio.speech.create(model=config.TTS_MODEL, voice=config.TTS_VOICE,
                                          input=text, speed=config.TTS_SPEED,
                                          response_format="mp3")
        data = resp.read() if hasattr(resp, "read") else resp.content
        if not data or len(data) < 20_000:
            raise ValueError("TTS returned suspiciously small audio")
        return data
    return retry_call(call, f"tts [{config.TTS_MODEL}]")


def transcribe_words(audio_path: str, language: str = "en", hint: str = "") -> List[Dict[str, Any]]:
    """Returns [{'word', 'start', 'end'}]. Raises if word timestamps unavailable.
    `language` stops Whisper writing Hindi in Urdu/Latin script; `hint` (the script's own
    opening text) biases it toward the correct spellings of names."""
    def call(model):
        with open(audio_path, "rb") as fh:
            kwargs = dict(model=model, file=fh, response_format="verbose_json",
                          timestamp_granularities=["word"], language=language)
            if hint:
                kwargs["prompt"] = hint[:400]
            resp = client.audio.transcriptions.create(**kwargs)
        d = resp.model_dump() if hasattr(resp, "model_dump") else json.loads(str(resp))
        words = d.get("words") or []
        if not words:
            raise ValueError("no word timestamps in transcription")
        return [{"word": w["word"], "start": float(w["start"]), "end": float(w["end"])} for w in words]
    return with_fallback(config.TRANSCRIBE_MODELS, call, "transcribe")

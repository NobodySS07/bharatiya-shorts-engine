"""Shared helpers: logging, retries with backoff, model fallback chains,
atomic file writes, tolerant JSON parsing, file validation and a run lock."""
import json
import logging
import os
import random
import re
import tempfile
import time
from typing import Any, Callable, Iterable, Optional

import openai

import config
import config_defaults  # fills settings missing from an older config.py

# ------------------------------------------------------------------ logging
os.makedirs(config.LOG_DIR, exist_ok=True)
log = logging.getLogger("shorts")
if not log.handlers:
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    fh = logging.FileHandler(os.path.join(config.LOG_DIR, "pipeline.log"), encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(sh)
    log.addHandler(fh)
    if config_defaults.MISSING:
        log.info(f"config.py is older than the code; using defaults for: {', '.join(config_defaults.MISSING)}")


# ------------------------------------------------------------------ errors
class PipelineError(Exception):
    """A failure that should mark the current video as failed (not crash the batch)."""


class TransientHTTP(Exception):
    """Retriable error from a non-OpenAI HTTP API (429 / 5xx / network)."""


class NonRetriable(Exception):
    """Wraps errors where retrying the same model is pointless (bad request, auth, 404)."""


TRANSIENT = (
    TransientHTTP,
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
)
PERMANENT = (
    openai.BadRequestError,
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    openai.NotFoundError,
    openai.UnprocessableEntityError,
)


def _retry_after_seconds(exc: Exception) -> Optional[float]:
    resp = getattr(exc, "response", None)
    if resp is None:
        return None
    try:
        val = resp.headers.get("retry-after")
        return float(val) if val else None
    except Exception:
        return None


def retry_call(fn: Callable[[], Any], what: str,
               attempts: int = config.API_MAX_ATTEMPTS) -> Any:
    """Run fn() with exponential backoff + jitter on transient errors.
    Permanent API errors raise NonRetriable immediately."""
    last: Optional[Exception] = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except PERMANENT as e:
            raise NonRetriable(f"{what}: {type(e).__name__}: {e}") from e
        except TRANSIENT as e:
            last = e
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            # malformed/empty model output — a retry often fixes it
            last = e
        except openai.APIStatusError as e:
            if e.status_code and 400 <= e.status_code < 500 and e.status_code not in (408, 409, 429):
                raise NonRetriable(f"{what}: HTTP {e.status_code}: {e}") from e
            last = e
        if i < attempts:
            wait = _retry_after_seconds(last) or min(
                config.API_BACKOFF_MAX_S, config.API_BACKOFF_BASE_S * (2 ** (i - 1)))
            wait += random.uniform(0, 1.0)
            log.warning(f"{what} failed ({type(last).__name__}: {str(last)[:160]}); "
                        f"retry {i}/{attempts - 1} in {wait:.1f}s")
            time.sleep(wait)
    raise PipelineError(f"{what}: gave up after {attempts} attempts: {last}")


def with_fallback(models: Iterable[str], fn: Callable[[str], Any], what: str) -> Any:
    """Try each model in order (each with its own retries). Returns fn(model)'s result."""
    errors = []
    for model in models:
        try:
            result = retry_call(lambda: fn(model), f"{what} [{model}]")
            return result
        except (NonRetriable, PipelineError) as e:
            log.warning(f"{what}: model {model} failed -> {str(e)[:200]}")
            errors.append(f"{model}: {e}")
    raise PipelineError(f"{what}: all models failed:\n  " + "\n  ".join(errors))


# ------------------------------------------------------------------ files
def atomic_write_bytes(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".part")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def atomic_write_json(path: str, obj: Any) -> None:
    atomic_write_bytes(path, json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))


def file_ok(path: str, min_bytes: int = 1) -> bool:
    return os.path.isfile(path) and os.path.getsize(path) >= min_bytes


def looks_like_image(data: bytes) -> bool:
    if not data or len(data) < 10_000:      # a real 9:16 render is far larger than 10 KB
        return False
    return (data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff"
            or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"))


def sanitize_name(name: str, max_len: int = 40) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return (s or "story")[:max_len]


# ------------------------------------------------------------------ JSON
def extract_json(text: str) -> Any:
    """Parse JSON from model output, tolerating ```json fences and chatter."""
    if not text or not text.strip():
        raise ValueError("empty model response")
    t = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start = min([i for i in (t.find("{"), t.find("[")) if i != -1], default=-1)
    if start == -1:
        raise ValueError("no JSON object found in model response")
    closer = "}" if t[start] == "{" else "]"
    end = t.rfind(closer)
    return json.loads(t[start:end + 1])


def word_count(text: str) -> int:
    """Unicode-safe (Devanagari matras are not \\w in Python regex, so split on spaces)."""
    return len([t for t in (text or "").split() if norm_token(t)])


_NUKTA, _CHANDRABINDU, _ANUSVARA = "\u093c", "\u0901", "\u0902"


def norm_token(tok: str) -> str:
    """Lowercase, drop punctuation (incl. Hindi danda), keep letters/marks/digits in ANY script.
    Folds nukta and chandrabindu so Whisper spelling variants still match."""
    import unicodedata
    t = unicodedata.normalize("NFC", tok.lower())
    t = t.replace(_NUKTA, "").replace(_CHANDRABINDU, _ANUSVARA)
    return "".join(ch for ch in t if unicodedata.category(ch)[0] in ("L", "M", "N"))


# ------------------------------------------------------------------ lock
class RunLock:
    """Prevents two pipeline runs from clobbering the same DB/output."""

    def __enter__(self):
        os.makedirs(os.path.dirname(config.LOCK_PATH), exist_ok=True)
        if os.path.exists(config.LOCK_PATH):
            try:
                pid = int(open(config.LOCK_PATH).read().strip() or 0)
            except ValueError:
                pid = 0
            if pid and _pid_alive(pid):
                raise SystemExit(f"Another run is active (pid {pid}). Delete {config.LOCK_PATH} if stale.")
            log.warning("Removing stale run lock.")
        with open(config.LOCK_PATH, "w") as fh:
            fh.write(str(os.getpid()))
        return self

    def __exit__(self, *exc):
        try:
            os.remove(config.LOCK_PATH)
        except FileNotFoundError:
            pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False
    except Exception:
        return True

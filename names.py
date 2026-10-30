"""One spelling per name: auto-correct known variants (names_hi.json) in every Hindi text field,
and give the writer the canonical list up front."""
import json
import os
import re
from typing import Any, Dict, List, Tuple

import config
import config_defaults  # noqa: F401
from utils import log

_PUNCT = "।॥,.!?;:\"'“”‘’()—-"


def _load() -> Dict[str, Any]:
    try:
        with open(config.NAMES_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"names": [], "prefix": []}


def glossary_prompt() -> str:
    g = _load()
    if not g["names"]:
        return ""
    pairs = ", ".join(f"{n['en']} = {n['hi']}" for n in g["names"])
    return f"\n\nनामों की तय वर्तनी (हमेशा यही लिखिए): {pairs}"


def _fix_text(text: str, g: Dict[str, Any]) -> Tuple[str, List[str]]:
    changes = []
    # multi-word variants first (longest first), on whitespace boundaries
    variants = sorted(((v, n["hi"]) for n in g["names"] for v in n.get("variants", [])),
                      key=lambda x: -len(x[0]))
    for wrong, right in variants:
        pat = rf"(?<!\S){re.escape(wrong)}(?=[\s{re.escape(_PUNCT)}]|$)"
        text, k = re.subn(pat, right, text)
        if k:
            changes.append(f"{wrong}→{right}")
    for p in g.get("prefix", []):
        pat = rf"(?<!\S){re.escape(p['from'])}(?!\u093c)"      # skip words that already have the nukta
        text, k = re.subn(pat, p["to"], text)
        if k:
            changes.append(f"{p['from']}…→{p['to']}…")
    return text, changes


def normalise_script(script: Dict[str, Any]) -> Dict[str, Any]:
    """Applies canonical spellings to narration, scenes and all Hindi metadata (in place)."""
    if script.get("language") != "hi":
        return script
    g = _load()
    all_changes = []
    for key in ("title", "youtube_title", "youtube_description", "hook_text", "engagement_question",
                "full_narration", "phonetic_narration"):
        if isinstance(script.get(key), str):
            script[key], ch = _fix_text(script[key], g)
            all_changes += ch
    for sc in script.get("scenes", []):
        sc["narration_segment"], ch = _fix_text(sc.get("narration_segment", ""), g)
        all_changes += ch
    if all_changes:
        log.info(f"Name spellings standardised: {', '.join(sorted(set(all_changes)))}")
    return script

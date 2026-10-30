"""Background music: load assets/music/library.json, let an LLM pick the best track for a
story, and prepare a loudness-normalised copy for mixing.

    python music.py            # check the library: which slots have files, durations, problems
"""
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

import config
import config_defaults  # noqa: F401  (fills settings missing from older config.py)
from utils import atomic_write_json, log


def load_library() -> Dict[str, Any]:
    if not os.path.isfile(config.MUSIC_LIBRARY):
        return {"default": None, "tracks": []}
    with open(config.MUSIC_LIBRARY, encoding="utf-8") as fh:
        lib = json.load(fh)
    lib.setdefault("tracks", [])
    return lib


def track_path(t: Dict[str, Any]) -> str:
    return os.path.join(config.MUSIC_DIR, t.get("file", ""))


def available_tracks() -> List[Dict[str, Any]]:
    return [t for t in load_library()["tracks"] if t.get("file") and os.path.isfile(track_path(t))]


PICKER_SYSTEM = """You are the music supervisor for 60-second Indian history and mythology Shorts.
Pick the ONE background track whose mood best supports the whole story, especially its emotional
arc and ending. Consider the narration, the tone and the source tradition. The music sits quietly
under narration. Return JSON only: {"id": str, "reason": str}"""


def pick_track(script: Dict[str, Any], ws: str) -> Optional[Dict[str, Any]]:
    """Returns the chosen track dict (cached in music_choice.json), or None if no music."""
    tracks = available_tracks()
    cache = os.path.join(ws, "music_choice.json")
    if os.path.isfile(cache):
        try:
            chosen = json.load(open(cache, encoding="utf-8"))
            for t in tracks:
                if t["id"] == chosen.get("id"):
                    return t
        except (json.JSONDecodeError, OSError):
            pass
    if not tracks:
        return None
    if len(tracks) == 1:
        atomic_write_json(cache, {"id": tracks[0]["id"], "reason": "only track available"})
        return tracks[0]

    menu = [{k: t.get(k) for k in ("id", "description", "moods", "energy", "best_for", "avoid_for")}
            for t in tracks]
    story = {"title": script.get("title"), "source_tradition": script.get("source_tradition"),
             "narration": script.get("full_narration", "")[:1500]}
    ids = {t["id"] for t in tracks}

    def validate(o):
        if o.get("id") not in ids:
            raise ValueError(f"picked id {o.get('id')!r} is not in the available tracks")

    try:
        from fuelix_client import chat_json
        choice = chat_json(config.MUSIC_PICKER_MODELS, PICKER_SYSTEM,
                           json.dumps({"story": story, "tracks": menu}, ensure_ascii=False),
                           "music-picker", temperature=0, validator=validate)
    except Exception as e:
        default = load_library().get("default")
        fallback = next((t for t in tracks if t["id"] == default), tracks[0])
        log.warning(f"Music picker failed ({str(e)[:150]}); using '{fallback['id']}'")
        choice = {"id": fallback["id"], "reason": "fallback (picker unavailable)"}
    atomic_write_json(cache, choice)
    track = next(t for t in tracks if t["id"] == choice["id"])
    log.info(f"Music: {track['id']} — {choice.get('reason', '')[:120]}")
    return track


def prepared_path(track: Dict[str, Any]) -> str:
    """Loudness-normalised, offset-trimmed copy (cached), so every track mixes at the same level."""
    src = track_path(track)
    norm_dir = os.path.join(config.MUSIC_DIR, ".normalised")
    os.makedirs(norm_dir, exist_ok=True)
    key = f"{track['id']}_{os.path.getmtime(src):.0f}_{track.get('start_offset_s', 0)}_{track.get('gain_db', 0)}"
    dst = os.path.join(norm_dir, key + ".m4a")
    if os.path.isfile(dst) and os.path.getsize(dst) > 10_000:
        return dst
    offset = float(track.get("start_offset_s", 0) or 0)
    gain = float(track.get("gain_db", 0) or 0)
    af = (f"aresample=48000,aformat=channel_layouts=stereo,"
          f"loudnorm=I={config.MUSIC_TARGET_LUFS}:TP=-2:LRA=11,volume={gain}dB")
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(offset), "-i", src, "-af", af,
                        "-c:a", "aac", "-b:a", "192k", dst], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"could not prepare music {src}: {r.stderr[-300:]}")
    return dst


def check_library() -> int:
    lib = load_library()
    print(f"Library: {config.MUSIC_LIBRARY}\n")
    ok = 0
    for t in lib["tracks"]:
        p = track_path(t)
        if not t.get("file") or not os.path.isfile(p):
            print(f"  --    {t['id']:<22} (no file: assets/music/{t.get('file', '?')})")
            continue
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", p], capture_output=True, text=True)
        try:
            dur = float(r.stdout.strip())
        except ValueError:
            print(f"  BAD   {t['id']:<22} ffprobe can't read {p}")
            continue
        note = "" if dur >= 30 else "  (short: it will loop; make sure it loops cleanly)"
        lic = "" if t.get("license") else "  (fill in 'license'!)"
        print(f"  OK    {t['id']:<22} {dur:6.1f}s{note}{lic}")
        ok += 1
    sfx = [p for p in (config.SFX_TRANSITION, config.SFX_HOOK) if os.path.isfile(p)]
    print(f"\n{ok}/{len(lib['tracks'])} music slots filled. SFX files found: "
          f"{', '.join(os.path.basename(p) for p in sfx) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(check_library())

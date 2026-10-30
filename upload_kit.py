"""Builds a copy-paste UPLOAD_KIT.txt (plus captions) for each rendered video.

    python upload_kit.py          # kits for every rendered story that doesn't have one
    python upload_kit.py 1        # rebuild the kit for story #1
    python upload_kit.py --all    # rebuild all kits

Each kit is written to output/NNN_Name/UPLOAD_KIT.txt and also collected in upload_queue/.
"""
import json
import os
import re
import shutil
import sys

import config
import config_defaults  # noqa: F401  (fills settings missing from older config.py)

CHANNEL_NAME = "इतिहास के सुनहरे पन्ने"
CHANNEL_HANDLE = "@Sunehre-Panne"
QUEUE_DIR = os.path.join(config.BASE_DIR, "upload_queue")

PLAYLISTS = {   # simple keyword -> playlist mapping; edit freely
    "भूले-बिसरे योद्धा": ["ahom", "maratha", "chola", "sikh", "rajput", "vijayanagara", "general", "battle",
                          "lachit", "sambhaji", "warrior"],
    "महाभारत की अनकही कथाएँ": ["mahabharata", "kurukshetra", "krishna", "pandava", "kaurava", "barbarika"],
    "रामायण की अनकही कथाएँ": ["ramayana", "rama", "sita", "hanuman", "ravana"],
    "वैदिक गाथाएँ": ["veda", "vedic", "rishi", "sage", "maharshi", "indra", "dadhichi", "upanishad"],
}


def _playlist(story, script) -> str:
    hay = " ".join([story.get("character_name", ""), story.get("story_title", ""),
                    script.get("source_tradition", ""), story.get("story_summary", "")]).lower()
    best, score = "भूले-बिसरे योद्धा", 0
    for name, keys in PLAYLISTS.items():
        s = sum(k in hay for k in keys)
        if s > score:
            best, score = name, s
    return best


def _tags(story, script):
    tags = [h.lstrip("#") for h in script.get("hashtags", [])]
    tags += [story.get("character_name", ""), "इतिहास", "Indian history", "Hindi history shorts",
             "भारतीय इतिहास", CHANNEL_NAME]
    seen, out, total = set(), [], 0
    for t in tags:
        t = re.sub(r"[<>,]", "", t).strip()
        if t and t.lower() not in seen and total + len(t) + 1 <= 480:   # YouTube limit is 500 chars
            seen.add(t.lower()); out.append(t); total += len(t) + 1
    return out


def _music_credit(ws) -> str:
    try:
        choice = json.load(open(os.path.join(ws, "music_choice.json"), encoding="utf-8"))
        import music
        t = next((t for t in music.load_library()["tracks"] if t["id"] == choice.get("id")), None)
        return f"\n🎵 {t['attribution']}" if t and t.get("attribution") else ""
    except Exception:
        return ""


def build(story) -> str:
    ws = story["output_dir"]
    with open(os.path.join(ws, "script_verified.json"), encoding="utf-8") as fh:
        script = json.load(fh)
    title = (script.get("youtube_title") or script["title"]).strip()
    if len(title) > 100:
        title = title[:97].rstrip() + "…"
    hashtags = " ".join(script.get("hashtags", [])[:8])
    first_line = script["full_narration"].split("।")[0].strip() + "।"
    description = "\n".join([
        first_line,
        "",
        script.get("youtube_description", "").strip(),
        "",
        f"📚 स्रोत / Source: {script.get('source_tradition', '')}",
        "",
        f"🔔 हर दिन इतिहास का एक नया सुनहरा पन्ना — Subscribe करें {CHANNEL_HANDLE}",
        "",
        "Visuals and narration created with AI tools; script researched and fact-checked.",
        _music_credit(ws),
        "",
        hashtags,
    ]).strip()
    question = script.get("engagement_question") or "आप अगली किस गाथा के बारे में जानना चाहते हैं?"
    pinned = (f"{question} 👇\n\n"
              f"📚 स्रोत: {script.get('source_tradition', '')}\n"
              "और बताइए, अगली गाथा किस पर बनाएँ?")
    tags = _tags(story, script)
    playlist = _playlist(story, script)

    kit = f"""══════════ UPLOAD KIT · Story #{story['id']} ══════════
VIDEO FILE   : {os.path.join(ws, 'final_short.mp4')}
CAPTIONS     : {os.path.join(ws, 'subtitles.srt')}  (language: Hindi)

── TITLE ({len(title)}/100) ─────────────────────────────
{title}

── DESCRIPTION ({len(description)}/5000) ───────────────
{description}

── TAGS (paste into "Tags" under Show more) ────────────
{", ".join(tags)}

── PLAYLIST ─────────────────────────────────────────────
{playlist}

── PINNED COMMENT (post right after publishing) ────────
{pinned}

── SETTINGS CHECKLIST ───────────────────────────────────
[ ] Audience: No, it's not made for kids
[ ] Altered or synthetic content: No (stylised animation; see notes in chat)
[ ] Category: Education     [ ] Video language: Hindi
[ ] Subtitles: upload subtitles.srt as Hindi
[ ] Visibility: Schedule (one video per day, same time)
[ ] Watched the whole video once on a phone before scheduling
"""
    path = os.path.join(ws, "UPLOAD_KIT.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(kit)
    os.makedirs(QUEUE_DIR, exist_ok=True)
    stem = os.path.basename(ws)
    shutil.copyfile(path, os.path.join(QUEUE_DIR, f"{stem}_UPLOAD_KIT.txt"))
    shutil.copyfile(os.path.join(ws, "final_short.mp4"), os.path.join(QUEUE_DIR, f"{stem}.mp4"))
    if os.path.isfile(os.path.join(ws, "subtitles.srt")):
        shutil.copyfile(os.path.join(ws, "subtitles.srt"), os.path.join(QUEUE_DIR, f"{stem}.hi.srt"))
    return path


def main() -> int:
    import db_manager as db
    db.init_db()
    args = sys.argv[1:]
    with db.connect() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM stories WHERE status='rendered' ORDER BY id")]
    if args and args[0].isdigit():
        rows = [r for r in rows if r["id"] == int(args[0])]
    elif "--all" not in args:
        rows = [r for r in rows if not os.path.isfile(os.path.join(r["output_dir"] or "", "UPLOAD_KIT.txt"))]
    if not rows:
        print("Nothing to do (no rendered stories without a kit). Use --all to rebuild.")
        return 0
    for r in rows:
        print(f"  #{r['id']:<3} {build(r)}")
    print(f"\nEverything is also collected in: {QUEUE_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

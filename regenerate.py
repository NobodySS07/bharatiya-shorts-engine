"""Redo part of an existing story without touching the rest of the queue.

    python regenerate.py --list                 # show all stories and their status
    python regenerate.py 1                      # re-render video only (default, no API cost)
    python regenerate.py 1 --from narration     # Hindi: re-edit narration only, keep images
    python regenerate.py 1 --from voice         # new voiceover + captions + render
    python regenerate.py 1 --from scenes        # new scene images + render
    python regenerate.py 1 --from anchors       # new character sheets + scenes + render
    python regenerate.py 1 --from script        # everything from scratch (new script + judge)
    python regenerate.py 1 --scene 4 7          # redo only scenes 4 and 7, then re-render
"""
import argparse
import glob
import os
import shutil
import sys

import config
import config_defaults  # noqa: F401  (fills settings missing from older config.py)
from utils import RunLock, log

RENDER = ["final_short.mp4", "final_short.part.mp4", "slideshow.mp4", "concat.txt",
          "upload_metadata.json", "clips/"]
CAPTIONS = ["subtitles.srt", "timings.json"]
VOICE = ["voiceover.mp3", "voiceover_raw.mp3"]
SCENES = ["scene_*.png"]
ANCHORS = ["anchor_*.png"]
SCRIPT = ["script_verified.json", "fact_check_log.json"]

STAGES = {
    "render":   RENDER,
    "captions": CAPTIONS + RENDER,
    "narration": VOICE + CAPTIONS + RENDER,
    "voice":    VOICE + CAPTIONS + RENDER,
    "scenes":   SCENES + RENDER,
    "anchors":  ANCHORS + SCENES + RENDER,
    "script":   SCRIPT + ANCHORS + SCENES + VOICE + CAPTIONS + RENDER,
}


def delete(ws: str, patterns) -> None:
    for pat in patterns:
        for path in glob.glob(os.path.join(ws, pat.rstrip("/"))):
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            log.info(f"  removed {os.path.relpath(path, ws)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("story_id", nargs="?", type=int)
    ap.add_argument("--from", dest="stage", choices=STAGES, default="render")
    ap.add_argument("--scene", type=int, nargs="+", help="regenerate only these scene numbers")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    import db_manager as db
    db.init_db()
    if a.list or a.story_id is None:
        with db.connect() as c:
            for r in c.execute("SELECT id, status, attempts, character_name, story_title FROM stories ORDER BY id"):
                print(f"#{r['id']:<3} {r['status']:<11} tries={r['attempts']}  {r['character_name']} — {r['story_title']}")
        return 0

    story = db.get_story(a.story_id)
    ws = story.get("output_dir")
    if not ws or not os.path.isdir(ws):
        sys.exit(f"Story #{a.story_id} has no output folder yet; just run main.py.")

    db.backup_database(f"regenerate #{a.story_id}")
    log.info(f"Regenerating story #{a.story_id} from '{a.stage}' in {ws}")
    if a.stage == "narration":
        import script_engine
        script_engine.repolish_existing(story)       # fails loudly before deleting anything
    if a.scene:
        delete(ws, [f"scene_{n:02d}.png" for n in a.scene] + RENDER)
    else:
        delete(ws, STAGES[a.stage])

    status = "queued" if a.stage == "script" else "scripted"
    fields = dict(status=status, attempts=0, last_error=None)
    if a.stage == "script":
        fields["script_json"] = None
    db.update_story(a.story_id, **fields)

    from main import preflight, process_story
    preflight()
    with RunLock():
        ok = process_story(db.get_story(a.story_id))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

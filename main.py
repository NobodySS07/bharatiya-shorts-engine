"""Pipeline orchestrator: resume -> seeds -> AI topics, one video at a time.
A failure in one video is logged and recorded; the batch continues with the next."""
import json
import os
import sys
import time
import traceback

import config
import config_defaults  # noqa: F401  (fills settings missing from older config.py)
from utils import PipelineError, RunLock, atomic_write_json, log


def preflight() -> None:
    import video_renderer
    video_renderer.check_ffmpeg()
    if not config.FUELIX_API_KEY:
        raise SystemExit("FUELIX_API_KEY missing (put it in .env).")
    if config.LANGUAGE == "hi" and not config.SARVAM_API_KEY:
        raise SystemExit("LANGUAGE='hi' needs SARVAM_API_KEY in .env (dashboard.sarvam.ai), "
                         "or set LANGUAGE='en' in config.py.")
    free_gb = __import__("shutil").disk_usage(config.BASE_DIR).free / 1e9
    if free_gb < 2:
        raise SystemExit(f"Only {free_gb:.1f} GB free disk; need at least 2 GB.")


def produce_one() -> bool:
    import db_manager as db
    import media_generator
    import script_engine
    import video_renderer

    topic = script_engine.get_next_topic()
    if topic is None:
        log.info("No topic available.")
        return False
    return process_story(topic)


def process_story(topic) -> bool:
    import db_manager as db
    import media_generator
    import script_engine
    import video_renderer

    sid = topic["id"]
    log.info(f"===== Story #{sid}: {topic['character_name']} — {topic['story_title']} =====")
    try:
        script = script_engine.draft_and_fact_check(topic)
        ws = script_engine.workspace_for(db.get_story(sid))
        media = media_generator.generate_all_media(topic, script, ws)
        final = video_renderer.render_video(topic, script, ws, media)
        atomic_write_json(os.path.join(ws, "upload_metadata.json"), {
            "title": script.get("youtube_title") or script["title"],
            "description": f"{script.get('youtube_description', '')}\n\nSource: "
                           f"{script.get('source_tradition', '')}\n\n{' '.join(script.get('hashtags', []))}",
            "file": final,
        })
        db.update_story(sid, status="rendered", last_error=None)
        db.backup_database("rendered")
        try:
            import upload_kit
            log.info(f"Upload kit: {upload_kit.build(db.get_story(sid))}")
        except Exception as e:                      # never fail a finished video over the kit
            log.warning(f"Could not build upload kit: {e}")
        return True
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        status = db.record_failure(sid, detail + "\n" + traceback.format_exc()[-1500:])
        log.error(f"Story #{sid} failed ({status}): {detail}")
        return False


def main() -> int:
    preflight()
    import db_manager as db
    db.init_db()
    db.backup_database("run start")

    done = failures = 0
    max_failures = max(3, config.VIDEOS_TO_GENERATE * 2)   # circuit breaker for the whole run
    with RunLock():
        while done < config.VIDEOS_TO_GENERATE:
            if failures >= max_failures:
                log.error(f"Stopping: {failures} failures this run (circuit breaker).")
                break
            t0 = time.time()
            try:
                ok = produce_one()
            except KeyboardInterrupt:
                log.warning("Interrupted. Progress is checkpointed; rerun to resume.")
                return 130
            except SystemExit:
                raise
            except Exception as e:                 # topic selection itself failed
                log.error(f"Topic selection failed: {e}")
                ok = False
            if ok:
                done += 1
                log.info(f"Video {done}/{config.VIDEOS_TO_GENERATE} done in {time.time() - t0:.0f}s")
            else:
                failures += 1
    log.info(f"Run finished: {done} rendered, {failures} failed.")
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())

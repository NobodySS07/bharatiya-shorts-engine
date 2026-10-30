"""Blind writer bake-off: the same story written by several models, labelled A/B/C/D.

    python bakeoff.py                    # uses the first seed topic
    python bakeoff.py 2                  # uses story #2 from the database
    python bakeoff.py --edited           # also run each draft through the Hindi editor (as in production)
    python bakeoff.py --editors          # ONE writer draft, polished by each editor model (+ unedited), blind

Read bakeoff/<timestamp>/A.txt ... D.txt (ideally aloud, with a Hindi-speaking friend), pick a
winner, THEN open KEY.json to see which model wrote what (plus judge scores and costs).
Set the winner first in config.WRITER_MODELS.
"""
import json
import os
import random
import sys
import time
from datetime import datetime

import config
import config_defaults  # noqa: F401  (fills settings missing from older config.py)
import script_engine as se
from fuelix_client import chat_json
from utils import atomic_write_json, word_count


def editor_bakeoff(topic, out_dir, user, system) -> int:
    from hindi_style import polish
    from names import normalise_script
    print(f"Story: {topic['story_title']}\nWriting one draft with {config.WRITER_MODELS[0]}...")
    draft = chat_json(config.WRITER_MODELS[:1], system, user, "bakeoff writer", temperature=0.8)
    draft["language"] = config.LANGUAGE
    draft["phonetic_narration"] = draft.get("full_narration", "")
    draft = normalise_script(draft)
    candidates = [("unedited", None)] + [(m, [m]) for m in config.EDITOR_BAKEOFF_MODELS]
    random.shuffle(candidates)
    key = {"topic": topic["story_title"], "writer": config.WRITER_MODELS[0], "mode": "editors", "entries": {}}
    for i, (name, models) in enumerate(candidates):
        label = chr(ord("A") + i)
        cand = draft if models is None else normalise_script(polish(json.loads(json.dumps(draft)), models))
        if models is not None and "editor" not in cand:     # polish() returns the input on failure
            name += " (editor failed, unchanged)"
        problems = se.validate_script(cand)
        try:
            verdict = se._judge(cand) if not problems else {"skipped": "structural problems"}
        except Exception as e:
            verdict = {"error": str(e)[:200]}
        with open(os.path.join(out_dir, f"{label}.txt"), "w", encoding="utf-8") as fh:
            fh.write(f"HOOK TEXT : {cand.get('hook_text', '')}\n\n{cand.get('full_narration', '')}\n")
        key["entries"][label] = {"editor": name, "words": word_count(cand.get("full_narration", "")),
                                 "local_problems": problems, "editor_notes": cand.get("editor"),
                                 "judge": {k: verdict.get(k) for k in ("approved", "hook_score", "hook_text_score",
                                                                       "hindi_fluency", "issues")}}
        print(f"  {label}: done")
    atomic_write_json(os.path.join(out_dir, "KEY.json"), key)
    print(f"\nRead {out_dir}/A.txt ... then open KEY.json only after choosing a winner.")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if a.isdigit()]       # ignores stray words (e.g. a pasted "# comment")
    edited = "--edited" in sys.argv
    if args:
        import db_manager as db
        db.init_db()
        topic = db.get_story(int(args[0]))
    else:
        topic = dict(config.SEED_TOPICS[0], id=0)

    out_dir = os.path.join(config.BASE_DIR, "bakeoff", datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    models = list(config.BAKEOFF_MODELS)
    labels = [chr(ord("A") + i) for i in range(len(models))]
    random.shuffle(models)                       # blind: label order says nothing about the model

    user = (f"Character: {topic['character_name']}\nStory: {topic['story_title']}\n"
            f"Summary: {topic.get('story_summary', '')}\nTone: {topic.get('script_tone', '')}\n"
            f"Source tradition: {topic.get('source_tradition') or 'identify the most authoritative source'}\n\n"
            f"Return JSON with this shape:\n{se.SCRIPT_SCHEMA_HINT}")
    system = se.WRITER_SYSTEM
    if config.LANGUAGE == "hi":
        from hindi_style import polish, viewer_feedback
        from names import glossary_prompt
        system += viewer_feedback() + glossary_prompt()
    if "--editors" in sys.argv:
        return editor_bakeoff(topic, out_dir, user, system)

    key = {"topic": topic["story_title"], "edited": edited, "entries": {}}
    print(f"Story: {topic['story_title']}\nWriting with {len(models)} models (blind)...\n")
    for label, model in zip(labels, models):
        t0 = time.time()
        try:
            draft = chat_json([model], system, user, f"bakeoff {label}", temperature=0.8)
            draft["language"] = config.LANGUAGE
            if config.LANGUAGE == "hi":
                draft["phonetic_narration"] = draft.get("full_narration", "")
                if edited:
                    draft = polish(draft)
            problems = se.validate_script(draft)
            try:
                verdict = se._judge(draft) if not problems else {"skipped": "structural problems"}
            except Exception as e:
                verdict = {"error": str(e)[:200]}
            text = draft.get("full_narration", "")
            with open(os.path.join(out_dir, f"{label}.txt"), "w", encoding="utf-8") as fh:
                fh.write(f"HOOK TEXT : {draft.get('hook_text', '')}\n\n{text}\n")
            atomic_write_json(os.path.join(out_dir, f"{label}_full.json"), draft)
            key["entries"][label] = {
                "model": model, "seconds": round(time.time() - t0, 1), "words": word_count(text),
                "local_problems": problems,
                "judge": {k: verdict.get(k) for k in ("approved", "hook_score", "hook_text_score",
                                                      "hindi_fluency", "issues")}
                if isinstance(verdict, dict) else verdict}
            print(f"  {label}: done ({word_count(text)} words, {len(problems)} structural problems)")
        except Exception as e:
            key["entries"][label] = {"model": model, "error": str(e)[:300]}
            print(f"  {label}: FAILED ({str(e)[:120]})")
    atomic_write_json(os.path.join(out_dir, "KEY.json"), key)
    print(f"\nRead {out_dir}/A.txt ... then open KEY.json only after choosing a winner.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

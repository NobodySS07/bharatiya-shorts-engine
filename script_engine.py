"""Topic queue (resume -> seeds -> AI discovery), script writer and fact-check judge."""
import json
import os
import re
from typing import Any, Dict, List, Optional

import config
import config_defaults  # noqa: F401  (fills settings missing from older config.py)
import db_manager as db
from fuelix_client import chat_json, embed
from hindi_style import WRITER_RULES_HI, polish as polish_hindi, viewer_feedback
from names import glossary_prompt, normalise_script
from utils import PipelineError, atomic_write_json, log, norm_token, sanitize_name, word_count


def _embed_text(t: Dict[str, Any]) -> str:
    return f"{t['character_name']} | {t['story_title']} | {t.get('story_summary', '')}"


# ================================================================== topics
def get_next_topic() -> Optional[Dict[str, Any]]:
    if config.RESUME_UNFINISHED:
        row = db.get_unfinished()
        if row:
            log.info(f"Resuming unfinished story #{row['id']}: {row['story_title']} (status={row['status']})")
            return row

    for seed in config.SEED_TOPICS:
        if db.title_exists(seed["story_title"]):
            continue
        vec = embed(_embed_text(seed))
        unique, sim, closest = db.check_similarity(vec)
        if not unique:
            log.info(f"Seed skipped as duplicate ({sim:.3f} ~ '{closest}'): {seed['story_title']}")
            continue
        log.info(f"Queued seed topic: {seed['story_title']}")
        return db.insert_story(seed, vec, "seed")

    return _discover_topic()


TOPIC_SYSTEM = """You are a researcher of Indian history and Itihasa-Purana literature, proposing topics
for 60-second YouTube Shorts. Favour lesser-known but WELL-DOCUMENTED figures and episodes:
Vedic/Upanishadic narratives, Valmiki Ramayana, Mahabharata, Puranas, and historical warriors and
rulers (Ahom, Maratha, Chola, Sikh, Rajput, Vijayanagara, Pandya, Kakatiya, Hoysala, Travancore,
women warriors, tribal resistance leaders, etc.). Only propose episodes you can source.
Return JSON only."""


def _discover_topic() -> Dict[str, Any]:
    rejected: List[str] = []
    for attempt in range(1, config.MAX_TOPIC_RETRIES + 1):
        existing = db.recent_titles()
        prompt = (
            "Propose ONE new topic. Do NOT repeat or closely resemble any of these existing videos:\n"
            + "\n".join(f"- {t}" for t in existing + rejected)
            + '\n\nReturn JSON: {"character_name": str, "story_title": str (punchy, <=90 chars), '
              '"story_summary": str (2 sentences, specific facts), "script_tone": str, '
              '"source_tradition": str (the specific text/parva/kanda or historical sources)}'
        )

        def validate(o):
            for k in ("character_name", "story_title", "story_summary", "script_tone", "source_tradition"):
                if not str(o.get(k, "")).strip():
                    raise ValueError(f"topic missing '{k}'")

        topic = chat_json(config.TOPIC_MODELS, TOPIC_SYSTEM, prompt, "topic-gen",
                          temperature=1.0, validator=validate)
        if db.title_exists(topic["story_title"]):
            rejected.append(f"{topic['character_name']} — {topic['story_title']}")
            continue
        vec = embed(_embed_text(topic))
        unique, sim, closest = db.check_similarity(vec)
        if unique:
            log.info(f"New AI topic (max sim {sim:.3f}): {topic['story_title']}")
            return db.insert_story(topic, vec, "ai")
        log.info(f"AI topic too similar ({sim:.3f} ~ '{closest}'), retry {attempt}")
        rejected.append(f"{topic['character_name']} — {topic['story_title']}")
    raise PipelineError("Could not find a unique topic after retries.")


# ================================================================== writer
LANG_RULES = {
    "en": f"""- Narration language: spoken English. {config.NARRATION_MIN_WORDS}-{config.NARRATION_MAX_WORDS} words, short punchy sentences.
- phonetic_narration: the SAME words in the SAME order, ONE-FOR-ONE (identical word count). Only respell
  Sanskrit/Indian names so an English TTS voice pronounces them well, using hyphens INSIDE a word
  (e.g. "Dadhichi" -> "Dah-dhee-chee"). Never add, drop, split or merge words.""",
    "hi": WRITER_RULES_HI,
}

WRITER_SYSTEM = f"""You write scripts for 60-second vertical YouTube Shorts about Indian Itihasa and history.
Rules:
{LANG_RULES[config.LANGUAGE]}
- THE FIRST SENTENCE DECIDES EVERYTHING (most viewers swipe within 2 seconds):
  * at most 12 words, spoken in under 3 seconds;
  * names a figure or event viewers already know (Krishna, Mahabharata, Mughals, Shivaji) and the STAKES
    or a surprising contradiction, as a question or a bold claim;
  * must be literally true to the source tradition (no "never shot an arrow" if he did);
  * no slow scene-setting ("सन सोलह सौ इकहत्तर।", "यह कहानी है...") before the hook.
- Also write hook_text: the on-screen title for the first seconds, max 7 words, same claim as the hook.
  It must be a QUESTION or a striking contradiction that makes sense with the sound off
  (e.g. "बुख़ार में तपता सेनापति, फिर भी जीत?"). Never a label like "X बनाम Y" / "X vs Y".
- Scene 1's image must be the most dramatic, instantly readable picture of that hook, not an establishing shot.
- End: the story's payoff line answers the opening hook; then ONE short spoken question to the viewer
  that invites an opinion in the comments (e.g. "आपकी नज़र में, क्या कृष्ण का फ़ैसला सही था?").
  Put that question in engagement_question too. No "like/subscribe" requests in the narration.
- Stay faithful to the stated source tradition. If the story comes from Purana or folk tradition, say so
  naturally in the narration (e.g. "According to the Skanda Purana..." / "स्कंद पुराण के अनुसार..."). Never present legend as documented history.
- No invented dates, numbers or places. NO QUOTED SPEECH: never put words in a historical figure's
  mouth, in quotation marks OR as first-person reported speech ("उन्होंने कहा कि ... मैं ..."). Describe what
  they did instead. Only if a named source records the words may you quote, with attribution
  ("बुरंजियों के अनुसार उन्होंने कहा..."), and only one short line.
- Respectful toward all communities; portray adversaries as historical actors, not caricatures.
- PRECISE CLAIMS: say in what sense a side was stronger ("larger in numbers and resources", "heavier
  boats"), never a vague "more powerful". Describe tactical outcomes as limitations ("the big boats could
  not manoeuvre freely") unless the source records the specific detail; no invented micro-events
  ("boats got tangled"). Use period-correct terms: a river fleet is a fleet (बेड़ा), not a navy (नौसेना);
  keep image prompts on the same scale (river boats, not ocean galleons or armadas).
- SAME RESPECT FOR EVERYONE: use the same level of respect for every historical figure, adversaries
  included. In Hindi, use respectful plural verb forms for ALL named figures (राम सिंह पहुँचे / आए,
  लाचित लड़े), never singular forms for one side only (राम सिंह आया / बढ़ रहा था).
- {config.MIN_SCENES} to {config.MAX_SCENES} scenes: about one scene per {config.WORDS_PER_SCENE[config.LANGUAGE]} words of
  narration (each image stays on screen ~5-6 seconds). Split at natural pauses, never mid-phrase.
  The scene narration_segments, joined with spaces, must equal full_narration EXACTLY.
- characters: 1 to {config.MAX_CHARACTERS} visual characters. Each needs:
  * visual_description: detailed, period-accurate (face, age, build, skin tone, hair, attire, weapons, colours).
  * key_features: the 3-5 features that make them instantly recognisable (max 25 words).
  * never: features this character must NOT have, especially other characters' signature traits
    (e.g. Barbarika: "no peacock feather, not blue-skinned, no flute").
  Make characters visually UNMISTAKABLE from each other: different colour palette, headgear and signature item.
- A disguise or a different form is a SEPARATE character with its own id and anchor
  (e.g. c3 = "Krishna disguised as an elderly Brahmin": ordinary human skin, no crown, no peacock feather).
  Use the plain form id only in scenes where that form is what we see.
- IMAGE PROMPTS — the image model is literal and gets confused easily:
  * ONE moment, ONE clear action per scene. 20-55 words. Order: subject -> pose/action -> setting -> light -> camera.
  * At most 2 named characters per scene; prefer 1. Unnamed crowds/armies are fine as background.
  * Only things a camera could see. No hypotheticals, no time spans, no "would / could / watches the whole war".
    Turn abstract ideas into one concrete image (an arrow piercing leaves; an army on the horizon).
  * Violent moments (beheading, death) are shown symbolically and non-graphically (e.g. a glowing
    sculpted head of Barbarika resting on a flower-decked hilltop shrine like a temple idol).
  * Refer to characters only by id (c1, c2). Do not restate the art style. No text in images.
- camera: one of "zoom_in", "zoom_out", "pan_left", "pan_right". Vary them.
Return JSON only."""

SCRIPT_SCHEMA_HINT = """{
  "title": str,
  "youtube_title": str (<= 90 chars, no clickbait lies),
  "hook_text": str (<= 7 words, on-screen in the first 2.5 s),
  "engagement_question": str (the closing question, also used as the pinned comment),
  "youtube_description": str (2-3 sentences + source note),
  "hashtags": [str, ...] (5-8, with #),
  "source_tradition": str,
  "full_narration": str,
  "phonetic_narration": str,
  "characters": [{"id": "c1", "name": str (English), "visual_description": str,
                  "key_features": str, "never": str}],
  "scenes": [{"scene_number": 1, "narration_segment": str, "characters_present": ["c1"],
              "image_prompt": str, "camera": "zoom_in"}]
}"""


def _norm_words(text: str) -> List[str]:
    return [t for t in (norm_token(w) for w in (text or "").split()) if t]


_SPEECH_VERBS = {"कहा", "बोले", "बोला", "बोलीं", "पुकारा", "ललकारा", "चिल्लाए", "चिल्लाया", "said", "shouted"}
_FIRST_PERSON = {"मैं", "मुझे", "मेरा", "मेरी", "मेरे", "हम", "हमें", "हमारा", "हमारी", "i", "me", "my", "we"}


def _speech_problems(text: str) -> List[str]:
    """Flags quoted or first-person reported speech without a named-source attribution."""
    out = []
    if re.search(r'["“”]', text):
        out.append("narration contains quotation marks; don't quote historical figures (describe actions)")
    for sent in re.split(r"(?<=[।.!?])\s+", text):
        toks = [norm_token(t) for t in sent.split()]
        if any(t in {norm_token(v) for v in _SPEECH_VERBS} for t in toks) and \
                any(t in {norm_token(v) for v in _FIRST_PERSON} for t in toks) and \
                "अनुसार" not in sent and "according" not in sent.lower():
            out.append(f"first-person reported speech without a source: '{sent.strip()[:70]}…'; "
                       "describe what they did instead")
    return out


def validate_script(s: Dict[str, Any]) -> List[str]:
    """Cheap local checks run BEFORE the paid judge call. Returns a list of problems."""
    problems = []
    for k in ("title", "full_narration", "phonetic_narration", "characters", "scenes"):
        if not s.get(k):
            problems.append(f"missing field '{k}'")
    if problems:
        return problems

    first = re.split(r"(?<=[।.!?])\s+", s["full_narration"].strip())[0]
    if word_count(first) > 14:
        problems.append(f"first sentence (the hook) is {word_count(first)} words; keep it <= 12 so it "
                        "lands in under 3 seconds")
    if not s.get("hook_text") or word_count(s.get("hook_text", "")) > 8:
        problems.append("hook_text missing or longer than 7 words")
    elif re.search(r"(?<!\S)(बनाम|vs\.?|versus)(?!\S)", s["hook_text"], re.I):
        problems.append("hook_text is an 'X बनाम Y' label; write a question or a striking contradiction")
    if not s.get("engagement_question"):
        problems.append("engagement_question missing")
    elif not s["full_narration"].rstrip().endswith("?"):
        problems.append("narration must end with the spoken engagement question (ending in '?')")

    problems += _speech_problems(s["full_narration"])

    wc = word_count(s["full_narration"])
    if not (config.NARRATION_MIN_WORDS <= wc <= config.NARRATION_MAX_WORDS):
        problems.append(f"full_narration has {wc} words; need "
                        f"{config.NARRATION_MIN_WORDS}-{config.NARRATION_MAX_WORDS}")

    if config.LANGUAGE == "hi":
        letters = [ch for ch in s["full_narration"] if ch.isalpha()]
        deva = sum(1 for ch in letters if "\u0900" <= ch <= "\u097f")
        if letters and deva / len(letters) < 0.9:
            problems.append("full_narration must be written in Devanagari Hindi (found Latin text)")
        for c in s["characters"]:
            if re.search(r"[\u0900-\u097f]", c.get("name", "")):
                problems.append(f"characters[].name must be in English letters (got '{c['name']}'); "
                                "it is inserted into English image prompts")
        if re.search(r"\d", s["full_narration"]):
            problems.append("write all numbers/years as Hindi words, not digits")

    if len(s["full_narration"].split()) != len(s["phonetic_narration"].split()):
        problems.append(f"phonetic_narration has {len(s['phonetic_narration'].split())} space-separated "
                        f"words but full_narration has {len(s['full_narration'].split())}; must match 1:1")

    chars = s["characters"]
    if not (1 <= len(chars) <= config.MAX_CHARACTERS):
        problems.append(f"need 1-{config.MAX_CHARACTERS} characters, got {len(chars)}")
    ids = {c.get("id") for c in chars}
    for c in chars:
        if len(c.get("visual_description", "")) < 80:
            problems.append(f"character {c.get('id')} visual_description too thin")
        if not c.get("key_features"):
            problems.append(f"character {c.get('id')} missing key_features")

    scenes = s["scenes"]
    if not (config.MIN_SCENES <= len(scenes) <= config.MAX_SCENES):
        problems.append(f"need {config.MIN_SCENES}-{config.MAX_SCENES} scenes, got {len(scenes)}")
    else:
        longest = max(word_count(sc.get("narration_segment", "")) for sc in scenes)
        if longest > 2.0 * config.WORDS_PER_SCENE[config.LANGUAGE]:
            problems.append(f"one scene has {longest} words of narration (image would stay too long); "
                            "split the narration more evenly across scenes")
    for i, sc in enumerate(scenes, 1):
        if not sc.get("narration_segment") or not sc.get("image_prompt"):
            problems.append(f"scene {i} missing narration_segment or image_prompt")
        if len(sc.get("characters_present", [])) > 2:
            problems.append(f"scene {i} has {len(sc['characters_present'])} named characters; max 2")
        if word_count(sc.get("image_prompt", "")) > 70:
            problems.append(f"scene {i} image_prompt is {word_count(sc['image_prompt'])} words; keep it 20-55")
        if re.search(r"\b(would|could|might|imagine|as if)\b", sc.get("image_prompt", ""), re.I):
            problems.append(f"scene {i} image_prompt describes something hypothetical; show one concrete moment")
        bad = [x for x in sc.get("characters_present", []) if x not in ids]
        if bad:
            problems.append(f"scene {i} references unknown character ids {bad}")
        if sc.get("camera") not in ("zoom_in", "zoom_out", "pan_left", "pan_right"):
            sc["camera"] = ("zoom_in", "zoom_out")[i % 2]       # auto-fix, not worth a retry

    joined = " ".join(sc.get("narration_segment", "") for sc in scenes)
    if _norm_words(joined) != _norm_words(s["full_narration"]):
        problems.append("scene narration_segments joined together do not exactly equal full_narration")
    return problems


def _write(topic: Dict[str, Any], feedback: Optional[str], previous: Optional[Dict]) -> Dict[str, Any]:
    user = (f"Character: {topic['character_name']}\nStory: {topic['story_title']}\n"
            f"Summary: {topic['story_summary']}\nTone: {topic['script_tone']}\n"
            f"Source tradition: {topic.get('source_tradition') or 'identify the most authoritative source'}\n\n"
            f"Return JSON with this shape:\n{SCRIPT_SCHEMA_HINT}")
    if feedback and previous:
        user += ("\n\nYour previous draft was REJECTED. Fix every issue below and return a complete new JSON.\n"
                 f"ISSUES:\n{feedback}\n\nPREVIOUS DRAFT:\n{json.dumps(previous, ensure_ascii=False)}")
    system = WRITER_SYSTEM + ((viewer_feedback() + glossary_prompt()) if config.LANGUAGE == "hi" else "")
    draft = chat_json(config.WRITER_MODELS, system, user, "writer", temperature=0.8)
    draft["language"] = config.LANGUAGE
    if config.LANGUAGE == "hi" and draft.get("full_narration"):
        draft["phonetic_narration"] = draft["full_narration"]
    return normalise_script(draft)


# ================================================================== judge
JUDGE_SYSTEM = """You are a strict scholar of Sanskrit literature (Vedas, Valmiki Ramayana, Mahabharata
incl. the BORI Critical Edition, Puranas) and of Indian history. Audit a YouTube Shorts script.
Reject if ANY of these occur:
- A claim contradicts the cited source tradition, or a popular TV-serial/folk embellishment is presented
  as if it were in the Valmiki Ramayana or BORI Mahabharata.
- Legend or Purana material is presented as documented history, or attribution to a text is wrong.
- Invented or wrong dates, numbers, names, places, battle outcomes, or fabricated quotations.
- Unverifiable superlatives stated as fact (e.g. "never lost a single battle") without hedging.
- Content demeaning to any religious or ethnic community (historical adversaries may be named factually).
- The image prompts contradict the narration or are anachronistic (attire, weapons).
- HOOKS are scored separately, as a stranger scrolling Shorts with no context:
  * hook_score: ONLY the field "spoken_first_sentence" (what the viewer hears). 10 = names a known figure
    + clear stakes/contradiction; 5 = generic lore or scene-setting ("सन सोलह सौ इकहत्तर").
  * hook_text_score: ONLY the field "on_screen_hook_text" (read with the sound off, max 7 words).
    10 = instantly intriguing on its own; 5 = vague label ("X बनाम Y" with no question or tension).
  Give better options for any hook below 8 in correction_instructions. A hook that bends the facts is an
  automatic reject.
- Any quoted or first-person reported speech of a historical figure that no named source records.
- Vague comparisons ("ज़्यादा ताक़तवर") without saying in what sense, invented tactical micro-details,
  or anachronistic terms/scale (a "navy", ocean-going ships, armadas for a river battle).
- Unequal respect: singular/disrespectful verb forms for one side's figures and respectful forms for the other.
- An image prompt is abstract, crowded or ambiguous (cannot be drawn as one clear moment), a disguised
  form shares an id with the true form, or two characters are not visually distinct (e.g. both blue-skinned,
  both with peacock feathers) or carry attributes that belong to another figure.
- (Hindi scripts) ungrammatical or unnatural Hindi, misspelled names, or English words in the narration.
Be specific and actionable. Do not reject for style preferences alone. Write issues in English.
Return JSON only: {"approved": bool, "confidence": 0-1, "hook_score": 1-10, "hook_text_score": 1-10,
"hindi_fluency": 1-10 or null,
"issues": [{"claim": str, "problem": str, "fix": str}], "correction_instructions": str}
For Hindi scripts, read the narration ALOUD in your head and rate hindi_fluency strictly:
10 = one consistent conversational storytelling voice that flows without a break and keeps you listening;
7 = correct but stop-start, or switches between plain and poetic/dramatic registers;
5 = correct but translated-sounding. Penalise: register switches, poetic fragments mid-story, a full stop
every few words, missing connectors, a flat middle, a moral-lesson ending. Most first drafts deserve 6-7."""


def _judge(script: Dict[str, Any]) -> Dict[str, Any]:
    first = re.split(r"(?<=[।.!?])\s+", (script.get("full_narration") or "").strip())[0]
    payload = {"spoken_first_sentence": first, "on_screen_hook_text": script.get("hook_text")}
    payload.update({k: script.get(k) for k in ("title", "source_tradition", "full_narration",
                                               "engagement_question", "characters")})
    payload["image_prompts"] = [sc["image_prompt"] for sc in script["scenes"]]

    hindi = script.get("language") == "hi"

    def validate(o):
        if not isinstance(o.get("approved"), bool):
            raise ValueError("judge must return boolean 'approved'")
        if hindi and not isinstance(o.get("hindi_fluency"), (int, float)):
            raise ValueError("judge must return numeric 'hindi_fluency' for Hindi scripts")
    models = config.HINDI_JUDGE_MODELS if hindi else config.JUDGE_MODELS
    system = JUDGE_SYSTEM + (viewer_feedback() if hindi else "")
    verdict = chat_json(models, system, json.dumps(payload, ensure_ascii=False), "judge",
                        temperature=0, validator=validate)
    for field, label, minimum in (("hook_score", "spoken first sentence", config.MIN_HOOK_SCORE),
                                  ("hook_text_score", "on-screen hook text", config.MIN_HOOK_TEXT_SCORE)):
        score = verdict.get(field)
        if verdict["approved"] and isinstance(score, (int, float)) and score < minimum:
            verdict["approved"] = False
            verdict.setdefault("issues", []).append({
                "claim": label, "problem": f"{label} scored {score}/10 (< {minimum})",
                "fix": verdict.get("correction_instructions") or "name a known figure + clear stakes"})
            log.info(f"{label} {score}/10 < {minimum}; rewriting")
    if hindi and verdict["approved"] and verdict["hindi_fluency"] < config.MIN_HINDI_FLUENCY:
        verdict["approved"] = False
        verdict.setdefault("issues", []).append({
            "claim": "overall Hindi narration", "problem": f"fluency {verdict['hindi_fluency']}/10 is below "
            f"{config.MIN_HINDI_FLUENCY}", "fix": "rewrite for natural, vivid documentary Hindi"})
        log.info(f"Facts OK but Hindi fluency {verdict['hindi_fluency']}/10 < {config.MIN_HINDI_FLUENCY}")
    return verdict


def workspace_for(topic: Dict[str, Any]) -> str:
    path = topic.get("output_dir") or os.path.join(
        config.OUTPUT_DIR, f"{topic['id']:03d}_{sanitize_name(topic['character_name'])}")
    os.makedirs(path, exist_ok=True)
    return path


def draft_and_fact_check(topic: Dict[str, Any]) -> Dict[str, Any]:
    ws = workspace_for(topic)
    db.update_story(topic["id"], output_dir=ws)
    verified_path = os.path.join(ws, "script_verified.json")
    if os.path.isfile(verified_path):
        try:
            with open(verified_path, encoding="utf-8") as fh:
                script = json.load(fh)
            if script.get("language", "en") != config.LANGUAGE:
                log.warning(f"Existing script is in '{script.get('language', 'en')}' but LANGUAGE="
                            f"'{config.LANGUAGE}'. Keeping it; run `regenerate.py {topic['id']} "
                            "--from script` to rewrite it in the new language.")
                return script
            legacy_ok = lambda p: p.startswith("characters[].name") or "words; need" in p
            blocking = [p for p in validate_script(script) if not legacy_ok(p)]
            if script.get("fact_check", {}).get("approved") and not blocking:
                log.info("Loaded existing verified script.")
                return script
            if blocking:
                log.warning(f"Existing script has structural problems {blocking}; regenerating.")
        except (json.JSONDecodeError, OSError):
            log.warning("script_verified.json is corrupt; regenerating.")

    # A NEW script is about to be written: media made for any previous script no longer matches it.
    import glob as _glob
    stale = [f for pat in ("anchor_*.png", "scene_*.png", "voiceover*.mp3", "subtitles.*", "timings.json",
                           "music_choice.json", "final_short*.mp4", "slideshow.mp4", "UPLOAD_KIT.txt")
             for f in _glob.glob(os.path.join(ws, pat))]
    if stale:
        log.warning(f"Writing a new script; removing {len(stale)} media files made for the old one.")
        for f in stale:
            os.remove(f)
        import shutil as _sh
        _sh.rmtree(os.path.join(ws, "clips"), ignore_errors=True)
    feedback, draft = None, None
    audit_log = []
    for round_no in range(1, config.MAX_JUDGE_RETRIES + 2):   # 1 draft + N revisions
        draft = _write(topic, feedback, draft)
        problems = validate_script(draft)
        if problems:
            feedback = "Structural problems:\n- " + "\n- ".join(problems)
            log.warning(f"Draft {round_no} failed local validation: {problems}")
            audit_log.append({"round": round_no, "local_problems": problems})
            continue
        if config.LANGUAGE == "hi" and config.HINDI_EDITOR_ENABLED:
            polished = normalise_script(polish_hindi(draft))
            post = validate_script(polished)
            if post:
                log.warning(f"Editor output failed validation ({post}); judging writer's draft instead.")
            else:
                draft = polished
        verdict = _judge(draft)
        audit_log.append({"round": round_no, "verdict": verdict})
        if verdict["approved"]:
            log.info(f"Judge approved draft {round_no} (confidence {verdict.get('confidence')}).")
            draft["fact_check"] = verdict
            atomic_write_json(verified_path, draft)
            atomic_write_json(os.path.join(ws, "fact_check_log.json"), audit_log)
            db.update_story(topic["id"], script_json=json.dumps(draft, ensure_ascii=False),
                            status="scripted", last_error=None)
            return draft
        issues = "\n".join(f"- {i.get('claim')}: {i.get('problem')} -> {i.get('fix')}"
                           for i in verdict.get("issues", []))
        feedback = f"{issues}\n{verdict.get('correction_instructions', '')}".strip()
        log.warning(f"Judge rejected draft {round_no}:\n{feedback}")

    atomic_write_json(os.path.join(ws, "fact_check_log.json"), audit_log)
    # Fail closed: an unapproved script never reaches the paid media stages.
    raise PipelineError("Script not approved by fact-checker after all retries "
                        "(see fact_check_log.json).")


def repolish_existing(topic: Dict[str, Any]) -> Dict[str, Any]:
    """Re-edit the Hindi narration of an already verified script WITHOUT touching scenes or
    image prompts (images stay valid), then re-judge. Used by `regenerate.py N --from narration`."""
    ws = workspace_for(topic)
    path = os.path.join(ws, "script_verified.json")
    with open(path, encoding="utf-8") as fh:
        script = json.load(fh)
    if script.get("language") != "hi":
        raise PipelineError("--from narration is for Hindi scripts; use --from script instead.")
    best = None
    for attempt in range(1, config.MAX_JUDGE_RETRIES + 1):
        cand = normalise_script(polish_hindi(script))
        # older scripts may have Devanagari character names; images already exist, so allow it here
        problems = [p for p in validate_script(cand) if not p.startswith("characters[].name")]
        if problems:
            log.warning(f"Re-polish {attempt} failed validation: {problems}")
            continue
        verdict = _judge(cand)
        if verdict["approved"]:
            cand["fact_check"] = verdict
            atomic_write_json(os.path.join(ws, "script_verified.json"), cand)
            db.update_story(topic["id"], script_json=json.dumps(cand, ensure_ascii=False))
            log.info(f"Narration re-polished and approved (fluency {verdict.get('hindi_fluency')}).")
            return cand
        best = verdict
        log.warning(f"Re-polish {attempt} rejected: {[i.get('problem') for i in verdict.get('issues', [])]}")
    raise PipelineError(f"Could not produce an approved re-polished narration: {best}")

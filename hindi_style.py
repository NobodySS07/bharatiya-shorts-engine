"""Hindi narration quality: native-language writing rules, a style example, a cheap local
style lint, and a separate editor (संपादक) pass on a second model."""
import os
import re
from collections import Counter
from typing import Any, Dict, List

import config
import config_defaults  # noqa: F401  (fills settings missing from older config.py)
from fuelix_client import chat_json
from utils import log, norm_token

# Written in Hindi on purpose: a model instructed in English tends to think in English
# and translate, which produces flat "translationese".
FEEDBACK_PATH = os.path.join(config.BASE_DIR, "hindi_feedback.txt")


def viewer_feedback() -> str:
    """Real viewer/editor notes, kept in hindi_feedback.txt and fed into every Hindi prompt."""
    try:
        with open(FEEDBACK_PATH, encoding="utf-8") as fh:
            notes = [l.strip() for l in fh if l.strip() and not l.lstrip().startswith("#")]
    except FileNotFoundError:
        return ""
    return ("\n\nदर्शकों की असली प्रतिक्रिया (इन्हें सबसे ज़्यादा महत्व दीजिए):\n- " + "\n- ".join(notes)) if notes else ""


WRITER_RULES_HI = f"""## कथावाचन की भाषा: हिंदी (देवनागरी)
कल्पना कीजिए कि आप किसी दोस्त को एक मिनट में यह कहानी सुना रहे हैं, और वह बीच में फ़ोन नहीं छोड़ पा रहा।
पहले पूरी कहानी एक बहाव में हिंदी में सोचिए और लिखिए; दृश्यों (scenes) में बाँटना बाद का काम है।

सबसे ज़रूरी: एक ही लहजा, शुरू से अंत तक
- पूरी कहानी एक ही रजिस्टर में हो: साफ़, बोलचाल की, कहानी सुनाने वाली हिंदी (जैसे कोई अच्छा हिंदी पॉडकास्ट)।
- बीच में अचानक कवितामय या नाटकीय पंक्तियाँ नहीं। अलंकार, तुकबंदी, "शब्द-चित्र" वाले टुकड़े नहीं।
  कविता जैसी एक पंक्ति अगर हो, तो सिर्फ़ आख़िर में।
- अधूरे टुकड़ों वाले वाक्य ("तीन बाण, और तीनों अचूक।") से बचिए। पूरे, स्वाभाविक वाक्य बोलिए।
- तत्सम/भारी शब्द कम से कम; जहाँ आम शब्द है वहाँ आम शब्द ("मौत", "लड़ाई", "हिम्मत")।

बहाव (flow) और रिदम
- वाक्य आपस में जुड़े हों। जोड़ने वाले शब्द इस्तेमाल कीजिए: "लेकिन", "तभी", "और फिर", "इसी बीच",
  "इसलिए", "पर असली सवाल ये था कि…"। हर वाक्य पिछले से निकले और अगले की ओर खींचे।
- पूर्ण विराम (।) कम, अल्पविराम ज़्यादा: औसतन एक वाक्य बारह से बीस शब्दों का। बहुत छोटे वाक्य सिर्फ़ ज़ोर देने के लिए, कभी-कभार।
- हर वाक्य "था/थी/थे" पर ख़त्म न हो, पर इसके लिए भारी शब्द भी न ठूँसें।

कहानी की बनावट (जिससे दर्शक रुका रहे)
- शुरुआत: पहले ही वाक्य में किसी जाने-पहचाने पात्र/घटना के साथ दाँव या विरोधाभास ("क्या आप जानते हैं…?" जैसी घिसी शुरुआत नहीं)।
- बीच: एक के बाद एक मोड़, हर मोड़ पर थोड़ा तनाव बढ़े।
- अंत: शुरुआत वाले सवाल का जवाब, एक साफ़ और याद रह जाने वाली पंक्ति में (उपदेश नहीं),
  और फिर दर्शक से एक छोटा सवाल जिस पर वह कमेंट में अपनी राय दे।

तकनीकी नियम:
- {config.NARRATION_MIN_WORDS}-{config.NARRATION_MAX_WORDS} शब्द (space से गिने जाते हैं)।
- सभी संख्याएँ और वर्ष शब्दों में ("सन सोलह सौ इकहत्तर", "1671" नहीं)।
- नाम सही देवनागरी में, नुक़्ता सही (मुग़ल, जहाज़)। कथावाचन में कोई अंग्रेज़ी शब्द नहीं।
- दृश्यों में बाँटते समय narration_segment किसी अल्पविराम या स्वाभाविक ठहराव पर भी टूट सकता है;
  हर segment का "।" पर ख़त्म होना ज़रूरी नहीं।
- phonetic_narration: full_narration की हूबहू प्रति।
- title, youtube_title, youtube_description हिंदी में (youtube_title के अंत में कोष्ठक में अंग्रेज़ी नाम चल सकता है)।
- image_prompt और visual_description अंग्रेज़ी में ही रहें (image model के लिए)। characters[].name अंग्रेज़ी में लिखें।

लहजे का उदाहरण (सिर्फ़ शैली के लिए; सामग्री मत दोहराइए):
"सन पंद्रह सौ चौंसठ की बात है। गोंडवाना की रानी दुर्गावती के सामने मुग़ल सेनापति आसफ़ ख़ान अपनी विशाल सेना
लेकर खड़ा था, और रानी के पास सैनिक कहीं कम थे। फिर भी उन्होंने पीछे हटने के बजाय ख़ुद हाथी पर सवार होकर
मोर्चा सँभाला। लड़ाई के बीच उन्हें एक तीर लगा, फिर दूसरा, लेकिन वो डटी रहीं। और जब उन्हें लगा कि अब
बंदी बनना तय है, तो उन्होंने दुश्मन के हाथ आने के बजाय मौत को चुना।"
"""

SCENE_MARK = "|"

EDITOR_SYSTEM = f"""आप एक वरिष्ठ हिंदी संपादक हैं। आपको एक YouTube Short का पूरा कथावाचन मिलेगा, जिसमें दृश्यों
(images) की सीमाएँ "{SCENE_MARK}" से दिखाई गई हैं।

काम: पूरी कहानी को एक इकाई की तरह पढ़िए और ऐसे दोबारा लिखिए कि ज़ोर से पढ़ने पर वह शुरू से अंत तक
एक ही लहजे में, बिना रुकावट बहे, जैसे कोई अच्छा कथावाचक दोस्त को कहानी सुना रहा हो।
- एक ही रजिस्टर: साफ़ बोलचाल की हिंदी। बीच-बीच में कवितामय/नाटकीय टुकड़े हटाइए। काव्यात्मक पंक्ति सिर्फ़ अंत में, वह भी एक।
- सब ऐतिहासिक पात्रों के लिए एक जैसा आदर (राम सिंह "आए", "आया" नहीं)। किसी के मुँह में शब्द न डालें: "उन्होंने कहा कि… मैं…" जैसे उद्धरण हटाकर उनका काम बताइए।
- वाक्यों को जोड़िए ("लेकिन", "तभी", "और फिर", "इसलिए")। बेवजह के पूर्ण विराम हटाइए; छोटे-छोटे टुकड़ों को पूरे वाक्य बनाइए।
- शुरुआत जिज्ञासा जगाए, बीच में तनाव बढ़े, अंत शुरुआत के सवाल का जवाब दे।

कठोर नियम:
- कोई नया तथ्य, नाम, संख्या, स्थान या दावा न जोड़ें; कोई तथ्य न हटाएँ। स्रोत का उल्लेख बना रहे।
- पहला वाक्य (हुक) बारह शब्दों से छोटा और उतना ही तीखा रहे; उसे लंबा या धीमा न करें।
- आख़िरी वाक्य दर्शक से पूछा गया सवाल है ("?" पर ख़त्म); उसे बनाए रखें।
- ठीक उतने ही "{SCENE_MARK}" चिह्न लौटाइए जितने मिले, और हर हिस्से का अर्थ उसी दृश्य से मेल खाए।
  चिह्न वाक्य के बीच (अल्पविराम या स्वाभाविक ठहराव पर) भी आ सकता है।
- कुल {config.NARRATION_MIN_WORDS}-{config.NARRATION_MAX_WORDS} शब्द। संख्याएँ शब्दों में। कोई अंग्रेज़ी शब्द नहीं।

सिर्फ़ JSON लौटाइए:
{{"narration": "पूरा कथावाचन, दृश्य-सीमाओं पर {SCENE_MARK} के साथ", "fluency_before": 1-10, "fluency_after": 1-10,
"changes": "short English summary"}}"""


def _glossary() -> str:
    from names import glossary_prompt
    return glossary_prompt()


def style_lint(segments: List[str], names: List[str]) -> List[str]:
    """Cheap, deterministic style warnings (not hard failures)."""
    text = " ".join(segments)
    sentences = [s.strip() for s in re.split(r"[।!?]", text) if s.strip()]
    warnings = []
    if sentences:
        tha = sum(1 for s in sentences if re.search(r"(था|थी|थे|थीं)$", s))
        if tha / len(sentences) > 0.30:
            warnings.append(f"{tha} of {len(sentences)} sentences end in था/थी/थे; vary the verb endings")
    stop = {norm_token(w) for w in "का की के को में से ने पर और है हैं था थी थे यह वह एक भी तो ही उसे उसने उसका उसकी".split()}
    name_toks = {norm_token(t) for n in names for t in n.split()}
    counts = Counter(t for t in (norm_token(w) for w in text.split())
                     if t and t not in stop and t not in name_toks and len(t) > 2)
    rep = [w for w, c in counts.items() if c >= 3]
    if rep:
        warnings.append(f"words repeated 3+ times: {', '.join(rep[:6])} "
                        "(fine for proper nouns; otherwise use a synonym or restructure)")
    words = len(text.split())
    if sentences and words / len(sentences) < 9:
        warnings.append(f"too choppy: {len(sentences)} sentences in {words} words "
                        f"(avg {words / len(sentences):.1f}); join sentences with connectors, aim for 12-20 words")
    long = [s for s in sentences if len(s.split()) > 28]
    if long:
        warnings.append(f"{len(long)} sentence(s) over 28 words (hard to follow by ear); split at a connector")
    frags = [s for s in sentences if len(s.split()) <= 4]
    if len(frags) > 2:
        warnings.append(f"{len(frags)} fragment sentences ({'; '.join(frags[:3])}); make them full sentences")
    if re.search(r"(सबक|सीख)\s*है?।?\s*$", text):
        warnings.append("ending sounds like a moral lesson; end on an image instead")
    return warnings


def polish(script: Dict[str, Any], models: List[str] = None) -> Dict[str, Any]:
    """Run the editor on the narration segments. Returns a NEW script dict, or the original
    if the editor's output breaks structure (the caller re-validates)."""
    segments = [sc["narration_segment"] for sc in script["scenes"]]
    names = [c.get("name", "") for c in script.get("characters", [])]
    lint = style_lint(segments, names)
    marked = f" {SCENE_MARK} ".join(segments)
    user = (f"कथावाचन ({len(segments)} दृश्य, {len(segments) - 1} \"{SCENE_MARK}\" चिह्न):\n{marked}"
            + ("\n\nस्वचालित शैली-जाँच की टिप्पणियाँ:\n- " + "\n- ".join(lint) if lint else "")
            + viewer_feedback() + _glossary())

    def validate(o):
        parts = [p.strip() for p in str(o.get("narration", "")).split(SCENE_MARK)]
        if len(parts) != len(segments) or not all(parts):
            raise ValueError(f"editor must keep exactly {len(segments) - 1} '{SCENE_MARK}' marks "
                             f"(got {len(parts) - 1})")
        o["narration_segments"] = parts

    try:
        out = chat_json(models or config.HINDI_EDITOR_MODELS, EDITOR_SYSTEM, user, "hindi-editor",
                        temperature=0.4, validator=validate)
    except Exception as e:
        log.warning(f"Hindi editor unavailable, keeping writer's draft: {str(e)[:200]}")
        return script
    new = {**script, "scenes": [dict(sc) for sc in script["scenes"]]}
    for sc, seg in zip(new["scenes"], out["narration_segments"]):
        sc["narration_segment"] = " ".join(seg.split())
    new["full_narration"] = " ".join(sc["narration_segment"] for sc in new["scenes"])
    new["phonetic_narration"] = new["full_narration"]
    new["editor"] = {k: out.get(k) for k in ("fluency_before", "fluency_after", "changes")}
    new["editor"]["lint_before"] = lint
    new["editor"]["lint_after"] = style_lint([sc["narration_segment"] for sc in new["scenes"]], names)
    log.info(f"Hindi editor: fluency {out.get('fluency_before')} -> {out.get('fluency_after')}; "
             f"lint {len(lint)} -> {len(new['editor']['lint_after'])} warnings")
    return new

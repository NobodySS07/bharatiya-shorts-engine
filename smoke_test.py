"""Probe every endpoint/model the pipeline uses, cheaply, before a real run.
Run:  python smoke_test.py          (add --images to also test image generation, ~3 image calls)"""
import os
import sys
import tempfile

import config
import config_defaults  # noqa: F401  (fills settings missing from older config.py)
from utils import PipelineError, log


def check(name, fn):
    try:
        info = fn()
        print(f"  PASS  {name}  {info or ''}")
        return True
    except Exception as e:
        print(f"  FAIL  {name}  -> {type(e).__name__}: {str(e)[:300]}")
        return False


def main():
    import fuelix_client as fx
    test_images = "--images" in sys.argv
    results = []
    print(f"Base URL: {config.FUELIX_BASE_URL}\n")

    for m in config.WRITER_MODELS + config.JUDGE_MODELS:
        results.append(check(f"chat json  {m}", lambda m=m: fx.chat_json(
            [m], "Reply with JSON only.", 'Return {"ok": true}', f"smoke {m}", temperature=0)))

    results.append(check(f"embeddings {config.EMBEDDING_MODEL}",
                         lambda: f"dim={len(fx.embed('Lachit Borphukan at Saraighat'))}"))

    tmp = os.path.join(tempfile.gettempdir(), "smoke_tts.mp3")

    def tts_test():
        text = ("This is a short test of the narration voice for the Bharatiya Shorts engine. " * 6)
        data = fx.client.audio.speech.create(model=config.TTS_MODEL, voice=config.TTS_VOICE, input=text)
        b = data.read()
        open(tmp, "wb").write(b)
        return f"{len(b) / 1024:.0f} KB"
    ok_tts = check(f"tts        {config.TTS_MODEL}/{config.TTS_VOICE}", tts_test)
    results.append(ok_tts)
    if ok_tts:
        results.append(check("transcribe word timestamps",
                             lambda: f"{len(fx.transcribe_words(tmp))} words"))

    if test_images:
        anchor = {}
        def anchor_test():
            b = fx.generate_image_text_only(config.ANCHOR_IMAGE_MODELS[:1],
                                            config.TEMPLE_FRESCO_GLOW_STYLE + " A Maratha warrior, full body.",
                                            "smoke anchor")
            anchor["b"] = b
            open("smoke_anchor.png", "wb").write(b)
            return f"{len(b) / 1024:.0f} KB -> smoke_anchor.png"
        results.append(check(f"image      {config.ANCHOR_IMAGE_MODELS[0]}", anchor_test))
        if anchor:
            for m in config.SCENE_IMAGE_MODELS_WITH_REF:
                def ref_test(m=m):
                    b = fx.generate_image_with_refs(
                        [m], config.TEMPLE_FRESCO_GLOW_STYLE + " The same warrior from the reference, "
                        "now standing on a fort rampart at dusk.", [anchor["b"]], f"smoke {m}")
                    open(f"smoke_{m}.png", "wb").write(b)
                    return f"{len(b) / 1024:.0f} KB -> smoke_{m}.png (check the face matches!)"
                results.append(check(f"image+ref  {m}", ref_test))
            results.append(check(f"vision QA  {config.VISION_QA_MODELS[0]}", lambda: fx.vision_check(
                [anchor["b"]], 'Return JSON {"has_text_or_watermark": bool}', "smoke qa")))

    print(f"\n{sum(results)}/{len(results)} checks passed.")
    if not test_images:
        print("Image models not tested. Run `python smoke_test.py --images` (≈3 image calls).")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())

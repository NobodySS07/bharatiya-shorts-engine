"""Audition Sarvam Hindi voices on a sample of your content.
    python sarvam_voice_test.py              # default shortlist
    python sarvam_voice_test.py ratan_hi_documentary anand_hi_news   # any v4-flash persona ids
Files are written to voice_samples/. Put your favourite first in config.SARVAM_VOICES."""
import os
import sys

import config
import config_defaults  # noqa: F401  (fills settings missing from older config.py)
import voice

SAMPLE = ("सन सोलह सौ इकहत्तर। ब्रह्मपुत्र की लहरों पर मुग़लों का विशाल बेड़ा बढ़ रहा था। "
          "असम का सेनापति लाचित बरफुकन तेज़ बुखार में तप रहा था। फिर भी उसने कहा, "
          "मुझे नाव पर ले चलो। उस रात सरायघाट में इतिहास बदल गया।")

SHORTLIST = [
    ("bulbul:v4-flash", "anand_hi_documentary"),
    ("bulbul:v4-flash", "shubh_hi_stories_dramatic"),
    ("bulbul:v4-flash", "mahesh_hi_documentary"),
    ("bulbul:v4-flash", "ratan_hi_documentary"),
    ("bulbul:v4-flash", "anand_hi_documentary"),
    ("bulbul:v4-flash", "girish_hi_devotional"),
    ("bulbul:v4-flash", "mukul_hi_suspense"),
    ("bulbul:v3", "shubh"),
    ("bulbul:v3", "kabir"),
]


def main():
    if not config.SARVAM_API_KEY:
        sys.exit("Add SARVAM_API_KEY to .env first.")
    voices = [("bulbul:v4-flash", v) for v in sys.argv[1:]] or SHORTLIST
    os.makedirs("voice_samples", exist_ok=True)
    for model, speaker in voices:
        try:
            wav = voice._sarvam_request(SAMPLE, model, speaker, config.SARVAM_PACE)
            path = os.path.join("voice_samples", f"{speaker}.mp3")
            open(path, "wb").write(voice._wav_to_mp3(wav))
            print(f"  OK    {model:16} {speaker:28} -> {path}")
        except Exception as e:
            print(f"  FAIL  {model:16} {speaker:28} {type(e).__name__}: {str(e)[:150]}")
    print("\nListen with:  open voice_samples/")


if __name__ == "__main__":
    main()

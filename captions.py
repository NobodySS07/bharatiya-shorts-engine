"""Map Whisper word timestamps (heard from the PHONETIC audio) onto the ORIGINAL narration
words, so subtitles show proper spellings and slides change exactly when their line starts."""
import difflib
import re
from typing import Any, Dict, List, Tuple

from utils import norm_token as _norm

_PUNCT_END = r"[.!?,;:—।॥]$"


def align(original_text: str, phonetic_text: str, heard: List[Dict[str, Any]],
          audio_duration: float) -> List[Dict[str, Any]]:
    """Returns one {'word', 'start', 'end'} per ORIGINAL word (space-separated tokens).

    Word-level alignment (not letter-level, which can match a word's letters to a repeat of the
    same phrase seconds later and drag every following caption off sync):
      1. exact matches of whole normalised words are anchors;
      2. a misheard stretch ('replace' block) gets the time span of the heard words it maps to,
         shared out by word length, so it cannot drift outside its neighbours;
      3. words Whisper skipped are interpolated between anchors.
    """
    orig = original_text.split()
    phon = phonetic_text.split()
    if len(orig) != len(phon):
        phon = orig
    n = len(orig)
    starts: List[float] = [None] * n                  # type: ignore
    ends: List[float] = [None] * n                    # type: ignore
    anchored = [False] * n
    p_norm = [_norm(p) for p in phon]
    h_norm = [_norm(h["word"]) for h in heard]
    # Drop empty tokens (punctuation-only) from the heard side, keeping their times.
    h_idx = [i for i, t in enumerate(h_norm) if t]
    h_tok = [h_norm[i] for i in h_idx]

    sm = difflib.SequenceMatcher(None, p_norm, h_tok, autojunk=False)
    # Merge every non-matching stretch with any isolated 1-word "match" inside it: a lone match
    # in a misheard stretch is usually the same word recurring elsewhere (e.g. a repeated phrase).
    regions, pending = [], None                     # (kind, i1, i2, j1, j2)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        trusted = op == "equal" and (i2 - i1 >= 2 or (pending is None and i1 == 0))
        if trusted:
            if pending:
                regions.append(("fuzzy",) + pending)
                pending = None
            regions.append(("equal", i1, i2, j1, j2))
        else:
            pending = (i1, i2, j1, j2) if pending is None else \
                      (pending[0], i2, pending[2], j2)
    if pending:
        regions.append(("fuzzy",) + pending)

    for kind, i1, i2, j1, j2 in regions:
        if kind == "equal":
            for k in range(i2 - i1):
                h = heard[h_idx[j1 + k]]
                starts[i1 + k], ends[i1 + k] = h["start"], h["end"]
                anchored[i1 + k] = True
        elif i2 > i1 and j2 > j1:                   # misheard stretch: share its real time span
            t0, t1 = heard[h_idx[j1]]["start"], heard[h_idx[j2 - 1]]["end"]
            weights = [max(2, len(p_norm[i])) for i in range(i1, i2)]
            total, t = sum(weights), t0
            for i, w in zip(range(i1, i2), weights):
                d = (t1 - t0) * w / total
                starts[i], ends[i] = t, t + d
                t += d
        # only original words, nothing heard: Whisper skipped them -> interpolated below

    _interpolate(orig, starts, ends, audio_duration,
                 heard[0]["start"] if heard else 0.0, heard[-1]["end"] if heard else audio_duration)
    return [{"word": orig[i], "start": starts[i], "end": ends[i], "anchored": anchored[i]}
            for i in range(n)]


def hybrid(aligned: List[Dict[str, Any]], paused: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Whisper's exact word matches where it heard clearly; pause-anchored timing everywhere else."""
    if len(aligned) != len(paused):
        return aligned
    # A Whisper match that disagrees with the pause-based estimate by > 1 s is almost always a
    # repeated phrase matched in the wrong place: trust the pauses there instead.
    out = [dict(a) if a.get("anchored") and abs(a["start"] - p["start"]) <= 1.0
           else dict(p, anchored=False) for a, p in zip(aligned, paused)]
    for k in range(1, len(out)):                      # keep order and non-zero durations
        if out[k]["start"] < out[k - 1]["end"]:
            out[k]["start"] = out[k - 1]["end"]
        if out[k]["end"] <= out[k]["start"]:
            out[k]["end"] = out[k]["start"] + 0.08
    return out


def alignment_quality(words: List[Dict[str, Any]]) -> Dict[str, float]:
    """Sanity numbers for the log and the fallback decision."""
    durs = [w["end"] - w["start"] for w in words]
    gaps = [words[i + 1]["start"] - words[i]["end"] for i in range(len(words) - 1)]
    return {"max_word_s": max(durs) if durs else 0.0,
            "max_gap_s": max(gaps) if gaps else 0.0,
            "long_words": sum(1 for d in durs if d > 1.6)}


def proportional(original_text: str, speech_start: float, speech_end: float,
                 final_pause: bool = True) -> List[Dict[str, Any]]:
    """Spread words by character length; punctuation gets a little extra for its pause.
    final_pause=False when the span already ends at a measured pause (no bonus on the last word)."""
    words = original_text.split()
    weights = [max(2, len(_norm(w))) + (3 if re.search(_PUNCT_END, w) else 0) for w in words]
    if words and not final_pause and re.search(_PUNCT_END, words[-1]):
        weights[-1] -= 3
    total = sum(weights)
    t, out = speech_start, []
    span = max(1.0, speech_end - speech_start)
    for w, wt in zip(words, weights):
        d = span * wt / total
        out.append({"word": w, "start": t, "end": t + d})
        t += d
    return out


def _dp_pins(words, cands, P, est_end, scale, skip_pause, skip_strong, skip_weak):
    """Optimal monotonic matching of punctuation candidates to pauses (both in time order)."""
    strong = re.compile(r"[।॥.!?]$")
    C, Q, INF = len(cands), len(P), float("inf")
    D = [[INF] * (Q + 1) for _ in range(C + 1)]
    B = [[None] * (Q + 1) for _ in range(C + 1)]
    D[0][0] = 0.0
    for ci in range(C + 1):
        for pj in range(Q + 1):
            cur = D[ci][pj]
            if cur == INF:
                continue
            if ci < C and pj < Q:
                i, (a, b) = cands[ci], P[pj]
                d = (est_end[i] - a) / scale
                kind = 0.0 if (b - a >= 0.3) == bool(strong.search(words[i])) else 0.4
                c = cur + d * d + kind
                if c < D[ci + 1][pj + 1]:
                    D[ci + 1][pj + 1], B[ci + 1][pj + 1] = c, ("m", ci, pj)
            if ci < C:
                c = cur + (skip_strong if strong.search(words[cands[ci]]) else skip_weak)
                if c < D[ci + 1][pj]:
                    D[ci + 1][pj], B[ci + 1][pj] = c, ("sc", ci, pj)
            if pj < Q:
                c = cur + skip_pause
                if c < D[ci][pj + 1]:
                    D[ci][pj + 1], B[ci][pj + 1] = c, ("sp", ci, pj)
    pins, ci, pj = [], C, Q
    while ci or pj:
        op, a, b = B[ci][pj]
        if op == "m":
            pins.append((cands[a], P[b][0], P[b][1]))
            ci, pj = a, b
        elif op == "sc":
            ci = a
        else:
            pj = b
    return pins[::-1]


def _spread(words, pins, speech_start, speech_end):
    out, prev, t0 = [], -1, speech_start
    for i, ps, pe in pins + [(len(words) - 1, speech_end, speech_end)]:
        seg = " ".join(words[prev + 1:i + 1])
        if seg:
            out += proportional(seg, t0, ps, final_pause=False)
        prev, t0 = i, pe
    return out


def pause_anchored(original_text: str, pauses: List[Tuple[float, float]],
                   speech_start: float, speech_end: float) -> List[Dict[str, Any]]:
    """Timing without (reliable) word timestamps. TTS pauses at punctuation but also takes
    dramatic breaths mid-sentence, and reads some commas without a pause. Two passes:
      1. pin sentence ends to the longer pauses (robust: breaths rarely compete there);
      2. with that much better estimate, match every punctuation mark to the remaining pauses
         with a tight tolerance, leaving breaths unmatched.
    Words are then spread by length between pins."""
    words = original_text.split()
    n = len(words)
    rough = proportional(original_text, speech_start, speech_end)
    P = [(a, b) for a, b in pauses if speech_start + 0.1 < a and b < speech_end - 0.1]
    strong = re.compile(r"[।॥.!?]$")
    sent = [i for i, w in enumerate(words[:-1]) if strong.search(w)]
    if not P:
        return rough
    long_p = [p for p in P if p[1] - p[0] >= 0.28] or P
    pins1 = _dp_pins(words, sent, long_p, [w["end"] for w in rough], scale=1.6,
                     skip_pause=1.0, skip_strong=2.5, skip_weak=0.5) if sent else []
    est = _spread(words, pins1, speech_start, speech_end)
    if len(est) != n:
        est, pins1 = rough, []
    # pass 2: all punctuation, all pauses, tight tolerance around the improved estimate
    cands = [i for i, w in enumerate(words[:-1]) if re.search(_PUNCT_END, w)]
    pins2 = _dp_pins(words, cands, P, [w["end"] for w in est], scale=0.45,
                     skip_pause=1.0, skip_strong=3.0, skip_weak=0.6)
    out = _spread(words, pins2, speech_start, speech_end)
    return out if len(out) == n else est


def _stream(tokens: List[str]) -> Tuple[str, List[int]]:
    chars, owner = [], []
    for i, tok in enumerate(tokens):
        for ch in tok:
            chars.append(ch); owner.append(i)
    return "".join(chars), owner


def _interpolate(words, starts, ends, duration, first, last):
    n = len(words)
    i = 0
    while i < n:
        if starts[i] is not None:
            i += 1
            continue
        j = i
        while j < n and starts[j] is None:
            j += 1
        left = ends[i - 1] if i > 0 else first
        right = starts[j] if j < n else last
        if right <= left:
            right = left + 0.25 * (j - i)
        step = (right - left) / (j - i)
        for k in range(i, j):
            starts[k] = left + step * (k - i)
            ends[k] = left + step * (k - i + 1)
        i = j
    for k in range(n):                                # enforce monotonic, non-zero durations
        if k and starts[k] < ends[k - 1]:
            starts[k] = ends[k - 1]
        if ends[k] <= starts[k]:
            ends[k] = starts[k] + 0.08
        ends[k] = min(ends[k], duration)


def _fmt(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(int(t * 1000), 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def caption_groups(words: List[Dict[str, Any]], per_caption: int = 3):
    """Short, punchy Shorts-style captions; break early at sentence punctuation.
    Yields (start, end, text)."""
    groups, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= per_caption or re.search(_PUNCT_END, w["word"]):
            groups.append(cur); cur = []
    if cur:
        groups.append(cur)
    out = []
    for i, g in enumerate(groups, 1):
        start = g[0]["start"]
        end = max(g[-1]["end"], start + 0.35)
        end = min(end, g[-1]["start"] + 1.2)          # a caption never lingers on a long silence
        if i < len(groups):
            end = min(end, groups[i][0]["start"])     # i is the next group's index (0-based)
        out.append((start, end, " ".join(x["word"] for x in g).upper()))
    return out


def build_srt(words: List[Dict[str, Any]], per_caption: int = 3) -> str:
    """Plain SRT (also handy to upload to YouTube as a caption track)."""
    return "\n".join(f"{i}\n{_fmt(a)} --> {_fmt(b)}\n{t}\n"
                     for i, (a, b, t) in enumerate(caption_groups(words, per_caption), 1))


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def build_ass(words: List[Dict[str, Any]], font: str, width: int, height: int,
              per_caption: int = 3, font_size: int = 96, hook_text: str = "",
              hook_seconds: float = 2.6, hook_size: int = 128, cta_text: str = "",
              cta_seconds: float = 2.6, end_time: float = 0.0) -> str:
    """ASS captions in real pixel units (PlayRes = video size). Rendered with the `ass`
    filter + complex shaping, which Devanagari conjuncts (e.g. ब्रह्मपुत्र) need."""
    margin_v = int(height * 0.24)                  # above the Shorts title/UI area
    header = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {width}\nPlayResY: {height}\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
        "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{font_size},&H0000D7FF,&H0000D7FF,&H00000000,&H96000000,"
        f"-1,0,0,0,100,100,0,0,1,7,4,2,70,70,{margin_v},1\n"
        # Hook: big white title in an opaque dark box, upper third (below the Shorts top bar)
        f"Style: Hook,{font},{hook_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H28140A1E,"
        f"-1,0,0,0,100,100,0,0,3,26,0,8,80,80,{int(height * 0.17)},1\n"
        # CTA: smaller gold-on-dark pill in the upper third during the last seconds
        f"Style: Cta,{font},{int(font_size * 0.72)},&H0000D7FF,&H0000D7FF,&H00000000,&H28140A1E,"
        f"-1,0,0,0,100,100,0,0,3,20,0,8,80,80,{int(height * 0.17)},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []
    if hook_text.strip():
        hook = hook_text.strip().replace("\n", " ")
        events.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(hook_seconds)},Hook,,0,0,0,,"
                      f"{{\\fad(0,250)}}{hook}")
    if cta_text.strip() and end_time > cta_seconds + 3:
        events.append(f"Dialogue: 1,{_ass_time(end_time - cta_seconds)},{_ass_time(end_time)},Cta,,0,0,0,,"
                      f"{{\\fad(250,0)}}{cta_text.strip()}")
    events += [f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},Default,,0,0,0,,{t.replace(chr(10), ' ')}"
              for a, b, t in caption_groups(words, per_caption)]
    return header + "\n".join(events) + "\n"


def scene_durations(words: List[Dict[str, Any]], scenes: List[Dict[str, Any]],
                    audio_duration: float) -> List[float]:
    """Each slide starts when its narration_segment starts; last slide runs to the end."""
    counts = [len(sc["narration_segment"].split()) for sc in scenes]
    if sum(counts) != len(words):
        return [audio_duration / len(scenes)] * len(scenes)
    starts, idx = [], 0
    for c in counts:
        starts.append(words[idx]["start"] if idx < len(words) else audio_duration)
        idx += c
    starts[0] = 0.0
    bounds = starts + [audio_duration]
    durs = [max(1.2, bounds[i + 1] - bounds[i]) for i in range(len(scenes))]
    scale = audio_duration / sum(durs)                # keep total exactly == audio length
    return [d * scale for d in durs]

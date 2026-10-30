"""FFmpeg rendering: per-slide Ken Burns clips -> concat -> voice + ducked music -> captions."""
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List

import config
import config_defaults  # noqa: F401  (fills settings missing from older config.py)
from utils import PipelineError, file_ok, log

W, H, FPS = config.WIDTH, config.HEIGHT, config.FPS


def check_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} not found on PATH. Install FFmpeg first.")
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    for f in ("zoompan", "ass", "sidechaincompress", "loudnorm"):
        if f not in out:
            raise SystemExit(f"Your FFmpeg build lacks the '{f}' filter (need libass etc.).")


def _run(cmd: List[str], cwd: str, what: str) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise PipelineError(f"FFmpeg {what} failed:\n{r.stderr[-1500:]}")


def _motion(camera: str, frames: int, punch: bool = False) -> str:
    z_max = 1.28 if punch else 1.15            # first clip moves faster to catch the eye
    step = (z_max - 1.0) / max(1, frames)
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if camera == "zoom_out":
        return f"z='{z_max}-{step:.6f}*on':x='{cx}':y='{cy}'"
    if camera == "pan_left":
        return f"z='1.12':x='(iw-iw/zoom)*(1-on/{frames})':y='{cy}'"
    if camera == "pan_right":
        return f"z='1.12':x='(iw-iw/zoom)*(on/{frames})':y='{cy}'"
    return f"z='1.0+{step:.6f}*on':x='{cx}':y='{cy}'"   # zoom_in


def render_clip(img: str, out: str, seconds: float, camera: str, cwd: str, punch: bool = False) -> None:
    frames = max(1, round(seconds * FPS))
    # Upscale 2x before zoompan: removes the well-known zoompan jitter on slow zooms.
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          f"scale={W * 2}:{H * 2}:flags=lanczos,"
          f"zoompan={_motion('zoom_in' if punch else camera, frames, punch)}:d={frames}:s={W}x{H}:fps={FPS},"
          f"format=yuv420p")
    _run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", img, "-vf", vf,
          "-frames:v", str(frames), "-r", str(FPS), "-c:v", "libx264", "-preset", "medium",
          "-crf", "18", "-pix_fmt", "yuv420p", "-an", out], cwd, f"clip {os.path.basename(img)}")


def _duration(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise PipelineError(f"ffprobe could not read {path}")


def verify_output(path: str, expected_s: float) -> None:
    """Fail loudly if the final file lacks audio, is silent, or is the wrong length."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    kinds = r.stdout.split()
    if "video" not in kinds:
        raise PipelineError("final render has no video stream")
    if "audio" not in kinds:
        raise PipelineError("final render has no audio stream")
    vd = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-map", "0:a", "-af", "volumedetect",
                         "-f", "null", "-"], capture_output=True, text=True).stderr
    m = re.search(r"mean_volume:\s*(-?[\d.]+|-inf) dB", vd)
    if not m or m.group(1) == "-inf" or float(m.group(1)) < -45:
        raise PipelineError(f"final render audio is silent (mean volume {m.group(1) if m else '?'} dB)")
    dur = _duration(path)
    if abs(dur - expected_s) > 1.5:
        raise PipelineError(f"final render is {dur:.1f}s, expected ~{expected_s:.1f}s")
    log.info(f"Verified output: video+audio, mean volume {m.group(1)} dB, {dur:.1f}s")


def build_audio_graph(inputs: List[str], voice_dur: float, durations: List[float],
                      music_file: str = None) -> str:
    """Voice + (ducked, faded music) + (optional SFX on the hook and scene changes) -> [a].
    Appends the needed -i inputs to `inputs`."""
    a = "aresample=48000,aformat=channel_layouts=stereo"
    parts = [f"[1:a]{a},asplit=2[vo][key]"]
    mix = ["[vo]"]
    idx = 2

    if not music_file and os.path.isfile(config.BG_MUSIC_PATH):
        music_file = config.BG_MUSIC_PATH                          # legacy single-track setup
    if music_file:
        inputs += ["-stream_loop", "-1", "-i", music_file]
        fade_out_at = max(0.0, voice_dur - config.MUSIC_FADE_OUT_S)
        parts.append(f"[{idx}:a]{a},volume={config.BG_MUSIC_VOLUME},"
                     f"afade=t=in:d={config.MUSIC_FADE_IN_S},"
                     f"afade=t=out:st={fade_out_at:.2f}:d={config.MUSIC_FADE_OUT_S}[bg]")
        parts.append("[bg][key]sidechaincompress=threshold=0.03:ratio=6:attack=15:release=350[duck]")
        mix.append("[duck]")
        idx += 1
    else:
        parts.append("[key]anullsink")
        log.info("No background music available; rendering voice only.")

    events = []                                                     # (file, start_seconds)
    if os.path.isfile(config.SFX_HOOK):
        events.append((config.SFX_HOOK, 0.0))
    if os.path.isfile(config.SFX_TRANSITION):
        t = 0.0
        for d in durations[:-1]:
            t += d
            events.append((config.SFX_TRANSITION, max(0.0, t - 0.15)))   # lead the cut slightly
    for k, (path, start) in enumerate(events):
        inputs += ["-i", path]
        ms = int(start * 1000)
        parts.append(f"[{idx}:a]{a},volume={config.SFX_VOLUME},adelay={ms}|{ms}[fx{k}]")
        mix.append(f"[fx{k}]")
        idx += 1

    if len(mix) == 1:
        parts.append("[vo]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]")
    else:
        parts.append("".join(mix) + f"amix=inputs={len(mix)}:duration=first:dropout_transition=0:"
                     "normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]")
    return ";".join(parts)


def render_video(topic: Dict[str, Any], script: Dict[str, Any], ws: str, media: Dict[str, Any]) -> str:
    final = os.path.join(ws, "final_short.mp4")
    if file_ok(final, 500_000):
        try:
            verify_output(final, _duration(media["audio"]))
            log.info("final_short.mp4 already exists and is valid; skipping render.")
            return final
        except PipelineError as e:
            log.warning(f"Existing final_short.mp4 is bad ({e}); re-rendering.")
            os.remove(final)

    clips_dir = os.path.join(ws, "clips")
    os.makedirs(clips_dir, exist_ok=True)
    durations = media["timings"]["scene_durations"]
    concat_lines = []
    for i, (img, sc, dur) in enumerate(zip(media["scenes"], script["scenes"], durations), 1):
        clip = os.path.join(clips_dir, f"clip_{i:02d}.mp4")
        marker = clip + f".{round(dur * FPS)}f"        # re-render if timing changed
        if not (file_ok(clip, 10_000) and os.path.exists(marker)):
            log.info(f"Rendering clip {i:02d} ({dur:.2f}s, {sc.get('camera', 'zoom_in')})")
            render_clip(img, clip, dur, sc.get("camera", "zoom_in"), ws, punch=(i == 1))
            open(marker, "w").close()
        concat_lines.append(f"file 'clips/clip_{i:02d}.mp4'")
    with open(os.path.join(ws, "concat.txt"), "w") as fh:
        fh.write("\n".join(concat_lines) + "\n")

    _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "concat.txt",
          "-c", "copy", "slideshow.mp4"], ws, "concat")

    # Captions: our own ASS file (pixel units) + COMPLEX shaping, required for Devanagari.
    # Relative path (cwd=ws) avoids Windows/colon escaping bugs in filter arguments.
    fonts = f":fontsdir='{config.FONTS_DIR}'" if os.path.isdir(config.FONTS_DIR) else ""
    subs = f"ass=subtitles.ass:shaping=complex{fonts}"
    voice = os.path.relpath(media["audio"], ws)
    tmp_out = "final_short.part.mp4"
    voice_dur = _duration(os.path.join(ws, voice))

    # ONE filter graph for video + audio (mixing -vf with -filter_complex is unreliable
    # across FFmpeg versions and can silently drop the audio track on 7.x/8.x).
    inputs = ["-i", "slideshow.mp4", "-i", voice]
    graph = f"[0:v]{subs},format=yuv420p[v];"
    graph += build_audio_graph(inputs, voice_dur, durations, media.get("music"))
    cmd = (["ffmpeg", "-y", "-v", "error"] + inputs +
           ["-filter_complex", graph, "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-t", f"{voice_dur:.3f}", "-movflags", "+faststart", tmp_out])
    try:
        _run(cmd, ws, "final mux")
    except PipelineError as e:
        if "shaping" not in str(e):
            raise
        log.warning("This FFmpeg's ass filter has no 'shaping' option; retrying without it "
                    "(Hindi conjuncts may render incorrectly).")
        cmd[cmd.index("-filter_complex") + 1] = graph.replace(":shaping=complex", "")
        _run(cmd, ws, "final mux")
    verify_output(os.path.join(ws, tmp_out), voice_dur)
    os.replace(os.path.join(ws, tmp_out), final)
    log.info(f"Rendered {final}")
    return final

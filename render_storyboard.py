"""
render_storyboard.py - render a storyboard of still images into an MP4 with
Remotion (Ken Burns pans/zooms + a two-layer fake-parallax motion), living in
the video-flow project so any pipeline can use it.

Library use:
    from render_storyboard import render_storyboard
    render_storyboard(
        [{"image": r"F:...still.jpg", "duration": 5, "motion": "parallax"}, ...],
        r"F:...\\visual_timeline.mp4")

CLI use:
    python render_storyboard.py storyboard.json out.mp4
        storyboard.json = {"scenes": [{"image": ..., "duration": ..., "motion": ...}],
                           "width": 1920, "height": 1080, "fps": 30}

Motions: zoom-in | zoom-out | pan-right | pan-left | parallax.
Images are copied into remotion/public/ (Remotion only serves static files
from there). Requires Node.js/npx on PATH (or Pinokio's bundled copy).
"""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REMOTION_DIR = ROOT / "remotion"
PUBLIC_DIR = REMOTION_DIR / "public"
_PINOKIO_NODE = Path(r"C:\pinokio\bin\miniconda")

VALID_MOTIONS = ("zoom-in", "zoom-out", "pan-right", "pan-left", "parallax")


def _npx():
    """npx from PATH, else Pinokio's bundled Node."""
    from shutil import which
    found = which("npx")
    if found:
        return found
    if (_PINOKIO_NODE / "npx.cmd").exists():
        return str(_PINOKIO_NODE / "npx.cmd")
    raise RuntimeError("npx not found - install Node.js (or check C:/pinokio/bin/miniconda)")


def _probe_duration(path):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=60)
        return float(out.stdout.strip())
    except Exception:
        return None


def _output_valid(out_mp4, expected_seconds):
    """A non-zero Remotion exit can still leave a finished file behind (the
    flush/cleanup step fails on very long renders). Accept it when the MP4
    header is real and the file is either big (>50 MB) or measures within
    5% of the expected duration."""
    try:
        if not out_mp4.is_file() or out_mp4.stat().st_size < 1024 * 1024:
            return False
        with open(out_mp4, "rb") as f:
            if f.read(8)[4:8] != b"ftyp":
                return False
    except Exception:
        return False
    if out_mp4.stat().st_size > 50 * 1024 * 1024:
        return True
    if expected_seconds:
        d = _probe_duration(out_mp4)
        if d and d >= expected_seconds * 0.95:
            return True
    return False


def render_storyboard(scenes, out_mp4, width=1920, height=1080, fps=30, fade_s=0.5):
    """scenes: [{"image": <path>, "duration": <seconds>, "motion": <name>}, ...]
    -> Path of the rendered MP4 (out_mp4, absolute)."""
    if not scenes:
        raise RuntimeError("storyboard has no scenes")
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)

    fade_frames = max(1, int(round(fade_s * fps)))
    props = []
    for i, s in enumerate(scenes, 1):
        src = Path(s["image"]).resolve()
        if not src.is_file():
            raise RuntimeError(f"scene {i}: image not found: {src}")
        motion = s.get("motion", "zoom-in")
        if motion not in VALID_MOTIONS:
            motion = "zoom-in"
        dest = PUBLIC_DIR / f"still_{i:02d}{src.suffix.lower()}"
        shutil.copyfile(src, dest)
        props.append({"src": dest.name,
                      "durationInFrames": max(1, int(round(s["duration"] * fps))),
                      "motion": motion})

    props_file = REMOTION_DIR / "storyboard_props.json"
    props_file.write_text(
        json.dumps({"scenes": props, "fadeFrames": fade_frames}, indent=1),
        encoding="utf-8")

    out_mp4 = Path(out_mp4).resolve()
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd = [_npx(), "remotion", "render", "src/index.jsx", "Storyboard",
           str(out_mp4), "--props", str(props_file), "--jpeg"]
    print("Remotion:", " ".join(cmd), flush=True)
    # Three failure shapes, handled differently:
    #   - stale output from a PREVIOUS render still on disk -> delete it
    #     BEFORE rendering; the flush recovery must never mistake it for
    #     this attempt's work (that once muxed old visuals with new audio)
    #   - browser start flake (fails fast, no usable file) -> retry
    #   - long-render flush error (EVERY frame rendered and encoded, file
    #     complete and fresh, exit 1 at cleanup) -> validate & proceed
    if out_mp4.exists():
        try:
            out_mp4.unlink()
            print(f"removed stale {out_mp4.name} before rendering", flush=True)
        except OSError as e:
            print(f"could not remove stale {out_mp4.name} ({e})", flush=True)

    expected_s = sum(s.get("duration", 0) for s in scenes)
    render_started = time.time()
    last_err = None
    for attempt, backoff in ((1, 8), (2, 25), (3, 60), (4, 120)):
        try:
            proc = subprocess.run(cmd, cwd=str(REMOTION_DIR))
            rc = proc.returncode
        except Exception as e:
            # npx missing, process killed, OS-level error: treat exactly like
            # a non-zero exit - validate whatever output exists, then retry
            print(f"Remotion launch failed ({type(e).__name__}: {str(e)[:120]})", flush=True)
            rc = -1
        fresh = out_mp4.is_file() and out_mp4.stat().st_mtime >= render_started - 1
        if rc == 0 and fresh:
            last_err = None
            break
        if rc != 0 and fresh and _output_valid(out_mp4, expected_s):
            print("Remotion threw exit %s, but output file exists and looks valid. "
                  "Proceeding..." % rc, flush=True)
            last_err = None
            break
        last_err = f"exit {rc}"
        print(f"Remotion render failed ({last_err}) - retrying in {backoff}s "
              "(browser start can flake after browser-heavy steps) ...", flush=True)
        time.sleep(backoff)
    if last_err is not None:
        raise RuntimeError(f"Remotion render failed ({last_err}, after 4 attempts)")
    print(f"Remotion render done -> {out_mp4} ({out_mp4.stat().st_size // 1024} KB)", flush=True)
    return out_mp4


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: python render_storyboard.py <storyboard.json> <out.mp4>")
        sys.exit(1)
    sb = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    try:
        render_storyboard(sb["scenes"], sys.argv[2],
                          sb.get("width", 1920), sb.get("height", 1080),
                          sb.get("fps", 30))
    except Exception as e:
        print(f"FAILED - {e}")
        sys.exit(1)

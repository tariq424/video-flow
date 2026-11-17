"""Render a scene list into a silent picture timeline with the Remotion "Film"
composition: ghost-free depth-warp camera moves on stills (depth_warp.py), video clips, chapter cards and
lower-thirds. Sound is mixed separately (music_mix.py).

scenes: [{"duration": s, "image": path} | {"duration": s, "video": path, "extras": [paths]}]
overlays: [{"kind": "chapter"|"lower"|"caption", "at": s, "dur": s, ...text fields}]
"""
import json
import math
import shutil
import subprocess
from pathlib import Path

from depth_warp import render_shot
from render_storyboard import _npx, _probe_duration

ROOT = Path(__file__).resolve().parent
REMOTION = ROOT / "remotion"
FPS = 30
MAX_SHOT_S = 11.0
MOVES = ["push", "pan-left", "pull", "pan-right", "rise"]


def _frames(t):
    return int(round(t * FPS))


def plan(scenes, log=print):
    """-> list of shots in seconds: {"start", "dur", "kind": "still"|"video", "src", "move", ...}"""
    shots, t, k = [], 0.0, 0
    for n, sc in enumerate(scenes, 1):
        end = t + sc["duration"]
        if sc.get("video"):
            clip_s = _probe_duration(sc["video"]) or 5.0
            first = min(clip_s, end - t)
            shots.append({"start": t, "dur": first, "kind": "video", "src": sc["video"], "clip_s": clip_s, "scene": n})
            t += first
            rest = end - t
            extras = list(sc.get("extras") or [])
            if rest > 2.0 and extras:
                per = rest / len(extras)
                for e in extras:
                    for sub in _split(per):
                        shots.append({"start": t, "dur": sub, "kind": "still", "src": e, "move": MOVES[k % len(MOVES)], "scene": n})
                        t += sub
                        k += 1
            elif rest > 0.05:        # no extras: hold the clip's last frame for the rest
                shots[-1]["dur"] += rest
                t = end
        else:
            for sub in _split(sc["duration"]):
                shots.append({"start": t, "dur": sub, "kind": "still", "src": sc["image"], "move": MOVES[k % len(MOVES)], "scene": n})
                t += sub
                k += 1
        t = end
    return shots


def _split(d):
    n = max(1, math.ceil(d / MAX_SHOT_S - 1e-9))
    return [d / n] * n


def render(scenes, overlays, out_mp4, tag, log=print):
    """Render the picture timeline to out_mp4 (no audio). Returns the shot plan."""
    pub = REMOTION / "public" / "film" / tag
    if pub.exists():
        shutil.rmtree(pub)
    pub.mkdir(parents=True)
    shots = plan(scenes, log)
    items = []
    acc = 0
    for s in shots:
        a, b = _frames(s["start"]), _frames(s["start"] + s["dur"])   # cumulative rounding: no drift
        if b <= a:
            continue
        if s["kind"] == "video":
            dst = pub / f"clip_{s['scene']:02d}{Path(s['src']).suffix}"
            shutil.copyfile(s["src"], dst)
            items.append({"kind": "video", "src": f"film/{tag}/{dst.name}", "from": a, "dur": b - a,
                          "clipFrames": _frames(s["clip_s"])})
        else:
            # ghost-free depth warp, pre-rendered per shot (one image per frame - never doubled)
            dst = pub / f"shot_{len(items):03d}_{Path(s['src']).stem}.mp4"
            render_shot(s["src"], (b - a) / FPS, s["move"], dst, log)
            items.append({"kind": "video", "src": f"film/{tag}/{dst.name}", "from": a, "dur": b - a})
        acc = b
    ovs = [dict(o, **{"from": _frames(o["at"]), "dur": _frames(o["dur"])}) for o in overlays]
    props = REMOTION / f"film_props_{tag}.json"
    props.write_text(json.dumps({"items": items, "overlays": ovs, "total": acc}, indent=1), encoding="utf8")
    out_mp4 = Path(out_mp4).resolve()
    if out_mp4.exists():
        out_mp4.unlink()
    log(f"rendering {len(items)} shots, {acc} frames ({acc / FPS:.1f}s) with Remotion ...")
    cmd = [_npx(), "remotion", "render", "src/index.jsx", "Film", str(out_mp4), "--props", str(props),
           "--jpeg-quality", "92", "--muted", "--log", "error"]
    # the headless browser can fail to start (it flakes after browser-heavy
    # steps) - retry like render_storyboard.py does
    import time
    err = ""
    for attempt, backoff in ((1, 8), (2, 25), (3, 60), (4, 0)):
        r = subprocess.run(cmd, cwd=REMOTION, capture_output=True, text=True)
        if r.returncode == 0 and out_mp4.exists():
            return shots
        err = (r.stderr or r.stdout)[-1500:]
        if attempt < 4:
            log(f"Remotion render failed (attempt {attempt}): {err.strip().splitlines()[-1][:160] if err.strip() else '?'}"
                f" - retrying in {backoff}s")
            if out_mp4.exists():
                out_mp4.unlink()
            time.sleep(backoff)
    raise RuntimeError("Remotion render failed after 4 attempts:\n" + err)

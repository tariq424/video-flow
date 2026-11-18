"""Splice build: rebuild a film segment from an edited spec, but re-render in Remotion
only the frame ranges whose shots or timing changed; every other frame is copied from
the previous job's picture. Voice and music are rebuilt in full (cheap).

usage: python film_splice.py <new_spec.json> <old_spec.json> <old_job_tag> [job_kind]
Run from the media dir (e.g. F:/videos/google-flow-videos).
"""
import json
import subprocess
import sys
from pathlib import Path

import film_sequence as fs

REAL_RUN = subprocess.run
FPS = fs.FPS


class _Stop(Exception):
    pass


def _desc(s):
    keys = ("still", "move", "focus", "video", "start", "end", "reverse", "grade", "vanish", "audio_db")
    return json.dumps({k: s.get(k) for k in keys}, sort_keys=True)


def _shots_of(spec):
    """Dry run: the shot descriptor of every Remotion item, in order (no rendering)."""
    rec = []
    orig = (fs.render_framed, fs.video_shot, fs.subprocess, fs.ThreadPoolExecutor)

    class Ex:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def map(self, f, jobs):
            for s, _sec, _out in jobs:
                rec.append(_desc(s))
            raise _Stop

    fs.ThreadPoolExecutor = Ex
    try:
        fs.build(spec)
    except _Stop:
        pass
    finally:
        fs.render_framed, fs.video_shot, fs.subprocess, fs.ThreadPoolExecutor = orig
    return rec


def _ranges(old_items, old_desc, new_items, new_desc, old_ov, new_ov, total):
    bad, n = [], min(len(old_items), len(new_items))
    diverge = None
    for i in range(n):
        o, w = old_items[i], new_items[i]
        if (o["from"], o["dur"], o["xf"]) != (w["from"], w["dur"], w["xf"]):
            diverge = w["from"] if diverge is None else diverge
            break
        if old_desc[i] != new_desc[i]:
            bad.append([w["from"], w["from"] + w["dur"]])
    if diverge is None and len(old_items) != len(new_items):
        diverge = new_items[n]["from"] if n < len(new_items) else old_items[n]["from"]
    for o in new_ov:                       # overlays that are new or moved
        if o not in old_ov:
            bad.append([o["from"], o["from"] + o["dur"]])
    for o in old_ov:
        if o not in new_ov:
            bad.append([o["from"], o["from"] + o["dur"]])
    if diverge is not None:
        bad = [r for r in bad if r[0] < diverge] + [[diverge, total]]
    bad = sorted([max(0, a), min(total, b)] for a, b in bad)
    merged = []
    for a, b in bad:
        if merged and a <= merged[-1][1] + 30:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def main():
    new_spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf8"))
    old_spec = json.loads(Path(sys.argv[2]).read_text(encoding="utf8"))
    old_tag = sys.argv[3]
    if len(sys.argv) > 4:
        new_spec["job_kind"] = sys.argv[4]
    md = Path(new_spec["media_dir"])
    old_props = json.loads((fs.REMOTION / f"film_props_{old_tag}.json").read_text(encoding="utf8"))
    old_picture = md / "film_work" / old_tag / "picture.mp4"
    fs.log("dry run of the old spec (shot list only) ...")
    fs.CLIP_CONTINUITY = bool(old_props.get("clip_continuity"))   # replay the old job exactly as it was built
    old_desc = _shots_of(old_spec)
    fs.CLIP_CONTINUITY = True
    assert len(old_desc) == len(old_props["items"]), "old spec does not reproduce the old job's shots"

    new_desc = []
    orig_render_framed, orig_video_shot = fs.render_framed, fs.video_shot

    class Shim:
        """Stands in for the subprocess module inside film_sequence: the full Remotion
        render becomes range renders + a splice with the old picture."""
        def __getattr__(self, k):
            return getattr(subprocess, k)

        def run(self, cmd, *a, **k):
            if "remotion" not in cmd:
                return REAL_RUN(cmd, *a, **k)
            props_path = Path(cmd[cmd.index("--props") + 1])
            picture = Path(cmd[5])
            new_props = json.loads(props_path.read_text(encoding="utf8"))
            total = new_props["total"]
            rngs = _ranges(old_props["items"], old_desc, new_props["items"], new_desc,
                           old_props["overlays"], new_props["overlays"], total)
            fs.log("re-rendering frame ranges: " + ", ".join(f"{a}-{b - 1}" for a, b in rngs)
                   + f" ({sum(b - a for a, b in rngs)} of {total} frames)")
            parts = []
            for n, (a, b) in enumerate(rngs):
                out = picture.with_name(f"range{n:02d}.mp4")
                c = cmd[:5] + [str(out)] + cmd[6:] + [f"--frames={a}-{b - 1}", "--timeout=120000"]
                for attempt in range(6):
                    r = REAL_RUN(c, cwd=fs.REMOTION, capture_output=True, text=True)
                    if r.returncode == 0 and out.exists():
                        break
                    fs.log(f"  range {a}-{b - 1}: Remotion attempt {attempt + 1} failed; retrying")
                else:
                    return r
                parts.append((a, b, out))
            # one ffmpeg pass: old picture where unchanged, new ranges where changed
            inputs, chains, labels, pos = ["-i", str(old_picture)], [], [], 0
            for n, (a, b, out) in enumerate(parts):
                if a > pos:
                    chains.append(f"[0:v]trim=start_frame={pos}:end_frame={a},setpts=PTS-STARTPTS[k{n}]")
                    labels.append(f"[k{n}]")
                inputs += ["-i", str(out)]
                chains.append(f"[{n + 1}:v]setpts=PTS-STARTPTS[r{n}]")
                labels.append(f"[r{n}]")
                pos = b
            if pos < total:
                chains.append(f"[0:v]trim=start_frame={pos}:end_frame={total},setpts=PTS-STARTPTS[kend]")
                labels.append("[kend]")
            fc = ";".join(chains) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=1:a=0[v]"
            REAL_RUN(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", fc, "-map", "[v]",
                      "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p",
                      "-r", str(FPS), str(picture)], check=True)
            fs.log(f"spliced picture: {fs.dur(picture):.2f}s")
            return subprocess.CompletedProcess(cmd, 0, "", "")

    def rec_framed(still, seconds, move, out, *a, **k):
        return orig_render_framed(still, seconds, move, out, *a, **k)

    # record the new shot descriptors in Remotion item order
    orig_tpe = fs.ThreadPoolExecutor

    class RecEx(orig_tpe):
        def map(self, f, jobs):
            jobs = list(jobs)
            new_desc.extend(_desc(s) for s, _sec, _out in jobs)
            return super().map(f, jobs)

    fs.ThreadPoolExecutor = RecEx
    fs.subprocess = Shim()
    fs.build(new_spec)


if __name__ == "__main__":
    main()

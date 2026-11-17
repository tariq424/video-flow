"""Build a finished film from a spec: picture (film_timeline.py) + soundtrack
(music_mix.py), then register it as a render job in the factory GUI.

python film_build.py spec.json

spec = {
  "media_dir": "F:/videos/google-flow-videos",
  "manifest": "storyboard_manifest.json",        # scenes, modes, extras
  "narration": "narration.mp3",                  # + narration.mp3.meta.json segments
  "music_library": "music_library/library.json",
  "music": [{"mood": "somber"}, ...],            # one per scene; "swell": true for dramatic
  "clip_sound_db": -6,                           # the clips' own sound, under the voice
  "overlays": [{"kind": "chapter", "at": 0.6, "dur": 5, "num": "CHAPTER ONE", "title": "The Flock"}, ...],
  "job_kind": "film",
  "copy_job_from": "renders/<id>"                # optional: reuse that job's manifest/narration/stills for the review page
}
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import film_timeline  # noqa: E402
import music_mix  # noqa: E402


def log(m):
    print(m, flush=True)


def _dur(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True).stdout.strip()
    return float(out or 0)


def _has_audio(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                          "-of", "csv=p=0", str(p)], capture_output=True, text=True).stdout.strip()
    return bool(out)


def scene_durations(narr, n):
    meta = json.loads(Path(str(narr) + ".meta.json").read_text(encoding="utf8"))
    seg = meta.get("segments") or []
    total = _dur(narr)
    if len(seg) != n:
        raise SystemExit(f"narration has {len(seg)} timed segments but the manifest has {n} scenes")
    seg = list(seg)
    seg[-1] += total - sum(seg)      # the last scene ends with the audio
    return seg, total


def build(spec):
    t0 = time.time()
    md = Path(spec["media_dir"])
    man = json.loads((md / spec["manifest"]).read_text(encoding="utf8"))
    narr = md / spec["narration"]
    durs, total = scene_durations(narr, len(man["scenes"]))
    scenes = []
    for s, d in zip(man["scenes"], durs):
        n = s["n"]
        if s.get("mode") == "video":
            scenes.append({"duration": d, "video": str(md / f"scene_{n:02d}.mp4"),
                           "extras": [str(md / e["asset"]) for e in s.get("extras") or []]})
        else:
            scenes.append({"duration": d, "image": str(md / f"scene_{n:02d}.jpg")})
    tag = time.strftime("%Y%m%d_%H%M%S")
    work = md / "film_work" / tag
    work.mkdir(parents=True, exist_ok=True)

    log(f"film build {tag}: {len(scenes)} scenes, narration {total:.2f}s")
    picture = work / "picture.mp4"
    shots = film_timeline.render(scenes, spec.get("overlays") or [], picture, tag, log)

    # music: one mood per scene -> cues (consecutive same-mood scenes merge)
    lib = json.loads((md / spec["music_library"]).read_text(encoding="utf8"))
    by_mood = {t["mood"]: md / Path(spec["music_library"]).parent / t["file"] for t in lib}
    cues, t = [], 0.0
    for (sc, d), m in zip(zip(scenes, durs), spec["music"]):
        f = by_mood[m["mood"]]
        if cues and cues[-1]["file"] == f and cues[-1]["swell"] == bool(m.get("swell")):
            cues[-1]["end"] = t + d
        else:
            cues.append({"file": f, "start": t, "end": t + d, "swell": bool(m.get("swell"))})
        t += d
    clip_sounds = [{"file": s["src"], "start": s["start"], "dur": s["dur"], "db": spec.get("clip_sound_db", -6)}
                   for s in shots if s["kind"] == "video" and _has_audio(s["src"])]
    audio = work / "soundtrack.m4a"
    music_mix.mix(narr, cues, clip_sounds, total, audio, log)

    final = work / "video.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(picture), "-i", str(audio),
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "copy", "-shortest", str(final)], check=True)
    vd, ad = _dur(final), _dur(narr)
    log(f"final {vd:.2f}s vs narration {ad:.2f}s (delta {abs(vd - ad):.2f}s)")

    jid = register(md, final, spec, tag)
    log(f"registered render job {jid} ({time.time() - t0:.0f}s total)")
    return jid


def register(md, final, spec, jid):
    jobs_dir = md / "renders"
    dest = jobs_dir / jid
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(final, dest / "video.mp4")
    src = spec.get("copy_job_from")
    has_narr = has_beats = False
    if src:
        s = md / src
        for name in ("narration.txt", "manifest.json"):
            if (s / name).exists():
                shutil.copyfile(s / name, dest / name)
        if (s / "stills").is_dir():
            shutil.copytree(s / "stills", dest / "stills", dirs_exist_ok=True)
        has_narr, has_beats = (dest / "narration.txt").exists(), (dest / "manifest.json").exists()
    jf = jobs_dir / "jobs.json"
    jobs = json.loads(jf.read_text(encoding="utf8")) if jf.exists() else []
    jobs.insert(0, {"id": jid, "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": spec.get("job_kind", "film"),
                    "size": (dest / "video.mp4").stat().st_size, "has_final": True,
                    "has_narr_text": has_narr, "has_beats": has_beats})
    jf.write_text(json.dumps(jobs[:50], indent=1), encoding="utf8")   # the GUI keeps 50 (app.JOBS_KEEP)
    return jid


if __name__ == "__main__":
    build(json.loads(Path(sys.argv[1]).read_text(encoding="utf8")))

"""Build a film section from an explicit sequence of pieces, with the picture
cut on SENTENCE boundaries so it never runs ahead of or behind the narration.

pieces (story order):
  {"say": paragraph, "voice": "narrator"|"<edge voice>", "shots": [...], "overlays": [...]}
      each sentence is voiced separately, so its start time is exact;
      a shot {"at": k} starts when sentence k starts (several shots with the
      same "at" share that sentence's time).
  {"clip": path, "start": s, "end": s, "cutaway_at": s, "shots": [...]}
      a speaking clip: its own audio is the voice.
  {"music_only": seconds, "shots": [...], "overlays": [...]}
      no voice (the hook): music plays loud.
shot:  {"still": path, "move": "push"|"wide"|"close"|"pull"|"pan-lr"|"pan-rl"|"tilt-down"|"dolly", "focus": [x, y]}
       {"video": path, "start": s, "end": s, "reverse": bool, "grade": "cctv"|"handheld", "audio_db": -12}
overlay: {"kind": "chapter"|"lower"|"caption", "at_sent": k (or "at": s), "dur": s, ...}
spec["music"]: [{"mood": m, "from": piece, "to": piece, "swell": bool}]
"""
import asyncio
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import music_mix  # noqa: E402
from depth_warp import render_framed  # noqa: E402
from film_build import register  # noqa: E402
from render_storyboard import _npx  # noqa: E402

REMOTION = ROOT / "remotion"
FPS, W, H = 30, 1920, 1080
BREATH = 0.5          # after a paragraph / line
SENT_GAP = 0.12       # between sentences of one paragraph
VOICES = {"narrator": "en-US-ChristopherNeural"}
_SPLIT = r'(?<=[.!?…])(?<!\bDr\.)(?<!\bMr\.)(?<!\bMrs\.)(?<!\bSt\.)\s+(?=[A-Z"\'0-9])'


XF = 14   # dissolve length in frames (~0.47 s)


def log(m):
    print(m, flush=True)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(" ".join(map(str, cmd[:6])) + " ...\n" + r.stderr[-1500:])
    return r


def dur(p):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)]).stdout.strip())


def sentences(text):
    return [s for s in re.split(_SPLIT, text.strip()) if s]


def tts(text, voice, out_wav, cache_dir):
    import edge_tts
    vid = VOICES.get(voice, voice)
    key = hashlib.sha1(f"{vid}|{text}".encode("utf8")).hexdigest()[:16]
    cached = Path(cache_dir) / f"{key}.wav"
    if cached.exists():
        shutil.copyfile(cached, out_wav)
        return dur(out_wav)
    mp3 = out_wav.with_suffix(".mp3")

    async def go():
        await edge_tts.Communicate(text, voice=vid).save(str(mp3))
    for attempt, wait in ((1, 5), (2, 15), (3, 40), (4, 0)):
        try:
            asyncio.run(go())
            if mp3.stat().st_size > 0:
                break
        except Exception as e:  # noqa: BLE001 - the service drops connections
            if attempt == 4:
                raise
            log(f"  TTS {type(e).__name__} - retry {attempt} in {wait}s")
            time.sleep(wait)
    run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3), "-ar", "48000", "-ac", "2", str(out_wav)])
    cached.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(out_wav, cached)
    return dur(out_wav)


def silence(seconds, out_wav):
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{seconds:.3f}", str(out_wav)])


def clip_audio(src, start, end, out_wav):
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(src)]
    if end:
        cmd += ["-t", f"{end - start:.3f}"]
    run(cmd + ["-vn", "-ar", "48000", "-ac", "2", str(out_wav)])
    return dur(out_wav)


def video_shot(src, start, end, seconds, out_mp4, reverse=False, grade=None):
    vf = [f"trim={start:.3f}:{end:.3f}" if end else f"trim=start={start:.3f}", "setpts=PTS-STARTPTS"]
    if reverse:
        vf.append("reverse")
    vf += [f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos", f"crop={W}:{H}",
           "unsharp=5:5:0.6", f"fps={FPS}"]
    if grade == "cctv":
        vf += ["hue=s=0.35", "noise=alls=14:allf=t", "vignette=PI/4"]
    elif grade == "handheld":          # Aris's own camera footage
        vf += ["eq=contrast=1.08:saturation=0.85", "noise=alls=9:allf=t", "vignette=PI/5"]
    vf += [f"tpad=stop_mode=clone:stop_duration={seconds + 1:.3f}", f"trim=duration={seconds:.3f}"]
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-an", "-vf", ",".join(vf),
         "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(out_mp4)])


def build(spec):
    t0 = time.time()
    md = Path(spec["media_dir"])
    tag = time.strftime("%Y%m%d_%H%M%S")
    work = md / "film_work" / tag
    work.mkdir(parents=True, exist_ok=True)
    pub = REMOTION / "public" / "film" / tag
    pub.mkdir(parents=True, exist_ok=True)
    cache = md / "film_work" / "tts_cache"

    # 1) audio, with the start time of every sentence
    parts, pieces, t = [], [], 0.0
    for i, p in enumerate(spec["pieces"]):
        sent_starts = []
        if "say" in p:
            ss = sentences(p["say"])
            for j, s in enumerate(ss):
                a = work / f"a{i:03d}_{j:02d}.wav"
                d = tts(s, p.get("voice", "narrator"), a, cache)
                sent_starts.append(t)
                parts.append(a)
                t += d
                if j < len(ss) - 1:
                    g = work / f"g{i:03d}_{j:02d}.wav"
                    silence(SENT_GAP, g)
                    parts.append(g)
                    t += SENT_GAP
            label = p["say"][:60]
        elif "clip" in p:
            a = work / f"a{i:03d}.wav"
            d = clip_audio(md / p["clip"], p.get("start", 0), p.get("end"), a)
            sent_starts.append(t)
            parts.append(a)
            t += d
            label = p["clip"]
        else:
            a = work / f"a{i:03d}.wav"
            silence(p["music_only"], a)
            sent_starts.append(t)
            parts.append(a)
            t += p["music_only"]
            label = "(music only)"
        g = work / f"b{i:03d}.wav"
        silence(BREATH, g)
        parts.append(g)
        t += BREATH
        pieces.append({"spec": p, "start": sent_starts[0], "end": t, "sent": sent_starts})
        log(f"  piece {i:02d} {t - sent_starts[0]:5.1f}s  {label}")
    total = t
    lst = work / "voice_list.txt"
    lst.write_text("".join(f"file '{x.as_posix()}'\n" for x in parts), encoding="utf8")
    voice = work / "voice.wav"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(voice)])
    log(f"voice track {total:.1f}s")

    # 2) shot timings: each shot starts at its sentence; several on one sentence share it
    jobs, items, overlays, clip_sounds = [], [], [], []
    for i, pc in enumerate(pieces):
        p = pc["spec"]
        shots = [dict(s) for s in (p.get("shots") or [])]
        if "clip" in p:
            cl = {"video": p["clip"], "start": p.get("start", 0), "end": p.get("end"), "at": 0}
            if p.get("cutaway_at"):
                cl["end"] = p.get("start", 0) + p["cutaway_at"]
                cl["len"] = p["cutaway_at"]
            for s in shots:
                s.setdefault("at", 0)
                s["after_clip"] = True
            shots = [cl] + shots
        starts = []
        for s in shots:
            k = min(s.get("at", 0), len(pc["sent"]) - 1)
            starts.append(pc["sent"][k])
        if starts and min(starts) > pc["sent"][0]:      # never leave the piece's first words without a picture
            first = starts.index(min(starts))
            starts = [pc["sent"][0] if s0 == starts[first] else s0 for s0 in starts]
        # shots sharing a start time split the span up to the next distinct start
        bounds = sorted(set(starts)) + [pc["end"]]
        for st in sorted(set(starts)):
            grp = [s for s, s0 in zip(shots, starts) if s0 == st]
            nxt = bounds[bounds.index(st) + 1]
            if grp and grp[0].get("len"):                      # speaking clip + cutaway
                first = grp[0]["len"]
                spans = [first] + [(nxt - st - first) / max(1, len(grp) - 1)] * (len(grp) - 1)
            else:
                spans = [(nxt - st) / len(grp)] * len(grp)
            x = st
            for s, sp in zip(grp, spans):
                a, b = round(x * FPS), round((x + sp) * FPS)
                x += sp
                if b <= a:
                    continue
                out = pub / f"s{len(items):03d}.mp4"
                # every shot runs XF frames past its cut; the next shot dissolves in over it
                jobs.append((s, (b - a + XF) / FPS, out))
                items.append({"kind": "video", "src": f"film/{tag}/{out.name}", "from": a, "dur": b - a + XF,
                              "xf": 0 if (not items or s.get("cut")) else XF})
                if s.get("rec"):                       # seen through Aris's camera
                    overlays.append({"kind": "rec", "from": a, "dur": b - a, "tc": round(a / FPS, 2) % 600})
                if "video" in s and s.get("audio_db") is not None:
                    clip_sounds.append({"file": md / s["video"], "start": a / FPS, "dur": (b - a) / FPS,
                                        "db": s["audio_db"], "src_start": s.get("start", 0)})
        for o in p.get("overlays") or []:
            at = pc["sent"][min(o.get("at_sent", 0), len(pc["sent"]) - 1)] + o.get("at", 0)
            overlays.append(dict(o, **{"from": round(at * FPS), "dur": round(o["dur"] * FPS)}))

    def render_one(job):
        s, seconds, out = job
        if "still" in s:
            render_framed(md / s["still"], seconds, s.get("move", "push"), out, s.get("focus"), lambda m: None,
                          vanish=s.get("vanish"))
        else:
            video_shot(md / s["video"], s.get("start", 0), s.get("end"), seconds, out,
                       s.get("reverse", False), s.get("grade"))
    log(f"rendering {len(jobs)} shots in parallel ...")
    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(render_one, jobs))

    total_f = round(total * FPS)
    props = REMOTION / f"film_props_{tag}.json"
    props.write_text(json.dumps({"items": items, "overlays": overlays, "total": total_f}, indent=1), encoding="utf8")
    picture = work / "picture.mp4"
    log(f"Remotion: {len(items)} shots, {total_f} frames ...")
    cmd = [_npx(), "remotion", "render", "src/index.jsx", "Film", str(picture), "--props", str(props),
           "--jpeg-quality", "92", "--muted", "--log", "error"]
    for attempt, wait in ((1, 8), (2, 25), (3, 60), (4, 0)):
        r = subprocess.run(cmd, cwd=REMOTION, capture_output=True, text=True)
        if r.returncode == 0 and picture.exists():
            break
        log(f"Remotion attempt {attempt} failed; retrying in {wait}s")
        time.sleep(wait)
    else:
        raise RuntimeError("Remotion render failed:\n" + (r.stderr or r.stdout)[-1500:])

    # 3) sound: clip sounds under the voice, music ducked under everything spoken
    lib = json.loads((md / spec["music_library"]).read_text(encoding="utf8"))
    by_mood = {x["mood"]: md / Path(spec["music_library"]).parent / x["file"] for x in lib}
    cues = [{"file": by_mood[m["mood"]], "start": pieces[m["from"]]["start"], "end": pieces[m["to"]]["end"],
             "swell": m.get("swell", False), "db": m.get("db")} for m in spec["music"]]
    if clip_sounds:                        # trim each source to its excerpt first
        for n, c in enumerate(clip_sounds):
            w = work / f"cs{n:02d}.wav"
            clip_audio(c["file"], c["src_start"], c["src_start"] + c["dur"], w)
            c["file"] = w
    audio = work / "soundtrack.m4a"
    music_mix.mix(voice, cues, clip_sounds, total, audio, log)
    final = work / "video.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(picture), "-i", str(audio), "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "copy", "-shortest", str(final)])
    log(f"final {dur(final):.2f}s (voice {total:.2f}s)")
    jid = register(md, final, spec, tag)
    log(f"registered render job {jid} ({time.time() - t0:.0f}s)")
    return jid


if __name__ == "__main__":
    build(json.loads(Path(sys.argv[1]).read_text(encoding="utf8")))

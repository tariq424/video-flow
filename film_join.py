"""Join finished segment renders into one film, leaving the segments untouched.

- every segment is levelled to the same integrated loudness (default -14 LUFS, YouTube's
  reference) with one fixed gain each, so nothing is pumped or re-compressed
- 1 s video dissolve + audio crossfade at every seam
- registered as a new render job

usage: python film_join.py <media_dir> <job_kind> <seg_job_id> [<seg_job_id> ...]
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from film_build import register

TARGET = -14.0
XF = 1.0


def dur(p):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(p)], capture_output=True, text=True).stdout.strip())


def loudness(p):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(p), "-map", "0:a", "-af", "ebur128=peak=true",
                        "-f", "null", "-"], capture_output=True, text=True)
    tail = r.stderr[r.stderr.rfind("Summary:"):]
    i = float(re.search(r"I:\s*(-?[\d.]+) LUFS", tail).group(1))
    pk = float(re.search(r"Peak:\s*(-?[\d.]+) dBFS", tail).group(1))
    return i, pk


def main():
    md, kind, ids = Path(sys.argv[1]), sys.argv[2], sys.argv[3:]
    srcs = [md / "renders" / j / "video.mp4" for j in ids]
    durs = [dur(s) for s in srcs]
    gains = []
    for j, s in zip(ids, srcs):
        i, pk = loudness(s)
        g = TARGET - i          # same loudness for every segment; peaks are caught by the limiter below
        gains.append(g)
        print(f"{j}: {i:.1f} LUFS, peak {pk:.1f} dBFS -> gain {g:+.1f} dB", flush=True)
    tag = time.strftime("%Y%m%d_%H%M%S")
    work = md / "film_work" / f"join_{tag}"
    work.mkdir(parents=True, exist_ok=True)
    args = ["ffmpeg", "-y", "-v", "error", "-stats"]
    for s in srcs:
        args += ["-i", str(s)]
    fc = []
    for k, g in enumerate(gains):
        fc.append(f"[{k}:v]settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p[v{k}]")
        fc.append(f"[{k}:a]asetpts=PTS-STARTPTS,aresample=48000,volume={g:.2f}dB[a{k}]")
    vprev, aprev, t = "v0", "a0", durs[0]
    for k in range(1, len(srcs)):
        off = t - XF
        fc.append(f"[{vprev}][v{k}]xfade=transition=fade:duration={XF}:offset={off:.3f}[vx{k}]")
        fc.append(f"[{aprev}][a{k}]acrossfade=d={XF}:c1=tri:c2=tri[ax{k}]")
        vprev, aprev, t = f"vx{k}", f"ax{k}", t + durs[k] - XF
    fc.append(f"[{aprev}]alimiter=limit=0.89:attack=5:release=80:level=disabled[alim]")   # -1 dBFS ceiling
    aprev = "alim"
    out = work / "video.mp4"
    args += ["-filter_complex", ";".join(fc), "-map", f"[{vprev}]", "-map", f"[{aprev}]",
             "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p", "-r", "30",
             "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", str(out)]
    print(f"joining {len(srcs)} segments, expected {t:.2f}s ...", flush=True)
    subprocess.run(args, check=True)
    starts, acc = [], 0.0
    for k, d in enumerate(durs):
        starts.append(round(acc, 3))
        acc += d - XF
    meta = {"segments": ids, "durations": durs, "gains_db": gains, "starts_in_film": starts, "crossfade": XF}
    (work / "join.json").write_text(json.dumps(meta, indent=1), encoding="utf8")
    jid = register(md, out, {"job_kind": kind}, tag)
    (md / "renders" / jid / "join.json").write_text(json.dumps(meta, indent=1), encoding="utf8")
    print(f"final {dur(out):.2f}s -> registered render job {jid}", flush=True)


if __name__ == "__main__":
    main()

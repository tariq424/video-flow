"""
tts.py - narration synthesis via edge-tts for the video-flow project.

    python tts.py <script.txt> <out.mp3> [voice]

The script has ONE line per scene. Each line is voiced separately and timed
exactly, then the pieces are joined into out.mp3; the per-scene lengths are
written to <out>.meta.json as "segments" so the build can give every scene
exactly its own narration time. A line "[SILENCE]" is a short pause for a
scene without narration (FLOW_TTS_GAP_S seconds, default 3). Blank lines
are ignored.

Library use:
    from tts import synth
    synth(["line one.", "line two."], r"F:...\\narration.mp3")

Default voice: en-US-ChristopherNeural (standard deep male documentary
voice). Override with the FLOW_TTS_VOICE env var or the CLI argument.
Requires internet (Microsoft's TTS service) and ffmpeg/ffprobe on PATH.
"""

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

DEFAULT_VOICE = os.environ.get("FLOW_TTS_VOICE", "en-US-ChristopherNeural")
SILENCE = "[SILENCE]"
RATE = 24000                       # edge-tts native sample rate (mono)


async def _save(text, voice, out_path):
    import edge_tts
    tts = edge_tts.Communicate(text, voice=voice)
    await tts.save(out_path)


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {r.stderr[-300:]}")
    return r


def _voice_line(text, voice, mp3):
    """One edge-tts request, retried once (the service drops connections)."""
    for attempt in (1, 2):
        try:
            asyncio.run(_save(text, voice, mp3))
            if os.path.getsize(mp3) > 0:
                return
            raise RuntimeError("empty audio")
        except Exception as e:
            if attempt == 2:
                raise
            print(f"TTS: {type(e).__name__} - retrying once in 5s ...", flush=True)
            time.sleep(5)


def _wav_seconds(wav):
    """Exact length of a PCM wav from its sample count."""
    r = _run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-count_packets",
              "-show_entries", "stream=duration_ts,sample_rate", "-of", "json", wav])
    st = json.loads(r.stdout)["streams"][0]
    return int(st["duration_ts"]) / int(st["sample_rate"])


def synth(lines, out_mp3, voice=None):
    """Voice each line separately -> one out_mp3 + <out>.meta.json with the
    audio sha1 (provenance) and per-line "segments" in seconds.
    -> the text that was spoken."""
    voice = voice or DEFAULT_VOICE
    lines = [l.strip() for l in lines if l and l.strip()]
    spoken = [l for l in lines if l != SILENCE]
    if not spoken:
        raise RuntimeError("narration script is empty")
    gap = float(os.environ.get("FLOW_TTS_GAP_S", "3"))
    work = tempfile.mkdtemp(prefix="tts_")
    try:
        wavs, segments = [], []
        for k, line in enumerate(lines, 1):
            wav = os.path.join(work, f"seg_{k:03d}.wav")
            if line == SILENCE:
                _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                      "-i", f"anullsrc=r={RATE}:cl=mono", "-t", str(gap),
                      "-c:a", "pcm_s16le", wav])
            else:
                mp3 = os.path.join(work, f"seg_{k:03d}.mp3")
                _voice_line(line, voice, mp3)
                _run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3,
                      "-ar", str(RATE), "-ac", "1", "-c:a", "pcm_s16le", wav])
            wavs.append(wav)
            segments.append(round(_wav_seconds(wav), 4))
            print(f"TTS: scene line {k}/{len(lines)} -> {segments[-1]:.2f}s"
                  + (" (silence)" if line == SILENCE else ""), flush=True)

        # join the exact PCM pieces, then encode once
        lst = os.path.join(work, "list.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for w in wavs:
                f.write("file '" + w.replace("\\", "/") + "'\n")
        joined = os.path.join(work, "joined.wav")
        _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
              "-i", lst, "-c", "copy", joined])
        tmp_out = os.path.join(work, "out.mp3")
        _run(["ffmpeg", "-y", "-loglevel", "error", "-i", joined,
              "-c:a", "libmp3lame", "-b:a", "96k", tmp_out])
        shutil.copyfile(tmp_out, str(out_mp3))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    digest = hashlib.sha1(open(out_mp3, "rb").read()).hexdigest()
    text = " ".join(spoken)
    meta = {"sha1": digest, "chars": len(text), "voice": voice,
            "segments": segments, "total_s": round(sum(segments), 4)}
    with open(str(out_mp3) + ".meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    print(f"TTS: {len(segments)} scene segment(s), {sum(segments):.2f}s total; "
          f"provenance sha1={digest[:12]} recorded", flush=True)
    return text


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: python tts.py <script.txt> <out.mp3> [voice]")
        sys.exit(1)
    script_path, out_path = sys.argv[1], sys.argv[2]
    voice = sys.argv[3] if len(sys.argv) > 3 else None
    with open(script_path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    print(f"TTS: voicing {sum(1 for l in lines if l.strip())} scene line(s) "
          f"with {voice or DEFAULT_VOICE} ...", flush=True)
    text = synth(lines, out_path, voice)
    size = os.path.getsize(out_path) // 1024
    print(f"TTS: done -> {out_path} ({size} KB, {len(text)} chars)", flush=True)

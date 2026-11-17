"""Mix the final soundtrack: narration on top, a music bed underneath that
ducks whenever the narrator speaks, swells in dramatic scenes, crossfades
between cues, plus the clips' own sound (no music in the clips).

cues: [{"file": path, "start": s, "end": s, "swell": bool}] - consecutive scenes
      with the same file should already be merged into one cue (it then plays on
      continuously instead of restarting).
clip_sounds: [{"file": path, "start": s, "dur": s, "db": -4}]

Design recreated for video-flow from the approach used in Video Studio
(sidechain ducking under the voice); no shared code.
"""
import subprocess
from pathlib import Path

BED_DB = -17        # music level in the narrator's pauses (after loudness normalising)
SWELL_DB = -9       # dramatic scenes
FADE_S = 2.0


def mix(narration, cues, clip_sounds, total, out_audio, log=print, stem_out=None):
    """stem_out: also write the ducked music alone (for level checks)."""
    ins, parts, labels = ["-i", str(narration)], [], []
    k = 1
    for i, c in enumerate(cues):
        seg = max(0.1, c["end"] - c["start"])
        lvl = c["db"] if c.get("db") is not None else (SWELL_DB if c.get("swell") else BED_DB)
        ins += ["-stream_loop", "-1", "-i", str(c["file"])]
        fade_out_at = max(0.0, seg - FADE_S)
        ms = int(round(c["start"] * 1000))
        parts.append(
            f"[{k}:a]atrim=0:{seg + FADE_S:.3f},asetpts=PTS-STARTPTS,aresample=48000,"
            f"aformat=channel_layouts=stereo,loudnorm=I=-16:LRA=9:TP=-2,volume={lvl}dB,"
            f"afade=t=in:st=0:d={FADE_S},afade=t=out:st={fade_out_at:.3f}:d={FADE_S},"
            f"adelay={ms}|{ms}[m{i}]")
        labels.append(f"[m{i}]")
        k += 1
    parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest,"
                 f"atrim=0:{total:.3f}[music]")
    # duck the music under the voice; the voice itself is untouched
    parts.append("[0:a]aresample=48000,aformat=channel_layouts=stereo,asplit=2[voice][key]")
    parts.append("[music][key]sidechaincompress=threshold=0.02:ratio=10:attack=30:release=900:makeup=1"
                 + (",asplit=2[ducked][stem]" if stem_out else "[ducked]"))
    mixl = ["[voice]", "[ducked]"]
    for j, s in enumerate(clip_sounds):
        ins += ["-i", str(s["file"])]
        ms = int(round(s["start"] * 1000))
        parts.append(f"[{k}:a]atrim=0:{s['dur']:.3f},asetpts=PTS-STARTPTS,aresample=48000,"
                     f"aformat=channel_layouts=stereo,volume={s.get('db', -4)}dB,"
                     f"afade=t=out:st={max(0, s['dur'] - 0.6):.3f}:d=0.6,adelay={ms}|{ms}[c{j}]")
        mixl.append(f"[c{j}]")
        k += 1
    parts.append(f"{''.join(mixl)}amix=inputs={len(mixl)}:normalize=0:duration=first,alimiter=limit=0.95[out]")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + ins + [
        "-filter_complex", ";".join(parts), "-map", "[out]", "-c:a", "aac", "-b:a", "256k", str(out_audio)]
    if stem_out:
        cmd += ["-map", "[stem]", "-c:a", "pcm_s16le", str(stem_out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("music mix failed:\n" + r.stderr[-2000:])
    log(f"soundtrack mixed -> {Path(out_audio).name} ({len(cues)} music cue(s), {len(clip_sounds)} clip sound(s))")
    return out_audio

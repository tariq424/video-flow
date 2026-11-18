"""Make a 9:16 Short (a hook / trailer) from a finished 16:9 render.

Layout (1080x1920):
  - background: the same shot, cover-filled, blurred and darkened
  - picture: the centre 75% of the frame width, 1080x810, in the middle
  - hook line: big text above the picture, on screen the whole time
  - end card: over the last 2.5 s, "Watch the full film" pointing at the link

Clips are cut from the render with their own sound (narration + music), with short audio
fades at every cut. Text is drawn with PIL (no ffmpeg font-path escaping on Windows).

usage: python short_maker.py <render.mp4> <out.mp4> "<hook>" a-b [a-b ...]
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1080, 1920, 30
PIC_W, PIC_H = 1080, 810
PIC_Y = (H - PIC_H) // 2
END_SECS = 2.5
MAX_SECS = 60.0
FONTS = ["C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf", "DejaVuSans-Bold.ttf"]


def _font(size):
    for f in FONTS:
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw, text, font, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= width or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _text_png(path, text, top, bottom, size=84, sub=None):
    """Transparent 1080x1920 PNG with `text` centred between y=top and y=bottom."""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = _font(size)
    while True:
        lines = _wrap(d, text, f, W - 120)
        lh = int(f.size * 1.18)
        if len(lines) * lh <= (bottom - top) or f.size <= 44:
            break
        f = _font(f.size - 6)
    block = len(lines) * lh + (int(size * 0.75) + 20 if sub else 0)
    y = top + max(0, (bottom - top - block) // 2)
    for ln in lines:
        x = (W - d.textlength(ln, font=f)) // 2
        d.text((x, y), ln, font=f, fill="white", stroke_width=max(3, f.size // 14), stroke_fill="black")
        y += lh
    if sub:
        fs = _font(int(size * 0.62))
        x = (W - d.textlength(sub, font=fs)) // 2
        d.text((x, y + 20), sub, font=fs, fill=(255, 214, 102), stroke_width=4, stroke_fill="black")
    im.save(path)


def _dur(p):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(p)], capture_output=True, text=True).stdout.strip() or 0)


def make_short(src, out, hook, clips, end_text="Watch the full film", end_sub="link below \u2193", log=print):
    src, out = Path(src), Path(out)
    total_src = _dur(src)
    clips = [(max(0.0, float(a)), min(total_src, float(b))) for a, b in clips]
    clips = [(a, b) for a, b in clips if b - a >= 0.5]
    if not clips:
        raise ValueError("no usable clips (each needs at least half a second)")
    total = sum(b - a for a, b in clips)
    if total > MAX_SECS + 0.01:
        raise ValueError(f"the Short would be {total:.1f}s; keep it to {MAX_SECS:.0f}s or less")
    tmp = Path(tempfile.mkdtemp(prefix="short_"))
    hook_png, end_png = tmp / "hook.png", tmp / "end.png"
    _text_png(hook_png, hook.strip() or " ", 140, PIC_Y - 40, size=88)
    _text_png(end_png, end_text, PIC_Y + PIC_H + 60, H - 200, size=78, sub=end_sub)

    args, chains, cat = ["ffmpeg", "-y", "-v", "error"], [], ""
    for i, (a, b) in enumerate(clips):
        args += ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(src)]
    n = len(clips)
    args += ["-loop", "1", "-i", str(hook_png), "-loop", "1", "-i", str(end_png)]
    for i, (a, b) in enumerate(clips):
        d = b - a
        chains.append(f"[{i}:v]fps={FPS},setpts=PTS-STARTPTS,split[s{i}a][s{i}b]")
        chains.append(f"[s{i}a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                      f"boxblur=24:2,eq=brightness=-0.18[bg{i}]")
        chains.append(f"[s{i}b]crop=iw*0.75:ih,scale={PIC_W}:{PIC_H}:flags=lanczos[fg{i}]")
        chains.append(f"[bg{i}][fg{i}]overlay=0:{PIC_Y},setsar=1[v{i}]")
        chains.append(f"[{i}:a]asetpts=PTS-STARTPTS,aresample=48000,"
                      f"afade=t=in:d=0.08,afade=t=out:st={max(0, d - 0.15):.3f}:d=0.15[a{i}]")
        cat += f"[v{i}][a{i}]"
    chains.append(f"{cat}concat=n={n}:v=1:a=1[cv][ca]")
    chains.append(f"[cv][{n}:v]overlay=0:0:shortest=1[hv]")
    chains.append(f"[hv][{n + 1}:v]overlay=0:0:shortest=1:enable='gte(t,{max(0, total - END_SECS):.3f})'[ov]")
    args += ["-filter_complex", ";".join(chains), "-map", "[ov]", "-map", "[ca]", "-t", f"{total:.3f}",
             "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p", "-r", str(FPS),
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
    log(f"Short: {n} clip(s), {total:.1f}s -> {out.name}")
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + r.stderr[-1200:])
    thumb = out.with_name("short_thumb_9_16.png")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{min(1.0, total / 3):.2f}", "-i", str(out),
                    "-frames:v", "1", str(thumb)], capture_output=True)
    meta = {"source": str(src), "hook": hook, "clips": clips, "seconds": round(total, 2)}
    out.with_name("short.json").write_text(json.dumps(meta, indent=1), encoding="utf8")
    return total


if __name__ == "__main__":
    rngs = [tuple(map(float, x.split("-"))) for x in sys.argv[4:]]
    print(make_short(sys.argv[1], sys.argv[2], sys.argv[3], rngs))

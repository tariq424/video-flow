"""cards.py - an opening title card and a closing subscribe card for long videos (owner, 2026-11-22).

    python cards.py <video.mp4> "<title>" [--still scene_01.jpg] [--target DIR]

The video is rewritten in place: ~3 s title card (the title over the first picture, blurred and darkened) + the video +
~5 s end card (title and a Subscribe pill; the right half stays clear for YouTube's end-screen elements).
Faceless ("headless") videos (owner, 2026-11-22): no person's or owner's name is ever drawn on a card.
The cards are silent. cards.json (in --target) records their lengths so the beat editor can find every beat's time.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

INTRO_S, OUTRO_S, W, H = 3.0, 5.0, 1920, 1080
FONTS = ["C:/Windows/Fonts/georgiab.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf"]


def _font(size):
    for f in FONTS:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def _wrap(d, text, font, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w
    return lines + [cur] if cur else lines


def _bg(still):
    if still and os.path.exists(still):
        im = Image.open(still).convert("RGB").resize((W, H))
        im = im.filter(ImageFilter.GaussianBlur(18))
        return Image.blend(im, Image.new("RGB", (W, H), (8, 10, 14)), 0.55)
    return Image.new("RGB", (W, H), (14, 17, 22))


def title_card(title, still, out):
    im = _bg(still)
    d = ImageDraw.Draw(im)
    f = _font(96)
    lines = _wrap(d, title, f, W - 360)[:3]
    y = H // 2 - len(lines) * 60
    for ln in lines:
        d.text(((W - d.textlength(ln, font=f)) / 2, y), ln, font=f, fill=(244, 239, 230))
        y += 120
    d.line([(W // 2 - 120, y + 20), (W // 2 + 120, y + 20)], fill=(214, 180, 106), width=4)
    im.save(out, quality=94)


def end_card(title, still, out):
    im = _bg(still)
    d = ImageDraw.Draw(im)
    x = 140
    f1, f3 = _font(64), _font(52)
    y = 300
    for ln in _wrap(d, title, f1, W // 2 - 200)[:3]:
        d.text((x, y), ln, font=f1, fill=(244, 239, 230))
        y += 80
    y += 40
    label = "Subscribe"
    tw = d.textlength(label, font=f3)
    d.rounded_rectangle([x, y, x + tw + 90, y + 96], radius=48, fill=(204, 0, 0))
    d.text((x + 45, y + 18), label, font=f3, fill=(255, 255, 255))
    im.save(out, quality=94)


def _probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,sample_rate,r_frame_rate",
                        "-of", "json", path], capture_output=True, text=True)
    st = json.loads(r.stdout or "{}").get("streams", [])
    a = next((s for s in st if s.get("codec_type") == "audio"), {})
    v = next((s for s in st if s.get("codec_type") == "video"), {})
    num, den = (v.get("r_frame_rate") or "30/1").split("/")
    return int(a.get("sample_rate") or 48000), round(int(num) / max(1, int(den)))


def add_cards(video, title, still=None, target=None):
    sr, fps = _probe(video)
    tmp = Path(tempfile.mkdtemp(prefix="cards_"))
    title_card(title, still, tmp / "intro.jpg")
    end_card(title, still, tmp / "outro.jpg")
    out = tmp / "with_cards.mp4"
    fc = (f"[0:v]scale={W}:{H},fps={fps},format=yuv420p,fade=t=in:st=0:d=0.6,fade=t=out:st={INTRO_S - 0.4}:d=0.4,setsar=1[i];"
          f"[1:v]scale={W}:{H},fps={fps},format=yuv420p,setsar=1[m];"
          f"[2:v]scale={W}:{H},fps={fps},format=yuv420p,fade=t=in:st=0:d=0.5,setsar=1[o];"
          f"[3:a]atrim=0:{INTRO_S},asetpts=PTS-STARTPTS[ia];[1:a]aresample={sr},asetpts=PTS-STARTPTS[ma];"
          f"[4:a]atrim=0:{OUTRO_S},asetpts=PTS-STARTPTS[oa];"
          f"[i][ia][m][ma][o][oa]concat=n=3:v=1:a=1[v][a]")
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-loop", "1", "-t", str(INTRO_S), "-i", str(tmp / "intro.jpg"), "-i", str(video),
           "-loop", "1", "-t", str(OUTRO_S), "-i", str(tmp / "outro.jpg"),
           "-f", "lavfi", "-t", str(INTRO_S), "-i", f"anullsrc=r={sr}:cl=stereo",
           "-f", "lavfi", "-t", str(OUTRO_S), "-i", f"anullsrc=r={sr}:cl=stereo",
           "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        raise SystemExit("cards: ffmpeg failed - " + r.stderr[-400:])
    os.replace(out, video)
    if target:
        Path(target, "cards.json").write_text(json.dumps({"intro_s": INTRO_S, "outro_s": OUTRO_S, "title": title}),
                                              encoding="utf-8")
    print(f"cards: {INTRO_S:.0f} s title card + {OUTRO_S:.0f} s end card added -> {video}", flush=True)


if __name__ == "__main__":
    a = sys.argv[1:]
    def opt(name, default=None):
        if name in a:
            i = a.index(name)
            v = a[i + 1]
            del a[i:i + 2]
            return v
        return default
    still, target = opt("--still"), opt("--target")
    if len(a) < 2:
        raise SystemExit(__doc__)
    add_cards(a[0], a[1], still, target)

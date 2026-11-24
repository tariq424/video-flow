"""cards.py - an opening title card and a closing subscribe card for long videos (owner, 2026-11-22).

    python cards.py <video.mp4> "<title>" [--still scene_01.jpg] [--target DIR]

The video is rewritten in place: ~3 s title card (the title over the first picture, blurred and darkened) + the video +
~5 s end card (title and a Subscribe pill; the right half stays clear for YouTube's end-screen elements).
Faceless ("headless") videos (owner, 2026-11-22): no person's or owner's name is ever drawn on a card.
The cards are silent. cards.json (in --target) records their lengths so the beat editor can find every beat's time.
"""
import json
import shutil
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
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,sample_rate,channels,r_frame_rate",
                        "-of", "json", path], capture_output=True, text=True)
    st = json.loads(r.stdout or "{}").get("streams", [])
    a = next((s for s in st if s.get("codec_type") == "audio"), {})
    v = next((s for s in st if s.get("codec_type") == "video"), {})
    num, den = (v.get("r_frame_rate") or "30/1").split("/")
    return int(a.get("sample_rate") or 48000), round(int(num) / max(1, int(den))), int(a.get("channels") or 2)


def _ff(args, what):
    r = subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error"] + args, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise SystemExit(f"cards: ffmpeg failed ({what}) - " + r.stderr[-400:])


def add_cards(video, title, still=None, target=None, out_path=None):
    """out_path: write the carded video there and leave `video` untouched (publishing a render that has no cards).
    2026-11-24: one filter graph over five inputs (two looped stills, two silent tracks, the video) deadlocked in
    ffmpeg 8 at the same frame every time on a 4.7-min video. Now each card is its own short clip in the video's own
    format, and the three files are joined one after another (the concat demuxer reads them in turn: no deadlock)."""
    sr, fps, ch = _probe(video)
    try:
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                    str(video)], capture_output=True, text=True).stdout.strip() or 0)
    except ValueError:
        dur = 0.0
    preset = "medium" if dur < 900 else "faster"   # a long film re-encodes in a reasonable time
    tmp = Path(tempfile.mkdtemp(prefix="cards_"))
    title_card(title, still, tmp / "intro.jpg")
    end_card(title, still, tmp / "outro.jpg")
    layout = "mono" if ch == 1 else "stereo"
    enc = ["-c:v", "libx264", "-crf", "18", "-preset", preset, "-pix_fmt", "yuv420p", "-r", str(fps),
           "-c:a", "aac", "-b:a", "192k", "-ar", str(sr), "-ac", str(ch)]
    for name, secs, fade in (("intro", INTRO_S, f"fade=t=in:st=0:d=0.6,fade=t=out:st={INTRO_S - 0.4}:d=0.4"),
                             ("outro", OUTRO_S, "fade=t=in:st=0:d=0.5")):
        _ff(["-loop", "1", "-t", str(secs), "-i", str(tmp / f"{name}.jpg"),
             "-f", "lavfi", "-t", str(secs), "-i", f"anullsrc=r={sr}:cl={layout}",
             "-vf", f"scale={W}:{H},fps={fps},format=yuv420p,{fade},setsar=1", "-shortest"] + enc
            + [str(tmp / f"{name}.mp4")], name + " card")
    _ff(["-i", str(video), "-vf", f"scale={W}:{H},fps={fps},format=yuv420p,setsar=1"] + enc + [str(tmp / "main.mp4")],
        "the video")
    (tmp / "list.txt").write_text("".join(f"file '{(tmp / n).as_posix()}'\n" for n in ("intro.mp4", "main.mp4", "outro.mp4")),
                                  encoding="utf-8")
    out = tmp / "with_cards.mp4"
    _ff(["-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"), "-c", "copy", "-movflags", "+faststart", str(out)],
        "joining")
    if not out.exists():
        raise SystemExit("cards: the joined video was not written")
    shutil.move(str(out), str(out_path or video))   # the temp folder may be on another drive
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

"""publish_meta.py - everything YouTube needs for a finished video, written by AI (owner, 2026-11-23).

The way Video Studio does it, our own code: when a video is published the owner only picks the channel; the title,
description, tags, chapters and a designed thumbnail are made here.

  make_meta(working_title, units, duration, credits, kind) -> {title, description, tags, chapters, thumb_lines,
                                                              thumb_accent, thumb_unit}
      units: [(start_s, end_s, narration)] in the video's own timeline. DeepSeek reads the narration and writes the
      metadata; the chapters it picks are checked against YouTube's rules here (>= 3, first at 0:00, >= 10 s apart,
      the last >= 10 s before the end) and dropped when they can't meet them.
  make_thumbnail(picture, lines, accent, out) -> a 1280x720 JPEG: the picture, a dark fade on the left and the
      headline in big letters over it, one line on a coloured bar.

Faceless videos (owner, 2026-11-22): no person's or owner's name, no channel name, on a thumbnail or in the text.
"""
import json
import re
import textwrap

from PIL import Image, ImageDraw, ImageFont

FONTS = ["C:/Windows/Fonts/impact.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf"]
ACCENT = (255, 204, 0)
OWNER_NAMES = ("tariq", "hasnain")

PROMPT = """You prepare a YouTube upload for a faceless narrated video. Read its narration and write the metadata.

Working title: {title}
Kind: {kind}
Length: {length}
The narration, in order, each part with its start time and index:
{units}

Rules:
- title: at most 90 characters, a strong curiosity hook that is TRUE to the video; no clickbait lies, no ALL CAPS, no emoji.
- description: 2 or 3 short paragraphs (at most 900 characters in all): what the viewer will learn and why it matters,
  in plain words, then one line inviting them to subscribe for more. You may end with 2 or 3 relevant hashtags.
  Do not list chapters or timestamps (they are added separately). Do not mention any narrator, host or person behind
  the channel, and never any channel name.
- tags: 10 to 15 search tags, each 1 to 4 words, no '#', no commas inside a tag.
- chapters: {nchap} chapters that follow the story; each is {{"index": <the part's index where it starts>,
  "title": <at most 40 characters>}}; the first must be index 0; chapters at least {gap} seconds apart.
- thumb_lines: the thumbnail headline: 2 to 4 short lines (each at most 16 characters, 2 to 3 words), together at most
  6 words, punchy and different from the title wording; no names of real private people.
- thumb_accent: the index (0-based) of the one thumb line to highlight.
- thumb_unit: the index of the narration part whose picture would make the most striking thumbnail.
Reply with ONLY a JSON object with exactly these keys: title, description, tags, chapters, thumb_lines, thumb_accent,
thumb_unit."""


def _mmss(s):
    s = int(s)
    return "%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60) if s >= 3600 else "%d:%02d" % (s // 60, s % 60)


def _clean(text):
    """No owner name, no angle brackets (YouTube refuses them)."""
    text = re.sub(r"[<>]", "", str(text or ""))
    for n in OWNER_NAMES:
        text = re.sub(r"(?i)\b%s\b" % n, "", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def check_chapters(chapters, duration):
    """YouTube's chapter rules -> a valid list or []."""
    out = []
    for c in sorted(chapters, key=lambda c: c["seconds"]):
        t = int(c["seconds"])
        title = _clean(c["title"]).replace("\n", " ")[:60]
        if not title:
            continue
        if not out:
            out.append({"seconds": 0, "title": title})
        elif t - out[-1]["seconds"] >= 10 and t <= duration - 10:
            out.append({"seconds": t, "title": title})
    return out if len(out) >= 3 else []


def make_meta(working_title, units, duration, credits="", kind="explainer", ask=None):
    if ask is None:
        from deepseek_client import ask
    budget = 14000   # characters of narration shown; long films get each part shortened evenly
    per = max(60, budget // max(1, len(units)))
    lines = []
    for i, (a, b, text) in enumerate(units):
        t = re.sub(r"\s+", " ", str(text or "")).strip()
        lines.append("[%d] %s  %s" % (i, _mmss(a), t if len(t) <= per else t[:per].rsplit(" ", 1)[0] + " ..."))
    nchap = "3 to 6" if duration < 600 else "5 to 10" if duration < 1800 else "8 to 15"
    gap = 30 if duration < 600 else 60
    prompt = PROMPT.format(title=working_title, kind=kind, length=_mmss(duration), units="\n".join(lines),
                           nchap=nchap, gap=gap)
    meta = None
    for _ in range(3):
        try:
            reply = ask(prompt, max_tokens=16000)   # the model reasons first; a small budget came back empty
            meta = json.loads(reply[reply.find("{"):reply.rfind("}") + 1])
            if meta.get("title") and meta.get("description"):
                break
        except (ValueError, TypeError):
            meta = None
    if not meta:
        raise RuntimeError("DeepSeek did not return the metadata")
    title = _clean(meta.get("title"))[:100] or _clean(working_title)[:100]
    desc = _clean(meta.get("description"))[:3500]
    if credits.strip():
        desc += "\n\n" + credits.strip()
    tags, seen = [], set()
    for t in meta.get("tags") or []:
        t = _clean(t).replace(",", " ").replace("#", "").strip()[:60]
        if t and t.casefold() not in seen and sum(len(x) + 3 for x in tags) + len(t) < 480:
            tags.append(t)
            seen.add(t.casefold())
    chapters = []
    for c in meta.get("chapters") or []:
        try:
            i = int(c.get("index"))
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(units):
            chapters.append({"seconds": int(units[i][0]) if i else 0, "title": c.get("title", "")})
    tl = [_clean(x).upper()[:18] for x in (meta.get("thumb_lines") or []) if _clean(x)][:4]
    if not tl:
        tl = [x.upper() for x in textwrap.wrap(title, 16)[:3]]
    try:
        acc = max(0, min(len(tl) - 1, int(meta.get("thumb_accent") or 0)))
    except (TypeError, ValueError):
        acc = len(tl) - 1
    try:
        unit = max(0, min(len(units) - 1, int(meta.get("thumb_unit") or 0)))
    except (TypeError, ValueError):
        unit = 0
    return dict(title=title, description=desc, tags=tags[:15], chapters=check_chapters(chapters, duration),
                thumb_lines=tl, thumb_accent=acc, thumb_unit=unit)


def _font(size):
    for f in FONTS:
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_thumbnail(picture, lines, accent, out):
    """1280x720: the picture filling the frame (its right side shows), a dark fade from the left, the headline over it."""
    W, H = 1280, 720
    try:
        im = Image.open(picture).convert("RGB")
        r = max(W / im.width, H / im.height)
        im = im.resize((int(im.width * r + 0.5), int(im.height * r + 0.5)), Image.LANCZOS)
        x0, y0 = max(0, int((im.width - W) * 0.6)), max(0, (im.height - H) // 2)
        im = im.crop((x0, y0, x0 + W, y0 + H))
    except (OSError, ValueError, TypeError):
        im = Image.new("RGB", (W, H), (14, 20, 32))
    fade = Image.new("L", (W, 1))
    for x in range(W):
        fade.putpixel((x, 0), int(255 * min(0.85, max(0.0, 1 - x / (W * 0.66)) * 0.95)))
    im = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), im, fade.resize((W, H)))
    d = ImageDraw.Draw(im)
    lines = [l for l in lines if l][:4] or ["WATCH THIS"]
    size = 124
    f = _font(size)
    while size > 50 and (max(d.textlength(l, font=f) for l in lines) > W * 0.58 or int(size * 1.1) * len(lines) > H - 110):
        size -= 4
        f = _font(size)
    lh = int(size * 1.1)
    y, x = (H - lh * len(lines)) // 2, 60
    for i, l in enumerate(lines):
        w = d.textlength(l, font=f)
        if i == accent:
            d.rectangle([x - 16, y + int(size * 0.04), x + w + 16, y + lh - int(size * 0.02)], fill=ACCENT)
            d.text((x, y), l, font=f, fill=(17, 17, 17))
        else:
            d.text((x, y), l, font=f, fill=(255, 255, 255), stroke_width=max(3, size // 22), stroke_fill=(0, 0, 0))
        y += lh
    im.save(out, quality=92)
    return out

"""visual_ladder.py - the factory's own visual ladder (modelled on Video Studio's rungs, written fresh for this
project; no Video Studio code is imported). Goal: use Google Flow as little as possible.

For each scene that does not need a recurring character, cheapest first:
  1. stock footage  - Pexels + Pixabay videos (keys: Windows Credential Manager, service "VideoFactory",
                      users "pexels_api_key" / "pixabay_api_key"; skipped when missing)       -> scene_NN.mp4 ([VIDEO])
  2. real photos    - Openverse, then Wikimedia Commons (no key; CC0 / PD / CC BY / CC BY-SA)  -> scene_NN.jpg (+ credit)
  3. cloud still    - Cloudflare Workers AI FLUX.1-schnell (account id: topic_config.json,
                      token: Credential Manager "ExplainerStudio" / "cloudflare_api_token")    -> scene_NN.jpg
  (4. Google Flow   - only scenes with recurring characters/places, Veo moments while the daily allowance lasts,
                      and anything the rungs above could not fill - in batches, see topic_video.py)
  (5. motion        - the factory build turns every still into Blender camera moves)
Every candidate is checked by the local vision model (Ollama qwen2.5vl:7b) before it is used: it must show what the
narration says and carry no readable text, logos or watermarks. A rung whose candidates all fail falls to the next.
"""
import base64
import io
import json
import re
import subprocess
import time
from pathlib import Path

import requests

# Wikimedia's User-Agent policy wants a tool name and a contact URL; without it, downloads get an error page instead
UA = {"User-Agent": "VideoFactory/1.0 (https://github.com/tariq424/video-flow; personal narrated-video tool) python-requests",
      "Api-User-Agent": "VideoFactory/1.0 (https://github.com/tariq424/video-flow)"}
OLLAMA = "http://127.0.0.1:11434"
VISION_MODEL = "qwen2.5vl:7b"
OK_LIC = {"cc0": 0, "pdm": 0, "by": 1, "by-sa": 2}
LIC_NAME = {"cc0": "CC0", "pdm": "Public domain", "by": "CC BY", "by-sa": "CC BY-SA"}
MIN_W = 1200


TRACE = []   # every rung attempt of the current scene ("photo commons: does not fit ..."), read by topic_video


def log(msg):
    print(msg, flush=True)
    if msg.startswith("    "):
        TRACE.append(msg.strip())


def _key(name):
    try:
        import keyring
        return keyring.get_password("VideoFactory", name) or ""
    except Exception:
        return ""


# ------------------------------------------------------------------ vision check
# Two steps (2026-11-22): the small local vision model is good at SAYING what a picture shows and at spotting text and
# distortion, but poor at judging fit (it rejected a molasses tank among brick buildings for "a tank looms over the
# North End"). So it only describes and flags; DeepSeek judges whether that description fits the shot.
DESCRIBE = """Look at this picture and reply with ONLY JSON:
{"thing": "max 6 words: the physical thing photographed (e.g. 'a street scene', 'a green sign with text', 'a map')",
 "text_share": "none", "little" or "lots" - how much of the picture is readable text,
 "seen": "max 25 words: what it shows - subject, setting, era/period cues (clothes, vehicles, technology), mood.
  Describe what you SEE; never repeat words you read in the picture",
 "overlay": true or false - is there text, a caption, a watermark, a logo or screen UI laid OVER the picture, or big
  readable lettering that dominates it? Small signs that belong in the scene (shop signs, street signs) do NOT count,
 "garbled": true or false - obviously melted or deformed objects, warped faces, extra limbs, impossible geometry?}"""

JUDGE = """A documentary video needs a picture for this shot.
SHOT: "{shot}"
NARRATION over it: "{narration}"
A candidate picture shows: "{seen}"
Could a viewer accept this picture as the shot? The same kind of subject and setting is enough; it need not match every
detail. Reject it only when it shows something clearly different, the wrong era (e.g. modern gear in a historical story),
or the wrong mood. Reply with ONLY JSON: {{"fits": "yes" or "no", "why": "max 12 words"}}"""


def _yes(v):
    return str(v).strip().lower() in ("true", "yes", "1")


def check(image_path, narration, shot=""):
    """-> (ok, reason). Fails closed: a picture that cannot be described or judged is not used."""
    try:
        b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
        # GPU interlock (owner, 2026-11-23): the shared GPU lock (Video Studio's) is held only for this one look, and
        # keep_alive 0 makes Ollama unload the model as soon as it answers - the VRAM is free again when the lock is.
        import interlock
        with interlock.gpu("vision check (%s)" % Path(image_path).name):
            r = requests.post(OLLAMA + "/api/chat", timeout=240, json={
                "model": VISION_MODEL, "stream": False, "keep_alive": 0,
                "messages": [{"role": "user", "content": DESCRIBE, "images": [b64]}],
                "options": {"temperature": 0.1, "num_predict": 160}})
        txt = r.json()["message"]["content"]
        j = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
    except Exception as e:
        return False, f"check unavailable ({str(e)[:60]})"
    seen = str(j.get("seen", ""))[:160]
    thing = str(j.get("thing", "")).lower()
    # 2026-11-22: a historical plaque (a green sign full of text) passed as "the Molasses Flood" because the model read
    # its words back as the description. Text-heavy pictures and signs/documents are never scene pictures.
    if str(j.get("text_share", "")).strip().lower() == "lots" or re.search(
            r"\b(sign|plaque|poster|document|page|map|newspaper|text|screenshot|diagram|chart|book|letter)\b", thing):
        return False, f"mostly text / a sign or document ({thing or seen[:60]})"
    seen = f"{thing}: {seen}" if thing else seen
    if _yes(j.get("overlay")):
        return False, f"text/watermark over the picture (shows {seen[:80]})"
    if _yes(j.get("garbled")):
        return False, f"garbled (shows {seen[:80]})"
    from deepseek_client import ask
    v, err = None, ""
    for _ in range(2):   # DeepSeek now and then returns an empty answer: ask once more
        try:
            reply = ask(JUDGE.format(shot=re.sub(r"\(Same .*?\)", "", shot or narration)[:500].replace('"', "'"),
                                     narration=narration[:400].replace('"', "'"), seen=seen.replace('"', "'")), max_tokens=2000)
            v = json.loads(reply[reply.find("{"):reply.rfind("}") + 1])
            break
        except Exception as e:
            err = str(e)[:60]
    if v is None:
        return False, f"judge unavailable ({err})"
    if not _yes(v.get("fits")):
        return False, f"does not fit: {str(v.get('why', ''))[:70]} (shows {seen[:70]})"
    return True, f"ok - {seen[:90]}"


def _frame(mp4, at, out):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", str(mp4), "-frames:v", "1", "-q:v", "3", str(out)],
                   capture_output=True)
    return Path(out).is_file()


# ------------------------------------------------------------------ rung 1: stock footage
def _pexels(q, n=8):
    k = _key("pexels_api_key")
    if not k:
        return []
    try:
        r = requests.get("https://api.pexels.com/videos/search", headers={**UA, "Authorization": k}, timeout=30,
                         params={"query": q, "orientation": "landscape", "per_page": n, "size": "medium"})
        out = []
        for v in r.json().get("videos", []):
            files = sorted([f for f in v.get("video_files", []) if (f.get("width") or 0) >= 1280 and f.get("file_type") == "video/mp4"],
                           key=lambda f: abs((f.get("width") or 0) - 1920))
            if files and 4 <= (v.get("duration") or 0) <= 60:
                slug = re.sub(r"[-\d]+$", "", (v.get("url") or "").rstrip("/").split("/")[-1]).replace("-", " ")
                out.append({"src": "pexels", "url": files[0]["link"], "desc": slug, "dur": v["duration"],
                            "credit": f"Video: {v.get('user', {}).get('name', 'Pexels')} on Pexels ({v.get('url')})"})
        return out
    except Exception as e:
        log(f"    pexels: {str(e)[:80]}")
        return []


def _pixabay(q, n=8):
    k = _key("pixabay_api_key")
    if not k:
        return []
    try:
        r = requests.get("https://pixabay.com/api/videos/", headers=UA, timeout=30,
                         params={"key": k, "q": q[:100], "per_page": max(3, n), "safesearch": "true"})
        out = []
        for v in r.json().get("hits", []):
            f = (v.get("videos") or {}).get("large") or (v.get("videos") or {}).get("medium") or {}
            if f.get("url") and (f.get("width") or 0) >= 1280 and 4 <= (v.get("duration") or 0) <= 60:
                out.append({"src": "pixabay", "url": f["url"], "desc": v.get("tags", ""), "dur": v["duration"],
                            "credit": f"Video: {v.get('user', 'Pixabay')} on Pixabay ({v.get('pageURL')})"})
        return out
    except Exception as e:
        log(f"    pixabay: {str(e)[:80]}")
        return []


def _rank(cands, narration, ask):
    """DeepSeek orders the candidates by how well their description fits the narration (cheap, no download)."""
    if len(cands) <= 1:
        return cands
    listing = "\n".join(f"{i}: {c['desc'][:120]}" for i, c in enumerate(cands))
    try:
        reply = ask(f"Narration: \"{narration}\"\n\nStock items (index: description):\n{listing}\n\n"
                    "Which items could illustrate this narration? Answer with ONLY a JSON list of indexes, best first, "
                    "leaving out items that do not fit. [] if none fit.", max_tokens=200)
        idx = json.loads(reply[reply.find("["):reply.rfind("]") + 1])
        return [cands[i] for i in idx if isinstance(i, int) and 0 <= i < len(cands)]
    except Exception:
        return cands


def stock_video(scene, out_mp4, ask, tmp):
    q = scene.get("stock") or ""
    if not q or not (_key("pexels_api_key") or _key("pixabay_api_key")):
        return None
    cands = _rank(_pexels(q) + _pixabay(q), scene["narration"], ask)[:3]
    for c in cands:
        try:
            data = requests.get(c["url"], headers=UA, timeout=180).content
            if len(data) < 100000:
                continue
            Path(out_mp4).write_bytes(data)
            fr = Path(tmp) / "stock_check.jpg"
            if not _frame(out_mp4, min(2.0, c["dur"] / 2), fr):
                continue
            ok, why = check(fr, scene["narration"], scene.get("visual", ""))
            log(f"    stock {c['src']}: {why}")
            if ok:
                return c
        except Exception as e:
            log(f"    stock {c['src']}: {str(e)[:80]}")
    Path(out_mp4).unlink(missing_ok=True)
    return None


# ------------------------------------------------------------------ rung 2: real photos
def _openverse(q, n=12):
    try:
        r = requests.get("https://api.openverse.org/v1/images/", headers=UA, timeout=30,
                         params={"q": q, "license": "cc0,pdm,by,by-sa", "page_size": n, "mature": "false"})
        if r.status_code != 200:
            return []
        out = []
        for x in r.json().get("results", []):
            if (x.get("width") or 0) >= MIN_W and x.get("license") in OK_LIC and (x.get("width") or 0) >= (x.get("height") or 1):
                out.append({"src": "openverse", "url": x["url"], "desc": x.get("title") or "", "lic": x["license"],
                            "credit": f"Photo: {x.get('title') or 'untitled'} by {x.get('creator') or 'unknown'}, "
                                      f"{LIC_NAME[x['license']]} {x.get('license_version') or ''} ({x.get('foreign_landing_url') or x['url']})"})
        return sorted(out, key=lambda c: OK_LIC[c["lic"]])
    except Exception:
        return []


def _commons(q, n=12):
    try:
        r = requests.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=30, params={
            "action": "query", "format": "json", "generator": "search", "gsrsearch": f"filetype:bitmap {q}", "gsrnamespace": 6,
            "gsrlimit": n, "prop": "imageinfo", "iiprop": "url|size|extmetadata", "iiurlwidth": 1920})
        out = []
        for pg in (r.json().get("query", {}).get("pages", {}) or {}).values():
            ii = (pg.get("imageinfo") or [{}])[0]
            md = ii.get("extmetadata") or {}
            lic = (md.get("LicenseShortName", {}).get("value") or "").lower()
            key = "cc0" if "cc0" in lic else "pdm" if "public domain" in lic else "by-sa" if "by-sa" in lic else "by" if "cc by" in lic else None
            if key and (ii.get("width") or 0) >= MIN_W and (ii.get("width") or 0) >= (ii.get("height") or 1):
                artist = re.sub(r"<[^>]+>", "", md.get("Artist", {}).get("value") or "unknown")[:60]
                out.append({"src": "commons", "url": ii.get("thumburl") or ii["url"], "desc": pg.get("title", ""), "lic": key,
                            "credit": f"Photo: {pg.get('title', '').replace('File:', '')} by {artist}, {md.get('LicenseShortName', {}).get('value')} "
                                      f"({ii.get('descriptionurl')})"})
        return out
    except Exception:
        return []


def _to_jpg(data, out):
    from PIL import Image
    im = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = im.size
    if w / h > 16 / 9:
        nw = int(h * 16 / 9)
        im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = int(w * 9 / 16)
        im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    im.resize((1920, 1080), Image.LANCZOS).save(out, "JPEG", quality=92)


def real_photo(scene, out_jpg, ask):
    q = scene.get("photo") or ""
    if not q:
        return None
    cands = _openverse(q) or []
    if len(cands) < 3:
        cands += _commons(q)
    for c in _rank(cands, scene["narration"], ask)[:3]:
        try:
            r = requests.get(c["url"], headers=UA, timeout=60)
            if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
                log(f"    photo {c['src']}: download refused (HTTP {r.status_code}, {r.headers.get('content-type', '?')[:30]})")
                continue
            _to_jpg(r.content, out_jpg)
            ok, why = check(out_jpg, scene["narration"], scene.get("visual", ""))
            log(f"    photo {c['src']}: {why}")
            if ok:
                return c
        except Exception as e:
            log(f"    photo {c['src']}: {str(e)[:80]}")
    Path(out_jpg).unlink(missing_ok=True)
    return None


# ------------------------------------------------------------------ rung 3: Cloudflare still
CF_EXHAUSTED = False   # set once Cloudflare says today's free allowance is used: no more calls this run


def cloudflare_still(prompt, out_jpg, account_id):
    global CF_EXHAUSTED
    if CF_EXHAUSTED:
        raise RuntimeError("Cloudflare's free daily allowance is used up (skipped)")
    import keyring
    tok = keyring.get_password("ExplainerStudio", "cloudflare_api_token")
    if not (account_id and tok):
        raise RuntimeError("Cloudflare account id (topic_config.json) or API token (Credential Manager) missing")
    p = ("Photorealistic, cinematic wide 16:9 landscape image. " + re.sub(r"\(Same .*?\)", "", prompt).strip()
         + " No text, letters, logos or watermarks.")
    r = requests.post(f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/black-forest-labs/flux-1-schnell",
                      headers={"Authorization": f"Bearer {tok}"}, json={"prompt": p[:2000], "steps": 8}, timeout=90)
    if r.status_code == 429:
        CF_EXHAUSTED = True
        raise RuntimeError("Cloudflare's free daily allowance is used up - skipping it for the rest of this run")
    if r.status_code != 200:
        raise RuntimeError(f"Cloudflare HTTP {r.status_code}: {r.text[:120]}")
    _to_jpg(base64.b64decode(r.json()["result"]["image"]), out_jpg)


def cloud_still(scene, out_jpg, account_id, tries=2):
    for k in range(tries):
        try:
            cloudflare_still(scene["visual"], out_jpg, account_id)
        except Exception as e:
            log(f"    cloudflare: {str(e)[:100]}")
            return False
        ok, why = check(out_jpg, scene["narration"], scene.get("visual", ""))
        log(f"    cloudflare (try {k + 1}): {why}")
        if ok:
            return True
    Path(out_jpg).unlink(missing_ok=True)
    return False

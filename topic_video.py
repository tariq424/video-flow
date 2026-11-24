"""topic_video.py - make a narrated video from a topic (the factory's "New video from a topic" page).

    python topic_video.py plan  <topic.txt> <minutes> [--no-video] [--target DIR]
        research (DeepSeek web search) -> scenes sized to the length (+ reference sheets for anything that recurs)
        -> the factory script (flow_prompts.txt, Section 2 template), topic_plan.json, storyboard manifest, narration.
    python topic_video.py visuals [--target DIR]
        Every scene's picture, using Google Flow as little as possible (owner, 2026-11-22):
          - scenes WITHOUT a recurring character/place go down the factory's own ladder (visual_ladder.py):
            stock footage -> real CC photos -> Cloudflare FLUX still, each checked by the local vision model;
          - only scenes WITH a recurring character/place, Veo moments that no stock footage covered (while today's
            allowance lasts), and anything the ladder could not fill go to Flow - in BATCHES on our own Flow profile:
            ONE Agent message with the reference sheets, then the scenes in as few messages as possible. Never
            prompt by prompt. Flow's items are mapped to scenes by the "S03" title Flow is asked to give them, else
            DeepSeek matches Flow's own titles to the prompts;
          - whatever is still missing gets an unchecked Cloudflare still, so the video never stalls.
        Writes credits.txt (photo/footage credits for the YouTube description).

Veo clips: only while today's allowance lasts (veo_ledger.json; topic_config.json "veo_daily"). The build afterwards
is the factory's own (build_video.py --parallax): stills become Blender camera moves, clips play as they are.
"""
import datetime
import json
import shutil
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import flow_automation as fa      # noqa: E402
import flow_generate as fg        # noqa: E402
import visual_ladder as vl        # noqa: E402

DEFAULT_TARGET = Path(os.environ.get("FLOW_TARGET_DIR") or r"F:\videos\google-flow-videos")
DEFAULTS = {"veo_daily": 3, "words_per_minute": 150, "scene_words": [20, 40], "scenes_per_message": 20,
            "max_references": 6, "wait_minutes_per_message": 25, "cloudflare_account_id": ""}
REF_PREFIX = "REF "


def log(msg):
    print(msg, flush=True)


def cfg(target):
    c = dict(DEFAULTS)
    try:
        c.update(json.loads((Path(target) / "topic_config.json").read_text(encoding="utf-8")))
    except Exception:
        pass
    return c


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------ Veo ledger
def _ledger_path(target):
    return Path(target) / "veo_ledger.json"


def veo_today(target):
    """(used today, daily allowance)."""
    try:
        led = json.loads(_ledger_path(target).read_text(encoding="utf-8"))
    except Exception:
        led = {}
    return int(led.get(datetime.date.today().isoformat(), 0)), int(cfg(target)["veo_daily"])


def veo_add(target, n):
    if n <= 0:
        return
    p = _ledger_path(target)
    try:
        led = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        led = {}
    day = datetime.date.today().isoformat()
    led[day] = int(led.get(day, 0)) + n
    p.write_text(json.dumps(led, indent=1), encoding="utf-8")


# ------------------------------------------------------------------ DeepSeek
def ask(prompt, system=None, search=False, max_tokens=4000):
    from deepseek_client import ask as _ask
    return _ask(prompt, system=system, search=search, max_tokens=max_tokens)


def _json_of(text):
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1)
    a, b = t.find("{"), t.rfind("}")
    if a == -1 or b == -1:
        raise ValueError("no JSON object in the reply")
    return json.loads(t[a:b + 1])


PLAN_RULES = """You write narrated, photorealistic documentary-style videos. Each scene is shown with ONE visual: real stock
footage, a real photo, or an AI image. Plan ONE video and answer with ONE JSON object only, no prose:

{
 "title": "short title",
 "references": [ {"name": "Short Name", "kind": "character|place|object",
                  "sheet": "a precise visual description used to draw ONE reference image of it"} ],
 "scenes": [ {"visual": "ONE shot, described for an image model", "narration": "the voice-over for this shot",
              "refs": ["Short Name"], "stock": "3-6 word stock-footage search", "photo": "real-photo search or empty",
              "people": ["Full Name"], "motion": 0} ]
}

Rules:
- references: ONLY invented characters (or a specific invented place/object) that appear in more than one scene and
  must look the same each time - at most {max_refs}; [] when the story has none (most documentaries need none). A
  character sheet: one person, full figure, clear face, plain neutral studio background, natural light; give age,
  build, hair, skin, clothing. Never a real, identifiable private person.
- scenes: {n_scenes} scenes (allowed {n_min}-{n_max}). Each narration is {w_lo}-{w_hi} words; all narration together is
  about {words} words (that is the video's length). Spoken English, vivid, accurate, no headings or lists; one story
  from a strong opening hook to a satisfying end.
- visual: ONE shot, 25-60 words. START with the named place or subject when there is one ("Vaduz Castle,
  Liechtenstein, ..."), then the concrete things in frame, light, camera angle and mood; END with "16:9,
  photorealistic". No text, captions, logos or signs with writing. If the shot shows a reference, use its exact name
  and list it in "refs".
- narration style (the owner's house style): write every number in words as it is spoken ("three point five per
  million", "forty-five billionaires", "nineteen nineteen"), so the voice reads it naturally; open with a hook that
  makes the viewer want the rest (a surprise, a flip of expectations); for a ranking count DOWN ("Number ten, ..." to
  "And number one, ..."); name the main data source once in the narration ("using Forbes 2025 data"); end with a
  clear takeaway ("The lesson is clear: ...").
- stock: a short generic search that stock-footage sites (Pexels, Pixabay) would match for this shot, e.g. "harbor
  fog boats dawn", "old factory smokestacks". Empty when the shot needs a reference character.
- photo: when the shot shows a REAL specific subject that photo archives hold (a named building, ship, event, city,
  historic photo), a search for it, e.g. "Boston North End 1919". Otherwise "".
- Faceless ("headless") video: no host and no presenter. Never write "I", "welcome to my channel", "in this video",
  a channel name or the owner's name; the narration speaks about the subject only.
- Keep pictures safe for an image model: no gore, no weapons aimed at people, never children together with violence.
- people: the REAL, named people the shot itself shows (e.g. ["James Earl Jones"]); [] when it shows nobody real.
  Real public photos or footage of them are used when they exist; otherwise they are drawn as an artist's rendition.
  Put their name in "photo" as well (e.g. "James Earl Jones 1987").
- Never make a copyrighted character or a film/TV still the picture (Darth Vader, a cartoon lion, a superhero): show
  a symbolic real-world scene instead (a recording booth, a real lion at dawn).
- motion: 0-3, how much the moment needs real movement (3 = it only works as moving video). Most scenes are 0 or 1.
- Facts must be correct. Use the research notes; do not invent statistics, dates or quotes."""


def research(topic):
    log("research: DeepSeek web search ...")
    t0 = time.time()
    q = ("Research this video topic and write compact, factual notes a scriptwriter can rely on: key facts, dates,"
         " numbers, names, places, what things looked like, and 3-6 surprising details. Cite the main sources (URLs)"
         " at the end. Topic:\n\n" + topic)
    notes = ""
    for _ in range(2):   # the search model spends much of its budget searching and reading pages before it writes
        notes = ask(q, search=True, max_tokens=12000)
        if len(notes.split()) >= 80:
            break
        log("research: the notes came back empty - asking again")
    log(f"research: {len(notes.split())} words of notes in {time.time() - t0:.0f} s")
    return notes


def plan(topic, minutes, target):
    c = cfg(target)
    words = int(round(float(minutes) * c["words_per_minute"]))
    w_lo, w_hi = c["scene_words"]
    n_scenes = max(3, round(words / ((w_lo + w_hi) / 2)))
    notes = research(topic)
    rules = (PLAN_RULES.replace("{max_refs}", str(c["max_references"])).replace("{n_scenes}", str(n_scenes))
             .replace("{n_min}", str(max(3, int(n_scenes * 0.85)))).replace("{n_max}", str(int(n_scenes * 1.15) + 1))
             .replace("{w_lo}", str(w_lo)).replace("{w_hi}", str(w_hi)).replace("{words}", str(words)))
    ask_text = f"TOPIC (from the owner):\n{topic}\n\nRESEARCH NOTES:\n{notes}\n\nTarget length: {minutes} minutes."
    last = None
    for attempt in range(2):
        log(f"plan: writing {n_scenes} scenes (~{words} narration words) ..." + (" (retry)" if attempt else ""))
        t0 = time.time()
        reply = ask(ask_text if not last else ask_text + f"\n\nYour previous answer was not usable ({last}). "
                    "Answer with the JSON object only.", system=rules, max_tokens=16000)
        try:
            p = _json_of(reply)
            scenes = [s for s in p.get("scenes", []) if str(s.get("visual", "")).strip() and str(s.get("narration", "")).strip()]
            if len(scenes) < 3:
                raise ValueError(f"only {len(scenes)} usable scene(s)")
            p["scenes"] = scenes
            log(f"plan: {len(scenes)} scenes, {len(p.get('references') or [])} reference sheet(s) in {time.time() - t0:.0f} s")
            p["notes"], p["minutes"], p["topic"] = notes, float(minutes), topic
            return p
        except Exception as e:
            last = str(e)[:160]
            log(f"plan: reply not usable - {last}")
    raise SystemExit("plan failed: DeepSeek did not return a usable plan twice")


# ------------------------------------------------------------------ hook, repeats, proofreading
# Modelled on Video Studio's writer (its rules, our own code): the opening sentence must be a real HOOK, it must not say
# what a later line says (it teases, never repeats), and the narration is proofread before the voice records it.
HOOK_CHECK = """The first sentence of a video:
"{first}"
Video title: "{title}"
Is that first sentence a HOOK - a surprising claim, a striking number, a sharp question or high stakes that makes a viewer
need to keep watching? It is NOT a hook if it is a credit or attribution ("X says", "using X data" first), a recap, a
greeting, an introduction of the host or channel, plain framing ("Today we look at ..."), or simply the first item of a
list ("Number ten, ...").
If it is not, write ONE hook sentence of 8-25 words to go BEFORE it, using only facts from the narration below (no number
or claim that is not in it), numbers written in words as spoken, never naming the channel owner or any host.
Narration (facts only):
{body}
Reply with JSON: {{"is_hook": true or false, "why": "a few words", "hook": "... or empty"}}"""

FRESH_HOOK = """This is the first sentence of a video, its cold-open hook:
"{hook}"
It repeats this line that the narration says later:
"{later}"
Rewrite ONLY the hook: one sentence of 8-25 words that makes the viewer need to keep watching - the stakes, a sharp
question or a surprising consequence - drawn from the video's subject ("{title}") but worded so it repeats NO later
sentence. No recap, no "In this video", no host introduction, no number or claim that is not in the narration below.
Numbers in words as spoken.
Narration (facts only):
{body}
Reply with JSON: {{"hook": "..."}}"""

PROOF = """Proofread this video narration before it is recorded. Find ONLY: misspellings, two words run together, a word split
in two, doubled words ("the the"), wrong homophones (its/it's, their/there), and broken punctuation that would make a
narrator read it wrongly. Do NOT reword, shorten, restyle or fact-check anything.
For each problem give the exact wrong text as it appears (a few words, copied exactly) and the corrected text.
Narration, one numbered part per line:
{parts}
Reply with JSON: {{"fixes": [{{"part": 1, "from": "exact wrong text", "to": "corrected text"}}]}} (empty list when clean)"""

_STOP = {"the", "a", "an", "to", "of", "and", "or", "in", "on", "with", "for", "is", "are", "be", "could", "would", "that",
         "this", "it", "its", "by", "as", "at", "from", "first", "every", "into", "was", "were", "has", "had"}


def _sentences(t):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", str(t).strip()) if s.strip()]


def _content(s):
    return {w for w in re.findall(r"[a-z0-9']+", s.lower()) if w not in _STOP and len(w) > 2}


def _nw(s):
    return len(str(s).split())


def _hook_repeat(scenes):
    """-> (hook, the later sentence it repeats) or None (half or more of the hook's content words shared)."""
    ss = _sentences(scenes[0]["narration"])
    if not ss:
        return None
    hook, hw = ss[0], _content(ss[0])
    if len(hw) < 3:
        return None
    later = ss[1:] + [x for s in scenes[1:] for x in _sentences(s["narration"])]
    best = max(later, key=lambda x: len(hw & _content(x)) / len(hw), default=None)
    return (hook, best) if best and len(hw & _content(best)) / len(hw) >= 0.5 else None


_NUMW = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
         "seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million "
         "billion trillion half quarter").split()


def _numbers(s):
    """The numbers a sentence states - digits and spelled-out number words."""
    t = s.lower()
    return set(re.findall(r"\d[\d.,]*", t)) | {w for w in re.findall(r"[a-z]+", t) if w in _NUMW}


def _bad_numbers(hook, rest):
    """Numbers in the hook that the rest of the narration never states (a hook may only use the video's own facts)."""
    return _numbers(hook) - _numbers(rest)


def polish(p):
    """Hook check -> fresh hook if it repeats a later line -> proofread. Each step is skipped (logged) if it fails."""
    scenes, title = p["scenes"], str(p.get("title") or "")
    body = " ".join(s["narration"] for s in scenes)[:12000].replace('"', "'")
    ss0 = _sentences(scenes[0]["narration"])
    first = (ss0 or [""])[0]
    rest_text = " ".join(ss0[1:] + [s["narration"] for s in scenes[1:]])
    # 2026-11-22: the planner's own opener said "Two meters separate fifth place from history" while the narration says
    # zero point nine metres - an opener with a number the narration never states is dropped and replaced
    bad = _bad_numbers(first, rest_text)
    if bad and len(ss0) > 1:
        scenes[0]["narration"] = " ".join(ss0[1:])
        log(f"opening line dropped - it states numbers the narration never does ({', '.join(sorted(bad))}): {first}")
        first = ss0[1]
    try:
        j = _json_of(ask(HOOK_CHECK.format(first=first.replace('"', "'"), title=title.replace('"', "'"), body=body),
                         system="You judge and write cold-open hooks for videos. Reply with JSON only.", max_tokens=3000))
        h = str(j.get("hook") or "").strip()
        if j.get("is_hook") is True:
            log(f"hook check: the opening is a hook ({str(j.get('why') or '')[:60]})")
        elif 6 <= _nw(h) <= 30 and not re.search(r"tariq|hasnain", h, re.I) and not _bad_numbers(h, body):
            scenes[0]["narration"] = h + " " + scenes[0]["narration"]
            log(f"hook added ({str(j.get('why') or '')[:50]}): {h}")
        elif h:
            log(f"hook proposal refused (numbers not in the narration: {sorted(_bad_numbers(h, body))}): {h}")
    except Exception as e:
        log(f"hook check skipped ({str(e)[:100]})")
    rep = _hook_repeat(scenes)
    if rep:
        try:
            j = _json_of(ask(FRESH_HOOK.format(hook=rep[0].replace('"', "'"), later=rep[1].replace('"', "'"),
                                               title=title.replace('"', "'"), body=body),
                             system="You write cold-open hooks for video essays. Reply with JSON only.", max_tokens=3000))
            h = str(j.get("hook") or "").strip()
            rest = _sentences(scenes[0]["narration"])[1:]
            if 6 <= _nw(h) <= 30 and not _hook_repeat([dict(scenes[0], narration=" ".join([h] + rest))] + scenes[1:]):
                scenes[0]["narration"] = " ".join([h] + rest)
                log(f"hook repeated a later line - rewritten: {h}")
            else:
                log("hook repeats a later line and no fresh one passed - kept")
        except Exception as e:
            log(f"fresh hook skipped ({str(e)[:100]})")
    try:
        import difflib
        parts = "\n".join(f"{i + 1}. {s['narration']}" for i, s in enumerate(scenes))
        j = _json_of(ask(PROOF.format(parts=parts[:60000]), system="You are a meticulous proofreader. Reply with JSON only.",
                         max_tokens=6000))
        done = []
        for f in j.get("fixes") or []:
            try:
                i, a, b = int(f.get("part")) - 1, str(f.get("from") or ""), str(f.get("to") or "")
            except (TypeError, ValueError):
                continue
            n = scenes[i]["narration"] if 0 <= i < len(scenes) else ""
            if not a or a == b or n.count(a) != 1 or len(a) > 60 or abs(len(a) - len(b)) > 12 \
                    or difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() < 0.6:
                continue   # only small exact fixes; anything that rewords is refused
            scenes[i]["narration"] = n.replace(a, b, 1)
            done.append(f"{a} -> {b}")
        log(f"proofread: {len(done)} fix(es)" + (": " + "; ".join(done)[:300] if done else " (clean)"))
    except Exception as e:
        log(f"proofread skipped ({str(e)[:100]})")
    return p


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "topic"


def _refs_of(p, i):
    names = {r["name"] for r in p.get("references") or []}
    return [n for n in (p["scenes"][i].get("refs") or []) if n in names]


_PHOTO_WORDS = re.compile(r"(?i)\b(a |an )?(real )?(archival |faded |vintage )?(photograph|photo|film still|still)( of)?\b|"
                          r",?\s*16:9,?\s*photorealistic\.?|photorealistic,?|sepia tone,?")


def apply_people(p):
    """owner, 2026-11-23: a scene showing a REAL, named person keeps its own shot description for judging real photos and
    footage ("shot"); its picture prompt becomes an artist's rendition (courtroom-sketch style) of that person's look,
    read from a public photo of them - used only when no real photo or footage fits."""
    for sc in p.get("scenes") or []:
        people = [str(x).strip() for x in sc.get("people") or [] if str(x).strip()]
        if not people or "courtroom-sketch" in str(sc.get("visual")):
            continue
        sc["shot"] = sc["visual"]
        scene = _PHOTO_WORDS.sub(" ", str(sc["visual"]))
        for name in people:
            scene = re.sub(re.escape(name) + r"('s)?", "the person", scene)
        sc["visual"] = vl.sketch_visual(people, " ".join(scene.split()).strip(" ,.") + ".")
    return p


def script_from_plan(p, veo_slots):
    """The factory script (Section 2 template). The top `veo_slots` scenes by motion (>= 2) get [VIDEO]."""
    ranked = sorted(range(len(p["scenes"])), key=lambda i: -int(p["scenes"][i].get("motion") or 0))
    video = {i for i in ranked[:veo_slots] if int(p["scenes"][i].get("motion") or 0) >= 2}
    paras = []
    for i, s in enumerate(p["scenes"]):
        visual = " ".join(str(s["visual"]).split())
        used = _refs_of(p, i)
        if used:
            visual += " (Same " + ", ".join(f"{n} as the reference image '{REF_PREFIX}{n}'" for n in used) + ".)"
        if "courtroom-sketch" in visual:
            visual += " 16:9."   # an artist's rendition stays a drawing
        elif "16:9" not in visual:
            visual += " Cinematic, photorealistic, 16:9."
        tag = "[VIDEO] " if i in video else ""
        paras.append(f"{tag}{visual}\nNARRATION: {' '.join(str(s['narration']).split())}")
    return "\n\n".join(paras) + "\n", sorted(video)


def write_storyboard(text, target):
    """The files the factory's import writes too: the manifest the build reads and the TTS lines."""
    items = fg.parse_prompts_text(text)
    rows = fg.scene_plan(items)
    scenes = [{"n": s["n"], "prompt_hash": s["hash"], "narration": s["narration"], "mode": s["mode"],
               "extras": [{"asset": e["asset"], "prompt_hash": e["hash"]} for e in s.get("extras") or []]} for s in rows]
    t = Path(target)
    (t / "storyboard_manifest.json").write_text(json.dumps({"scene_count": len(scenes), "scenes": scenes, "ts": now()}),
                                                encoding="utf-8")
    (t / "narration_script.txt").write_text("\n".join(fg.tts_lines(items)), encoding="utf-8")
    return rows


def cmd_plan(topic_file, minutes, target, allow_video=True):
    topic = Path(topic_file).read_text(encoding="utf-8").strip()
    if not topic:
        raise SystemExit("empty topic")
    used, daily = veo_today(target)
    slots = max(0, daily - used) if allow_video else 0
    log(f"Veo clips today: {used} of {daily} used - this video may use up to {slots}")
    p = apply_people(polish(plan(topic, minutes, target)))
    text, video = script_from_plan(p, slots)
    p["veo_scenes"] = [i + 1 for i in video]
    t = Path(target)
    slug = _slug(p.get("title") or topic)
    (t / f"episode_{slug}.txt").write_text(text, encoding="utf-8")
    (t / "flow_prompts.txt").write_text(text, encoding="utf-8")
    (t / "topic_plan.json").write_text(json.dumps(p, indent=1, ensure_ascii=False), encoding="utf-8")
    rows = write_storyboard(text, t)
    words = sum(len(s["narration"].split()) for s in rows)
    log(f"script: {len(rows)} scenes, {words} narration words (~{words / cfg(t)['words_per_minute']:.1f} min), "
        f"{len(video)} Veo candidate(s), {len(p.get('references') or [])} reference sheet(s) -> episode_{slug}.txt")
    for r in p.get("references") or []:
        log(f"  reference: {r['name']} ({r.get('kind', '?')})")
    log("__PLAN_DONE__")


# ------------------------------------------------------------------ script edits
def _set_video_tag(target, n, on):
    t = Path(target)
    paras = re.split(r"\n\s*\n", (t / "flow_prompts.txt").read_text(encoding="utf-8").strip())
    if 0 < n <= len(paras):
        para = re.sub(r"\[VIDEO\]\s*", "", paras[n - 1], count=1)
        paras[n - 1] = ("[VIDEO] " + para) if on else para
    text = "\n\n".join(paras) + "\n"
    (t / "flow_prompts.txt").write_text(text, encoding="utf-8")
    return text


def _rows(target):
    text = (Path(target) / "flow_prompts.txt").read_text(encoding="utf-8")
    return text, fg.scene_plan(fg.parse_prompts_text(text))


# ------------------------------------------------------------------ Flow batches
REF_PREAMBLE = """Create reference images for a film. Generate exactly ONE separate image for each reference below, in order.
- Each is a clean reference used later to keep that character, place or object looking the same in every scene.
- Characters: one person, full figure, clear face, plain neutral studio background, natural soft light.
- Photorealistic, 16:9 landscape. No text, captions, letters, logos or watermarks. No collages, grids or panels.
- Use Nano Banana for every image. Save each as a separate project asset.
- Title each image with its reference title exactly as given (for example "REF Anna").
- When done, list each reference with done or failed."""

SCENE_PREAMBLE = """Generate exactly ONE separate image for each scene below, in order.
- This project may contain reference images titled "REF <name>". Whenever a scene says "the reference image
  'REF <name>'", use that image as an ingredient so the person, place or object looks the same.
- Each scene is an independent 16:9, photorealistic, cinematic shot - except a scene that asks for a courtroom-sketch
  illustration: draw that one exactly as described, as a hand-drawn sketch. NARRATION lines are context only, never text
  in the picture. No text, captions, letters, logos or watermarks. No collages.
- Use Nano Banana for every still image. Save each as a separate project asset.
- Title each generated item with its scene label first, exactly as given, e.g. "S03 harbour at dawn".
- When done, list each scene with done or failed."""

VIDEO_NOTE = """- Exception: scenes labelled (VIDEO CLIP) - for each of those use Veo to generate ONE 5-second 16:9 video clip
  instead of an image (cinematic, no text). Approve those clips."""


def _scene_lines(chunk):
    out = []
    for s in chunk:
        label = f"Scene {s['n']} (VIDEO CLIP)" if s["mode"] == "video" else f"Scene {s['n']}"
        line = f"{label}, title \"S{s['n']:02d}\": {s['prompt']}"
        if s.get("narration"):
            line += f"\nNARRATION: {s['narration']}"
        out.append(line)
        for e in s.get("extras") or []:
            out.append(f"Scene {s['n']}{e['suffix']} (STILL, same scene as {s['n']}), title \"S{s['n']:02d}{e['suffix']}\": "
                       f"{e['prompt']}")
    return "\n\n".join(out)


def _new_project(page):
    fa.goto(page, fa.FLOW_HOME)
    page.wait_for_timeout(6000)
    if "/about" in page.url:
        b = page.get_by_role("button", name=re.compile("create with google flow", re.I)).first
        if b.count():
            b.click()
            page.wait_for_timeout(6000)
    if not fa.run_gate(page):
        raise fa.Blocker("Flow sign-in not confirmed - sign in on the Flow profile window and run again")
    fa.close_news(page)
    np_ = page.get_by_role("button", name=re.compile("new project", re.I)).first
    if not np_.count():
        raise fa.Blocker("no 'New project' button on Flow's home page")
    np_.click()
    page.wait_for_timeout(9000)
    if "/project/" not in page.url:
        raise fa.Blocker(f"New project did not open a project (at {page.url})")
    fa.close_news(page)
    return page.url


def _agent_box(page):
    tog = page.get_by_role("button", name=re.compile(r"^flow agent$", re.I)).first
    try:
        if tog.count() and tog.get_attribute("aria-pressed") != "true":
            tog.click()
            page.wait_for_timeout(3000)
    except Exception:
        pass
    boxes = page.locator("textarea, [contenteditable='true'], [role='textbox']").filter(visible=True)
    best, best_x = None, -1
    for i in range(boxes.count()):
        b = boxes.nth(i)
        try:
            if re.search("describe your goal", (b.get_attribute("aria-label") or ""), re.I):
                return b
            x = b.bounding_box()["x"]
            if x > best_x:
                best, best_x = b, x
        except Exception:
            continue
    if best is None:
        raise fa.Blocker("Flow's Agent message box was not found")
    return best


def _send(page, text):
    box = _agent_box(page)
    box.click()
    page.keyboard.insert_text(text)          # one paste-like insert, not keystroke by keystroke
    page.wait_for_timeout(1200)
    page.keyboard.press("Enter")
    page.wait_for_timeout(3000)
    for _ in range(6):
        if len((fa._composer_text(box) or "").strip()) < 40:
            return
        for sel in fa.SEND_BUTTON_SELECTORS:
            try:
                b = page.locator(sel).last
                if b.is_visible(timeout=600) and b.is_enabled():
                    b.click(timeout=4000)
                    break
            except Exception:
                continue
        page.wait_for_timeout(2500)
    if len((fa._composer_text(box) or "").strip()) >= 40:
        raise fa.Blocker("the batch was typed into Flow but not sent")


def _tiles(page):
    """Every media tile in the project: [{key, label, video}]. key = the tile's own image src (stable)."""
    return page.evaluate("""() => [...document.querySelectorAll('flow-grid-tile-container')].map(t => {
        const im = t.querySelector('img'); const v = t.querySelector('video');
        return {key: im ? (im.currentSrc || im.src) : (v ? (v.currentSrc || v.src || v.poster) : ''),
                label: t.getAttribute('aria-label') || '',
                video: !!v || (im && /video/i.test(im.alt || ''))};
    }).filter(x => x.key)""")


def _approve_once(page, allowed):
    """A credit quote for the batch's Veo clips: plain 'Approve' once (never 'Always approve')."""
    if not allowed:
        return False
    for b in page.get_by_role("button", name=re.compile(r"^approve$", re.I)).all():
        try:
            if b.is_visible():
                b.click()
                log("  Flow asked to approve the Veo clips - approved once")
                return True
        except Exception:
            continue
    return False


def _wait_for(page, before_keys, expected, minutes, sent_text, video_ok):
    """Poll until `expected` new tiles have media and the count holds steady, or time runs out."""
    t0, last, steady, approved = time.time(), -1, 0, False
    while time.time() - t0 < minutes * 60:
        page.wait_for_timeout(20000)
        fa._session_refusal(page, sent_text)            # raises AutomationFlagged on a rate/unusual-activity notice
        if not approved:
            approved = _approve_once(page, video_ok)
        new = [t for t in _tiles(page) if t["key"] not in before_keys]
        steady = steady + 1 if len(new) == last else 0
        last = len(new)
        log(f"  {time.time() - t0:4.0f} s: {len(new)} of {expected} new item(s)")
        if len(new) >= expected and steady >= 1:
            return new
        if len(new) and steady >= 6 and time.time() - t0 > 600:   # nothing new for 2 min after 10 min: take what came
            log("  no new items for 2 minutes - continuing with what Flow made")
            return new
    return [t for t in _tiles(page) if t["key"] not in before_keys]


def _video_src(page, key):
    """A video tile shows only a thumbnail; its clip URL appears after hovering it."""
    tile = page.locator("flow-grid-tile-container").filter(has=page.locator(f"img[src='{key}']")).first
    try:
        tile.hover()
        page.wait_for_timeout(1500)
        return tile.evaluate("t => { const v = t.querySelector('video'); return v ? (v.currentSrc || v.src) : '' }") or None
    except Exception:
        return None


def _match(rows, tiles):
    """rows: todo items [{asset, n, extra, mode, prompt}]; tiles: new tiles -> {asset: tile}.
    First the 'S03' / 'S03b' title Flow was asked to give; the rest by DeepSeek over Flow's own titles."""
    out, used = {}, set()
    for t in tiles:
        m = re.match(r"\s*S\s*0*(\d+)\s*([b-d])?\b", t["label"], re.I)
        if not m:
            continue
        n, suf = int(m.group(1)), (m.group(2) or "").lower()
        for r in rows:
            if r["n"] == n and (r.get("extra") or "") == suf and r["asset"] not in out \
                    and (r["mode"] == "video") == bool(t["video"]):
                out[r["asset"]] = t
                used.add(t["key"])
                break
    left_rows = [r for r in rows if r["asset"] not in out]
    left_tiles = [t for t in tiles if t["key"] not in used]
    if left_rows and left_tiles:
        log(f"  matching {len(left_tiles)} item(s) to {len(left_rows)} scene(s) by Flow's titles (DeepSeek) ...")
        listing = "\n".join(f"T{i}: {'VIDEO' if t['video'] else 'IMAGE'} titled \"{t['label']}\"" for i, t in enumerate(left_tiles))
        scenes = "\n".join(f"{r['asset']}: {'VIDEO' if r['mode'] == 'video' else 'IMAGE'} - {r['prompt'][:220]}" for r in left_rows)
        try:
            reply = ask("Match generated items to the scenes they were made for. Each item has a short title the image tool"
                        " gave it. Only match an IMAGE to an IMAGE scene and a VIDEO to a VIDEO scene; leave an item or a"
                        " scene unmatched when nothing fits. Answer with a JSON object {\"scene_asset\": \"T<number>\"}.\n\n"
                        f"ITEMS:\n{listing}\n\nSCENES:\n{scenes}", max_tokens=16000)
            for asset, tid in _json_of(reply).items():
                m = re.match(r"T(\d+)$", str(tid).strip())
                if m and int(m.group(1)) < len(left_tiles) and asset in {r["asset"] for r in left_rows}:
                    t = left_tiles[int(m.group(1))]
                    if t["key"] not in used:
                        out[asset] = t
                        used.add(t["key"])
        except Exception as e:
            log(f"  title matching failed ({str(e)[:100]})")
    return out


def _collect(t, todo, tiles, ctx, page, meta):
    """Match Flow tiles to the todo scenes and save each matched picture or clip. -> Veo clips saved."""
    matched = _match(todo, tiles)
    made_video = 0
    for r in todo:
        tile = matched.get(r["asset"])
        if not tile:
            continue
        src = _video_src(page, tile["key"]) if r["mode"] == "video" else tile["key"]
        data = fa._download(src, ctx, page, "video" if r["mode"] == "video" else "image") if src else None
        if fg._save_asset(t, meta, r, data, "flow") and r["mode"] == "video":
            made_video += 1
    return made_video


def reuse_last_project(t, p, rows, meta):
    """owner, 2026-11-23: a run that stopped after Flow had already made the pictures must not pay for them twice. The
    last project (topic_flow.json, same day) is opened and its items are matched to the scenes still missing.
    -> Veo clips saved (pictures are saved into meta)."""
    rec = json.loads((t / "topic_flow.json").read_text(encoding="utf-8")) if (t / "topic_flow.json").exists() else {}
    if not rec.get("project") or not str(rec.get("ts", "")).startswith(time.strftime("%Y-%m-%d"))             or rec.get("title") != p.get("title"):   # only this same video's project
        return 0
    todo = fg.todo_records(rows)
    by_n = {s["n"]: s for s in rows}
    for r in todo:
        r["prompt"] = by_n[r["n"]]["prompt"]
    pw, ctx, page = fa.attach_or_launch(onscreen=False)
    try:
        log(f"Flow: first taking what the last project already made ({rec['project']})")
        fa.goto(page, rec["project"])
        page.wait_for_timeout(8000)
        tiles = [x for x in _tiles(page) if not str(x.get("label", "")).startswith(REF_PREFIX)]
        log(f"  {len(tiles)} item(s) in that project")
        return _collect(t, todo, tiles, ctx, page, meta) if tiles else 0
    finally:
        try:
            ctx.browser.new_browser_cdp_session().send("Browser.close")
        except Exception:
            pass
        try:   # a second Playwright session in this process (run_flow next) needs this one stopped
            pw.stop()
        except Exception:
            pass
        for _ in range(30):   # and the browser fully closed, or the next connection catches it shutting down
            if not fa.cdp_alive(timeout_s=1):
                break
            time.sleep(1)


def run_flow(t, p, rows, meta):
    """Flow for just these scene rows: reference sheets (only those the rows use) in one message, then the scenes
    in as few messages as possible. Saves what it gets into meta; returns the number of Veo clips made."""
    c = cfg(t)
    todo = fg.todo_records(rows)
    by_n = {s["n"]: s for s in rows}
    for r in todo:
        s = by_n[r["n"]]
        r["prompt"] = next((e["prompt"] for e in s["extras"] if e["suffix"] == r.get("extra")), s["prompt"]) \
            if r.get("extra") else s["prompt"]
    needed = {n for s in rows for n in _refs_of(p, s["n"] - 1)}
    refs = [r for r in p.get("references") or [] if r["name"] in needed]
    video_ok = any(s["mode"] == "video" for s in rows)

    pw, ctx, page = fa.attach_or_launch(onscreen=False)      # our Flow profile, off-screen
    try:
        project = _new_project(page)
        log(f"Flow project: {project}")
        (t / "topic_flow.json").write_text(json.dumps({"project": project, "ts": now(), "title": p.get("title")}),
                                           encoding="utf-8")
        if refs:
            msg = REF_PREAMBLE + "\n\n" + "\n\n".join(
                f"Reference {i}, title \"{REF_PREFIX}{r['name']}\" ({r.get('kind', 'character')}): {r['sheet']}"
                for i, r in enumerate(refs, 1))
            before = {x["key"] for x in _tiles(page)}
            log(f"Flow message 1: {len(refs)} reference sheet(s)")
            _send(page, msg)
            got = _wait_for(page, before, len(refs), c["wait_minutes_per_message"], msg, False)
            log(f"reference sheets: {len(got)} of {len(refs)} made")
            (t / "topic_refs").mkdir(exist_ok=True)
            for x in got:
                data = fa._download(x["key"], ctx, page, "image")
                if data:
                    (t / "topic_refs" / (_slug(x["label"] or "ref") + ".jpg")).write_bytes(data)
        baseline = {x["key"] for x in _tiles(page)}
        chunks, cur = [], []
        for s in rows:
            if cur and sum(1 + len(x["extras"]) for x in cur) + 1 + len(s["extras"]) > c["scenes_per_message"]:
                chunks.append(cur)
                cur = []
            cur.append(s)
        if cur:
            chunks.append(cur)
        for k, chunk in enumerate(chunks, 1):
            expect = sum(1 + len(s["extras"]) for s in chunk)
            has_video = any(s["mode"] == "video" for s in chunk)
            msg = SCENE_PREAMBLE + ("\n" + VIDEO_NOTE if has_video else "") + "\n\n" + _scene_lines(chunk)
            before = {x["key"] for x in _tiles(page)}
            log(f"Flow scene message {k} of {len(chunks)}: {len(chunk)} scene(s), {expect} item(s)")
            _send(page, msg)
            got = _wait_for(page, before, expect, c["wait_minutes_per_message"], msg, video_ok and has_video)
            log(f"Flow scene message {k}: {len(got)} of {expect} item(s) made")
        new = [x for x in _tiles(page) if x["key"] not in baseline]
        return _collect(t, todo, new, ctx, page, meta)
    finally:
        try:   # leave nothing on screen: close the Flow window this run used
            ctx.browser.new_browser_cdp_session().send("Browser.close")
        except Exception:
            pass


# ------------------------------------------------------------------ visuals: ladder first, Flow last
def _have(meta, t, s):
    """The scene has its picture or clip for this prompt - real stock footage included (flow_generate's own check
    counts "stock" as a placeholder still; here stock footage is a real, checked clip)."""
    if fg._is_fresh(meta, t, s["asset"], s["hash"]):
        return True
    rec = meta.get(s["asset"]) or {}
    return (Path(t) / s["asset"]).is_file() and rec.get("source") == "stock" and rec.get("prompt_hash") == s["hash"]


def cmd_visuals(target):
    t = Path(target)
    p = json.loads((t / "topic_plan.json").read_text(encoding="utf-8"))
    acct = cfg(t).get("cloudflare_account_id")
    text, rows = _rows(t)
    meta = fg._load_meta(t)
    credits, flow_n = [], []
    tmp = t / "topic_tmp"
    tmp.mkdir(exist_ok=True)
    used, daily = veo_today(t)
    log(f"visuals: {len(rows)} scenes; Veo today {used} of {daily}")
    ladder = {}   # per beat: what each rung tried and which one fired (the review page shows it above the scrubber)
    for s in rows:
        n, i = s["n"], s["n"] - 1
        vl.TRACE = []                 # a fresh list per beat; visual_ladder.log appends to the current one
        ladder[n] = {"tried": vl.TRACE}
        sc = dict(p["scenes"][i]) if i < len(p["scenes"]) else {}
        sc.update(narration=s["narration"], visual=s["prompt"])
        real = dict(sc, visual=sc["shot"]) if sc.get("shot") else sc   # stock and real photos: the shot as written
        if _have(meta, t, s):
            log(f"[{n:02d}] already has its picture ({(meta.get(s['asset']) or {}).get('source')})")
            continue
        if _refs_of(p, i):
            log(f"[{n:02d}] recurring character/place -> Flow")
            flow_n.append(n)
            continue
        log(f"[{n:02d}] {s['narration'][:70]}...")
        c = vl.stock_video(real, t / f"scene_{n:02d}.mp4", ask, tmp)
        if c:
            if s["mode"] != "video":
                text = _set_video_tag(t, n, True)
            text, rows_now = _rows(t)
            sv = next(r for r in rows_now if r["n"] == n)
            data = (t / f"scene_{n:02d}.mp4").read_bytes()
            fg._save_asset(t, meta, {"n": n, "mode": "video", "asset": sv["asset"], "hash": sv["hash"]}, data, "stock")
            credits.append(c["credit"])
            continue
        if s["mode"] == "video" and used < daily:
            log(f"[{n:02d}] no stock footage -> Veo clip in Flow")
            flow_n.append(n)
            continue
        if s["mode"] == "video":
            text = _set_video_tag(t, n, False)
            text, rows_now = _rows(t)
            s = next(r for r in rows_now if r["n"] == n)
        c = vl.real_photo(real, t / s["asset"], ask)
        if c:
            meta[s["asset"]] = {"prompt_hash": s["hash"], "source": "photo", "ts": now()}
            credits.append(c["credit"])
            continue
        if vl.cloud_still(sc, t / s["asset"], acct):
            meta[s["asset"]] = {"prompt_hash": s["hash"], "source": "cloudflare", "ts": now()}
            continue
        log(f"[{n:02d}] no rung passed -> Flow")
        flow_n.append(n)
    fg._save_meta(t, meta)

    made_video = 0
    if flow_n and os.environ.get("TOPIC_NO_FLOW"):
        log(f"Flow: {len(flow_n)} scene(s) would go to Flow - skipped (TOPIC_NO_FLOW set)")
    elif flow_n:
        text, rows = _rows(t)
        flow_rows = [s for s in rows if s["n"] in flow_n]
        log(f"Flow: {len(flow_rows)} scene(s) of {len(rows)} ({sum(1 for s in flow_rows if s['mode'] == 'video')} Veo)")
        try:
            made_video = reuse_last_project(t, p, flow_rows, meta)
            fg._save_meta(t, meta)   # keep what was taken even if the next step fails
            flow_rows = [s for s in flow_rows if not _have(meta, t, s)]
            if flow_rows:
                made_video += run_flow(t, p, flow_rows, meta)
        except fa.AutomationFlagged as e:
            log("FLOW STOPPED - " + str(e))
        except fa.Blocker as e:
            log("FLOW STOPPED - " + str(e))
        veo_add(t, made_video)
        fg._save_meta(t, meta)
    else:
        log("Flow: not needed for this video")

    # anything still missing: a [VIDEO] scene without a clip becomes a still; every missing still gets Cloudflare
    text, rows = _rows(t)
    for s in rows:
        if s["mode"] == "video" and not _have(meta, t, s):
            log(f"[{s['n']:02d}] no clip - becomes a still scene")
            text = _set_video_tag(t, s["n"], False)
    text, rows = _rows(t)
    for s in rows:   # a scene whose Veo clip never came: its own second still (scene_NNb.jpg) from Flow stands in
        extra = t / f"scene_{s['n']:02d}b.jpg"
        if s["mode"] != "video" and not _have(meta, t, s) and extra.is_file() and s["asset"].endswith(".jpg"):
            shutil.copyfile(extra, t / s["asset"])
            meta[s["asset"]] = {"prompt_hash": s["hash"], "source": "flow", "ts": now()}
            log(f"[{s['n']:02d}] uses its second Flow still in place of the clip")
    for s in rows:
        if not _have(meta, t, s):
            try:
                vl.cloudflare_still(s["prompt"], t / s["asset"], acct)
                meta[s["asset"]] = {"prompt_hash": s["hash"], "source": "cloudflare-unchecked", "ts": now()}
                log(f"[{s['n']:02d}] stand-in Cloudflare still (unchecked)")
            except Exception as e:
                log(f"[{s['n']:02d}] stand-in failed: {str(e)[:100]}")
    fg._save_meta(t, meta)
    rows = write_storyboard(text, t)
    for s in rows:   # which rung fired for each beat (Flow and the stand-in included)
        src = (meta.get(s["asset"]) or {}).get("source", "missing")
        ladder.setdefault(s["n"], {"tried": []})["fired"] = src
    (t / "ladder.json").write_text(json.dumps({str(k): v for k, v in sorted(ladder.items())}, indent=1), encoding="utf-8")
    (t / "credits.txt").write_text("\n".join(dict.fromkeys(credits)) + ("\n" if credits else ""), encoding="utf-8")
    by_src = {}
    for s in rows:
        src = (meta.get(s["asset"]) or {}).get("source", "missing")
        by_src[src] = by_src.get(src, 0) + 1
    log(f"visuals by source: {by_src}; Veo clips made: {made_video} (today {veo_today(t)[0]} of {daily})")
    missing = [s["asset"] for s in rows if not _have(meta, t, s)]
    if missing:
        raise SystemExit(f"{len(missing)} scene(s) still have no picture: {missing}")
    log("__VISUALS_DONE__")


def main():
    a = sys.argv[1:]
    target = DEFAULT_TARGET
    if "--target" in a:
        i = a.index("--target")
        target = Path(a[i + 1])
        del a[i:i + 2]
    no_video = "--no-video" in a
    a = [x for x in a if x != "--no-video"]
    if a[:1] == ["plan"] and len(a) >= 3:
        cmd_plan(a[1], a[2], target, allow_video=not no_video)
    elif a[:1] == ["visuals"]:
        cmd_visuals(target)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()

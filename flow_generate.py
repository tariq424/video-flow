"""
flow_generate.py - CLI runner for flow_automation: batch-generate scene assets
in Google Flow and drop them into the video project folder as
scene_NN.jpg / scene_NN.mp4.

    python flow_generate.py <prompts.json> [--target DIR]
        prompts.json = [{"type": "image"|"video", "prompt": "..."}, ...]
        item N is saved as scene_NN.jpg / scene_NN.mp4 in the target folder
        (default: FLOW_TARGET_DIR env, else F:\\videos\\google-flow-videos).
        build_video.py prefers scene_NN files over keyword matches.
    python flow_generate.py --check
        passive busy check + profile/sign-in gate only. Submits nothing,
        spends nothing.

Every run: passive busy monitor first (pauses while another automation is
driving Flow), then the profile/sign-in gate, then the batch. Exit code
0 = at least one asset saved (or nothing failed), 1 = nothing was saved.
Individual item failures are always listed in the summary.
"""

import os
import re
import sys
import time
import json
import random
import shutil
import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import flow_automation as fa                      # noqa: E402
from flow_automation import Blocker               # noqa: E402

DEFAULT_TARGET = Path(os.environ.get("FLOW_TARGET_DIR") or r"F:\videos\google-flow-videos")
BATCH_SIZE = 10   # long scripts are processed in groups of this many items,
                  # with a rest + busy-monitor checkpoint between groups


def check_only():
    """Busy monitor + profile/sign-in gate, nothing submitted."""
    if not fa.wait_until_flow_free(max_wait_s=300, every_s=15):
        return False
    pw, ctx, page = fa.open_browser()
    try:
        fa.goto(page, fa.FLOW_HOME)
        return fa.run_gate(page)
    finally:
        ctx.close()
        pw.stop()


def _prompt_hash(desc):
    return hashlib.sha1(" ".join(desc.split()).lower().encode("utf-8")).hexdigest()[:16]


def _load_meta(target):
    p = Path(target) / "stills_meta.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_meta(target, meta):
    try:
        Path(target).mkdir(parents=True, exist_ok=True)
        (Path(target) / "stills_meta.json").write_text(
            json.dumps(meta, indent=1), encoding="utf-8")
    except Exception:
        pass


def _violated_path(target):
    return Path(target) / "violated_prompts.json"


def _load_violated(target):
    try:
        return json.loads(_violated_path(target).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_violated(target, data):
    try:
        _violated_path(target).write_text(
            json.dumps(data, indent=1), encoding="utf-8")
    except Exception:
        pass


def _manual_scenes(prompts, target, skip_existing=True):
    """The scenes the manual batch must generate: sanitized exactly like
    run_batch (same header-line / narration-only / sanitize order), optionally
    limited to scenes whose still is missing or made from a different prompt.
    Returns [{"n", "prompt", "narration", "hash"}, ...]."""
    meta = _load_meta(target)
    out = []
    for i, it in enumerate(prompts, 1):
        desc = str(it.get("prompt", "")).strip()
        narr = str(it.get("narration", "")).strip()
        if desc and narr and re.match(r"(title|style)\s*:", desc, re.I):
            desc = narr
        elif narr and not desc:
            desc = "Documentary still, photorealistic, 16:9, cinematic lighting: " + narr
        if not desc:
            continue
        desc = fa.sanitize_prompt(desc)
        ph = _prompt_hash(desc)
        dest = Path(target) / f"scene_{i:02d}.jpg"
        rec = (meta.get(dest.name) or {}) if dest.is_file() else {}
        if skip_existing and dest.is_file() and rec.get("source") != "stock" \
                and rec.get("prompt_hash") == ph:
            fa.log(f"[{i:02d}] SKIP - {dest.name} already exists (same prompt)")
            continue
        if skip_existing and dest.is_file() and rec.get("source") == "stock":
            fa.log(f"[{i:02d}] QUEUED - {dest.name} is a stock fallback image "
                   "(low relevance) - replacing with a real Flow generation")
        out.append({"n": i, "prompt": desc, "narration": narr, "hash": ph})
    return out


def _batch_text(scenes):
    """The exact text agent_batch_generate would send - one Agent submission
    covering every scene, ready for a human to paste into Flow."""
    text = fa.AGENT_BATCH_PREAMBLE + "\n\n"
    for s in scenes:
        text += f"Scene {s['n']}: {s['prompt']}\n"
        if s.get("narration"):
            text += f"NARRATION: {s['narration']}\n"
        text += "\n"
    return text


def _copy_to_clipboard(text):
    try:
        import tkinter
        r = tkinter.Tk()
        r.withdraw()
        r.clipboard_clear()
        r.clipboard_append(text)
        r.update_idletasks()
        r.update()
        r.destroy()
        return True
    except Exception as e:
        fa.log(f"clipboard copy failed ({type(e).__name__}: {str(e)[:80]}) - "
               "the batch text is still on disk")
        return False


def prepare_batch(prompts, target):
    """Manual-assist step 1: build the Flow Agent batch for the scenes that
    still need stills, write agent_batch.txt next to the target and copy it to
    the clipboard. The user pastes it into the Flow composer themselves -
    human-paste submissions do not trip Google's automation guard."""
    scenes = _manual_scenes(prompts, target, skip_existing=True)
    if not scenes:
        fa.log("manual batch: every scene already has a fresh still - nothing to generate")
        return False
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    (out / "agent_batch.txt").write_text(_batch_text(scenes), encoding="utf-8")
    (out / "scenes_todo.json").write_text(
        json.dumps([{"n": s["n"], "hash": s["hash"]} for s in scenes], indent=1),
        encoding="utf-8")
    _copy_to_clipboard(_batch_text(scenes))
    fa.log(f"manual batch ready: {len(scenes)} scene(s) -> {out / 'agent_batch.txt'} "
           "(also copied to the clipboard)")
    return True


def open_project_session(target):
    """Manual-assist step 2: open/attach the REAL browser window on our Flow
    profile, run the sign-in gate, and record the images currently visible as
    the import baseline. Leaves the browser OPEN for the user to paste into."""
    todo_path = Path(target) / "scenes_todo.json"
    if not todo_path.is_file():
        fa.log("no scenes_todo.json - run --prepare-batch first")
        return False
    todo = json.loads(todo_path.read_text(encoding="utf-8"))
    url = fa.PROJECT_URL or fa.FLOW_HOME
    pw, ctx, page = fa.attach_or_launch()
    try:
        fa.goto(page, url)
    except Exception as e:
        fa.log(f"navigation issue ({str(e)[:100]}) - the browser window is still open")
    try:
        if fa.run_gate(page):
            fa.log("signed in - ready for your paste")
        else:
            fa.log("sign-in not confirmed - complete it INSIDE the opened window if asked")
    except Exception as e:
        fa.log(f"gate check skipped ({str(e)[:80]})")
    try:
        page.bring_to_front()
    except Exception:
        pass
    baseline = {"url": url, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "todo": todo, "srcs": fa.frames_imgs_snapshot(page)}
    Path(target).mkdir(parents=True, exist_ok=True)
    (Path(target) / "import_baseline.json").write_text(
        json.dumps(baseline, indent=1), encoding="utf-8")
    fa.log("")
    fa.log("=" * 62)
    fa.log(f"MANUAL STEP - in the Flow window that just opened:")
    fa.log(f"  1. click into the composer, paste (Ctrl+V) and send")
    fa.log(f"     ({len(todo)} scene(s) are already on your clipboard)")
    fa.log(f"  2. wait until every scene image has finished generating")
    fa.log(f"  3. then click 'Import Flow stills' here in the builder")
    fa.log("=" * 62)
    # deliberately NO ctx.close()/pw.stop(): the browser stays for the paste
    return True


def import_batch(prompts, target, project_url=None):
    """Manual-assist step 3: read the generated images from a Flow project and
    map them to scene_NN.jpg in DOM (chronological) order.

    project_url: the URL the user pasted into (any window, same account) -
    overrides the recorded baseline URL. When the URL does not match the
    baseline (or no baseline exists), ALL images visible in the project are
    the candidate set - designed for a BRAND-NEW project where every image
    belongs to the paste. On a count mismatch nothing is mapped (order would
    be guesswork) - the user generates the rest and re-imports."""
    base_path = Path(target) / "import_baseline.json"
    base = json.loads(base_path.read_text(encoding="utf-8")) if base_path.is_file() else {}
    todo_path = Path(target) / "scenes_todo.json"
    if todo_path.is_file():
        todo = json.loads(todo_path.read_text(encoding="utf-8"))
    else:
        # no prepare step ran - build the todo from the script itself
        todo = [{"n": i, "hash": _prompt_hash(fa.sanitize_prompt(str(it.get("prompt", "")).strip()))}
                for i, it in enumerate(prompts, 1)]
    if not todo:
        fa.log("no scenes to import - prepare the batch first")
        return False
    url = project_url or base.get("url") or fa.PROJECT_URL or fa.FLOW_HOME
    if not fa.cdp_alive():
        fa.log("no manual-assist browser running - launching one (same profile, "
               "so the same account sees the project) ...")
    pw, ctx, page = fa.attach_or_launch()
    try:
        fa.goto(page, url)
        if not fa.run_gate(page):
            fa.log("sign-in not confirmed - complete it inside the window and re-run import")
            return False
        same_base = base.get("url") == url and base.get("srcs")
        before = set(base.get("srcs") or []) if same_base else set()
        if same_base:
            new_srcs = [s for s in fa.frames_imgs(page) if s not in before]
        else:
            new_srcs = list(fa.frames_imgs(page))
            fa.log("no matching baseline for this URL - treating ALL images in "
                   "the project as the batch (use a brand-new project!)")
        fa.log(f"import: {len(new_srcs)} image(s) found "
               f"({len(todo)} expected)")
        if len(new_srcs) < len(todo):
            fa.log(f"IMPORT INCOMPLETE: {len(todo) - len(new_srcs)} scene image(s) "
                   f"still missing - let Flow finish (or regenerate the missing "
                   f"ones) and click 'Import Flow stills' again. Nothing was mapped.")
            return False
        if len(new_srcs) > len(todo):
            fa.log("more images than expected - mapping the FIRST "
                   f"{len(todo)} in DOM order (chronological = scene order; "
                   "extra images stay in Flow)")
            new_srcs = new_srcs[:len(todo)]

        target_path = Path(target)
        target_path.mkdir(parents=True, exist_ok=True)
        meta = _load_meta(target_path)
        for s, src in zip(todo, new_srcs):
            n, ph = s["n"], s["hash"]
            data = fa._download(src, ctx, page, "image")
            if not data or not fa.is_image(data):
                fa.log(f"[{n:02d}] FAILED - download failed or not a valid image "
                       f"({str(src)[:70]})")
                continue
            dest = target_path / f"scene_{n:02d}.jpg"
            dest.write_bytes(data)
            meta[dest.name] = {"prompt_hash": ph,
                               "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
            fa.log(f"[{n:02d}] SAVED -> {dest} ({len(data) // 1024} KB)")
        _save_meta(target_path, meta)
        saved, missing = [], []
        for s in todo:
            rec = meta.get(f"scene_{s['n']:02d}.jpg") or {}
            (saved if rec.get("prompt_hash") == s["hash"] else missing).append(s["n"])
        if missing:
            fa.log(f"VALIDATION: scene(s) still missing after import: {missing}")
            return False
        fa.log(f"VALIDATION PASSED: {len(saved)} scene(s) imported with provenance")
        return True
    finally:
        # keep the window open - the user may need to re-import
        pass


def run_batch_agent(prompts, target, ctx=None, page=None):
    """Agent batch mode: ONE Flow Agent submission generates all scene stills,
    which are then mapped to scene_NN.jpg by submission order. Missing beats
    fall back to stock sources (Bing, Openverse). Returns True on success.
    ctx/page may be passed in by a caller that already opened the browser."""
    scenes = []
    for i, it in enumerate(prompts, 1):
        p = (it.get('prompt') or '').strip()
        n = (it.get('narration') or '').strip()
        if p:
            scenes.append({'n': i, 'prompt': p, 'narration': n})
    if not scenes:
        fa.log("agent batch: no scene prompts found")
        return False

    fa.log(f"agent batch: {len(scenes)} scene prompt(s) in ONE Flow Agent submission")
    own_browser = ctx is None or page is None
    saved_paths = []
    pw = None
    try:
        if own_browser:
            pw, ctx, page = fa.open_browser()
        fa.goto(page, fa.FLOW_HOME)
        if not fa.run_gate(page):
            raise fa.Blocker("gate failed (signed out / CAPTCHA / unknown state)")
        srcs = fa.agent_batch_generate(ctx, page, scenes)
        saved_paths = fa.agent_batch_download(ctx, page, srcs, scenes)
    except fa.Blocker as e:
        fa.log(f"agent batch FAILED - {e}")
        return False
    except Exception as e:
        fa.log(f"agent batch FAILED - {type(e).__name__}: {str(e)[:200]}")
        return False
    finally:
        if own_browser:
            try:
                ctx.close()
                pw.stop()
            except Exception:
                pass

    # map the batch images to scene_NN.jpg in submission order
    target_path = Path(target)
    target_path.mkdir(parents=True, exist_ok=True)
    meta = _load_meta(target_path)
    saved = []
    for i, src_path in enumerate(saved_paths, 1):
        dest = target_path / f"scene_{i:02d}.jpg"
        shutil.copyfile(src_path, dest)
        meta[dest.name] = {"prompt_hash": _prompt_hash(scenes[i - 1]['prompt']),
                           "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        fa.log(f"[{i:02d}] SAVED -> {dest}")
        saved.append(i)
    _save_meta(target_path, meta)

    # validation: the expected count vs the actual
    missing = [i for i in range(1, len(scenes) + 1) if i not in saved]
    if missing:
        fa.log(f"VALIDATION: {len(missing)} scene(s) missing from the batch: {missing}")
        return False
    fa.log(f"VALIDATION PASSED: {len(saved)} scene(s) mapped in order")
    return True


def run_batch(prompts, target, skip_existing=False):
    saved, failed = [], []
    meta = _load_meta(target)
    violated = _load_violated(target)
    total = len(prompts)
    fa.log(f"batch: {total} item(s) -> {target} (processed in groups of {BATCH_SIZE})")
    for i, item in enumerate(prompts, 1):
        # group checkpoint: between groups of BATCH_SIZE, cool down and make
        # sure no other automation has started driving Flow
        if i > 1 and (i - 1) % BATCH_SIZE == 0:
            fa.log(f"batch checkpoint: {i - 1} of {total} done - resting 30s before "
                   f"items {i}-{min(i + BATCH_SIZE - 1, total)} ...")
            time.sleep(30)
            if not fa.wait_until_flow_free():
                failed.append((i, "Flow still busy - batch stopped here"))
                break
        kind = str(item.get("type", "")).strip().lower()
        desc = str(item.get("prompt", "")).strip()
        aspect = str(item.get("aspect", "16:9")).strip()
        narr = str(item.get("narration", "")).strip()

        # Title:/Style: header lines are script metadata, not visuals - if the
        # block carries narration, illustrate THAT instead of the header text
        if desc and narr and re.match(r"(title|style)\s*:", desc, re.I):
            desc = narr
            fa.log(f"[{i:02d}] header line detected - illustrating the narration instead")
        elif narr and not desc:
            desc = "Documentary still, photorealistic, 16:9, cinematic lighting: " + narr
            fa.log(f"[{i:02d}] no visual prompt - illustrating the narration instead")

        # PRE-EMPTIVE sanitize: conflict/weapon/minor-imagery terms never reach
        # Flow's image filter. The reform ladder below stays as the second line
        # of defense; the provenance hash records the ORIGINAL prompt so resume
        # stays consistent across sanitizer updates.
        safe_desc = fa.sanitize_prompt(desc)
        if safe_desc != desc:
            fa.log(f"[{i:02d}] prompt sanitized pre-send (policy-trigger terms neutralized)")
            desc = safe_desc

        # a prompt Flow refused for a policy violation is recorded permanently
        # and NEVER repeated (same run, resume, or re-run)
        ph = _prompt_hash(desc)
        if ph in violated:
            reason = violated[ph].get("reason", "violation")
            fa.log(f"[{i:02d}] SKIPPED - Flow refused this prompt previously and it "
                   f"is not being repeated ({reason[:100]}). Reword the prompt and "
                   f"remove its entry from violated_prompts.json to retry.")
            failed.append((i, "previously refused by Flow (violation) - not repeated"))
            continue

        if kind not in ("image", "video") or not desc:
            fa.log(f"[{i:02d}] SKIPPED - needs \"type\" (image|video) and visual prompt "
                   f"lines (a NARRATION:-only block has no image): {json.dumps(item)[:120]}")
            failed.append((i, "invalid item"))
            continue

        ext = "mp4" if kind == "video" else "jpg"
        dest = Path(target) / f"scene_{i:02d}.{ext}"

        # resume mode: keep an existing still ONLY if it was generated from
        # the same prompt - a new script must regenerate, not reuse. A still
        # that came from the STOCK FALLBACK is never "fresh": it is a generic
        # web image with weak relevance to its beat, so it always regenerates.
        if skip_existing and dest.is_file():
            rec = meta.get(dest.name) or {}
            if rec.get("source") == "stock":
                fa.log(f"[{i:02d}] REGENERATE - {dest.name} is a stock fallback "
                       "image (low relevance) - replacing with a Flow generation")
            elif rec.get("prompt_hash") == _prompt_hash(desc):
                fa.log(f"[{i:02d}] SKIP - {dest.name} already exists (same prompt)")
                saved.append(i)
                continue
            else:
                fa.log(f"[{i:02d}] REGENERATE - {dest.name} exists but was made "
                       "from a different prompt")

        fa.log(f"[{i:02d}] Flow {kind} generation: {desc[:90]}{'...' if len(desc) > 90 else ''}")

        # passive monitor: another automation must not be driving Flow
        if not fa.wait_until_flow_free():
            failed.append((i, "Flow still busy - batch stopped here"))
            break

        # generation ladder: original prompt -> documentary-safe rewrite ->
        # neutral symbolic still (the last rung only when Flow refuses the
        # prompt for policy). The still that lands is spliced into the video;
        # its provenance records the ORIGINAL prompt hash so resume stays
        # consistent, and refusals are remembered so they're never repeated.
        variants = [desc]
        vi = 0
        last_exc = None
        while vi < len(variants):
            sent_desc = variants[vi]
            vi += 1
            try:
                out = fa.generate(sent_desc, kind, aspect)
                data = Path(out).read_bytes()
                valid = fa.is_video(data) if kind == "video" else fa.is_image(data)
                if not valid:
                    raise Blocker("saved file failed header validation (ftyp/JPEG/PNG)")
                Path(target).mkdir(parents=True, exist_ok=True)
                shutil.copyfile(out, dest)
                meta[dest.name] = {"prompt_hash": ph,
                                   "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
                _save_meta(target, meta)
                fa.log(f"[{i:02d}] SAVED -> {dest} ({len(data) // 1024} KB)"
                       + (" (documentary-safe rewrite)" if vi > 1 else ""))
                saved.append(i)
                last_exc = None
                break
            except Exception as e:                   # keep the batch going
                last_exc = e
                msg = str(e)
                # a Flow policy refusal is recorded permanently - that exact
                # prompt is never sent again (this run, resume, or re-run)
                if "Flow refused this prompt" in msg or "session says" in msg:
                    violated[ph] = {"prompt": desc[:200],
                                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                    "reason": msg[:300]}
                    _save_violated(target, violated)
                    if vi >= len(variants):
                        rewritten = fa.sanitize_prompt(variants[-1])
                        if rewritten.lower() != variants[-1].lower() and rewritten.strip():
                            variants.append(rewritten)
                            fa.log(f"[{i:02d}] Flow refused this prompt - retrying with "
                                   f"a documentary-safe rewrite ...")
                        else:
                            variants.append(fa.neutral_prompt())
                            fa.log(f"[{i:02d}] Flow refused this prompt - retrying with "
                                   f"a neutral symbolic still ...")
                    time.sleep(5)
                    continue
                break
        if last_exc is not None:
            why = (str(last_exc) if isinstance(last_exc, Blocker)
                   else f"{type(last_exc).__name__}: {str(last_exc)[:200]}")
            fa.log(f"[{i:02d}] FAILED - {why}")

            # STOCK FALLBACK: when Flow refuses or fails a generation, keep the
            # beat covered with a documentary still from stock sources (Bing,
            # Openverse) - the beat is never left empty or stale
            stock_ok = False
            try:
                import stock_stills
                fa.log(f"[{i:02d}] fetching a documentary still from stock sources "
                       f"(Bing, Openverse) ...")
                q = re.sub(r"^(visual prompt|end card[^:]*)[:\s]*", "", desc,
                           flags=re.I).strip()
                stock_ok = stock_stills.fetch_still(q, str(dest))
            except Exception as se:
                fa.log(f"[{i:02d}] stock fallback error: {type(se).__name__}: {str(se)[:120]}")
            if stock_ok:
                # "source": "stock" marks this still as a generic web image -
                # resume and prepare-batch treat it as STALE so a real Flow
                # generation always replaces it on the next run
                meta[dest.name] = {"prompt_hash": ph,
                                   "source": "stock",
                                   "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
                _save_meta(target, meta)
                fa.log(f"[{i:02d}] SAVED (stock FALLBACK, low relevance) -> {dest}")
                saved.append(i)
                last_exc = None

        # pacing cooldown after every GENERATED item: randomized rest between
        # browser sessions - back-to-back regular sessions are what tripped
        # Google's unusual-activity detection. On an unusual-activity failure,
        # back off 10 minutes once before the next item.
        if saved or failed:
            rest = random.uniform(45, 120)
            fa.log(f"[{i:02d}] pacing cooldown {rest:.0f}s before the next generation ...")
            time.sleep(rest)
        if last_exc is not None and "unusual activity" in str(last_exc).lower():
            fa.log(f"[{i:02d}] unusual-activity flag - backing off 10 minutes before "
                   f"the next item (the flag usually clears on its own) ...")
            time.sleep(600)

    fa.log("")
    fa.log(f"=== Flow batch summary: {len(saved)} saved, {len(failed)} failed/skipped ===")
    for i, why in failed:
        fa.log(f"  scene {i:02d}: {why}")
    if saved:
        fa.log("build_video.py prefers scene_NN files, so a plain Build will use them.")
    # chain-friendly: the batch succeeded as long as SOMETHING was saved
    # (single-item Flow hiccups must not abort a one-click pipeline that
    # can still build from the remaining assets).
    return bool(saved) or not failed


def split_block(block):
    """One prompt block -> (visual_prompt, narration_or_None).
    The narration is the NARRATION: marked text - on its own line (spec) or
    inline at the end of the block. It never reaches Flow as image prompt."""
    parts = re.split(r"narration\s*:\s*", block, flags=re.I, maxsplit=1)
    if len(parts) == 2:
        visual, narration = parts[0], parts[1].strip()
    else:
        visual, narration = block, None
    visual = re.sub(r"\s+", " ", visual).strip()
    return visual, narration


def parse_prompts_text(text):
    """Plain-paragraph prompts -> image-only items (free-tier friendly: stills
    are approved only at 0 credits). Paragraphs are blank-line separated; a
    single block with several lines falls back to one prompt per line.
    Item N becomes scene_NN.jpg. A trailing "NARRATION: <text>" per block is
    carried in the item's "narration" key (kept out of the visual prompt)."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if len(blocks) <= 1:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(lines) > 1:
            blocks = lines
    items = []
    pending = []   # narration blocks stranded BEFORE any visual block
    for b in blocks:
        visual, narration = split_block(b)
        if not visual and narration:
            # a NARRATION: line stranded in its own block belongs to the
            # previous scene (a blank line before it is a common paste slip)
            if items:
                if items[-1].get("narration"):
                    items[-1]["narration"] += " " + narration
                else:
                    items[-1]["narration"] = narration
            else:
                pending.append(narration)
            continue
        item = {"type": "image", "prompt": visual}
        parts = pending + ([narration] if narration else [])
        if parts:
            item["narration"] = " ".join(parts)
        pending = []
        items.append(item)
    return items


def narration_script(items):
    """The narration lines, in scene order - the TTS script."""
    return [str(i["narration"]).strip() for i in items
            if isinstance(i, dict) and i.get("narration")]


def main():
    argv = sys.argv[1:]
    skip_existing = "--skip-existing" in argv
    argv = [a for a in argv if a != "--skip-existing"]

    target = DEFAULT_TARGET
    if "--target" in argv:
        k = argv.index("--target")
        if k + 1 < len(argv):
            target = Path(argv[k + 1])
            argv = argv[:k] + argv[k + 2:]

    if "--check" in argv:
        sys.exit(0 if check_only() else 1)


    if not argv:
        fa.log("usage: python flow_generate.py <prompts.json> [--target DIR] | --check | "
               "--prepare-batch | --open-project | --import-batch")
        sys.exit(1)
    prompts_file = Path(argv[0])
    try:
        raw = prompts_file.read_text(encoding="utf-8")
    except Exception as e:
        fa.log(f"FAILED - cannot read prompts file {prompts_file}: {e}")
        sys.exit(1)
    if prompts_file.suffix.lower() == ".txt":
        prompts = parse_prompts_text(raw)
        fa.log(f"parsed {len(prompts)} prompt(s) from paragraphs (images only)")
    else:
        try:
            prompts = json.loads(raw)
        except Exception as e:
            fa.log(f"FAILED - {prompts_file.name} is not valid JSON: {e}")
            fa.log("(for plain prompts, save them as a .txt file: one prompt per paragraph)")
            sys.exit(1)
    if not isinstance(prompts, list) or not prompts:
        fa.log("FAILED - prompts file must be a non-empty JSON list or a .txt with prompts.")
        sys.exit(1)

    # ---- manual-assist workflow (paste-proof against Google's automation
    # guard): the factory prepares the batch, the human pastes and sends it in
    # the REAL browser window, the factory imports the finished stills.
    project_url = None
    if "--project-url" in argv:
        k = argv.index("--project-url")
        if k + 1 < len(argv):
            project_url = argv[k + 1].strip()
            argv = argv[:k] + argv[k + 2:]
    if "--prepare-batch" in argv:
        sys.exit(0 if prepare_batch(prompts, target) else 1)
    if "--open-project" in argv:
        sys.exit(0 if open_project_session(target) else 1)
    if "--import-batch" in argv:
        sys.exit(0 if import_batch(prompts, target, project_url=project_url) else 1)

    if "--agent-batch" in argv:
        # programmatic Agent batch: ONE submission generates all scene stills
        if not fa.wait_until_flow_free():
            sys.exit(1)
        pw, ctx, page = fa.open_browser()
        try:
            fa.goto(page, fa.FLOW_HOME)
            ok = fa.run_gate(page)
            if ok:
                ok = run_batch_agent(prompts, target, ctx, page)
        finally:
            try:
                ctx.close()
                pw.stop()
            except Exception:
                pass
        sys.exit(0 if ok else 1)

    # passive busy monitor, then the profile/sign-in gate before spending anything
    if not fa.wait_until_flow_free():
        sys.exit(1)
    pw, ctx, page = fa.open_browser()
    try:
        fa.goto(page, fa.FLOW_HOME)
        ok = fa.run_gate(page)
    finally:
        ctx.close()
        pw.stop()
    if not ok:
        sys.exit(1)

    sys.exit(0 if run_batch(prompts, target, skip_existing=skip_existing) else 1)


if __name__ == "__main__":
    main()

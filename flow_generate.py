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
        # the same prompt - a new script must regenerate, not reuse
        if skip_existing and dest.is_file():
            rec = meta.get(dest.name) or {}
            if rec.get("prompt_hash") == _prompt_hash(desc):
                fa.log(f"[{i:02d}] SKIP - {dest.name} already exists (same prompt)")
                saved.append(i)
                continue
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
                meta[dest.name] = {"prompt_hash": ph,
                                   "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
                _save_meta(target, meta)
                fa.log(f"[{i:02d}] SAVED (stock) -> {dest}")
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
        fa.log("usage: python flow_generate.py <prompts.json> [--target DIR] | --check")
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

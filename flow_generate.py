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
import shutil
import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import flow_automation as fa                      # noqa: E402
from flow_automation import Blocker               # noqa: E402

DEFAULT_TARGET = Path(os.environ.get("FLOW_TARGET_DIR") or r"F:\videos\google-flow-videos")


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


def run_batch(prompts, target, skip_existing=False):
    saved, failed = [], []
    meta = _load_meta(target)
    fa.log(f"batch: {len(prompts)} item(s) -> {target}")
    for i, item in enumerate(prompts, 1):
        kind = str(item.get("type", "")).strip().lower()
        desc = str(item.get("prompt", "")).strip()
        aspect = str(item.get("aspect", "16:9")).strip()
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

        last_exc = None
        for attempt in (1, 2):
            try:
                out = fa.generate(desc, kind, aspect)
                data = Path(out).read_bytes()
                valid = fa.is_video(data) if kind == "video" else fa.is_image(data)
                if not valid:
                    raise Blocker("saved file failed header validation (ftyp/JPEG/PNG)")
                Path(target).mkdir(parents=True, exist_ok=True)
                shutil.copyfile(out, dest)
                meta[dest.name] = {"prompt_hash": _prompt_hash(desc),
                                   "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
                _save_meta(target, meta)
                fa.log(f"[{i:02d}] SAVED -> {dest} ({len(data) // 1024} KB)")
                saved.append(i)
                last_exc = None
                break
            except Exception as e:                   # keep the batch going
                last_exc = e
                msg = str(e)
                # "typed but not sent" / composer issues are transient Flow UI
                # timing - one immediate second attempt is worth the time
                if attempt == 1 and ("not sent" in msg or "composer" in msg):
                    fa.log(f"[{i:02d}] transient Flow UI failure - retrying once in 5s ...")
                    time.sleep(5)
                    continue
                break
        if last_exc is not None:
            if isinstance(last_exc, Blocker):
                fa.log(f"[{i:02d}] FAILED - {last_exc}")
                failed.append((i, str(last_exc)))
            else:
                fa.log(f"[{i:02d}] FAILED - {type(last_exc).__name__}: {str(last_exc)[:200]}")
                failed.append((i, f"{type(last_exc).__name__}: {str(last_exc)[:160]}"))

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

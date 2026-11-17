"""Depth layers for 2.5D parallax: estimate depth on a still (Depth Anything V2
Small, ONNX, CPU, ~1-3 s per image), then split the still into layers that the
Remotion "Film" composition moves at different speeds.

- One clear near subject (a person, a vehicle): 2 layers - the background with
  the subject painted out, and the subject as ONE rigid layer, so it never
  tears apart.
- Otherwise (landscapes, crowds): 3 layers - the full picture, the nearer half
  and the nearest ~20 %, with feathered edges; the full picture stays
  underneath, so moving layers never reveal holes.

The model (Apache-2.0, ~100 MB) is downloaded once into ./models/.
Recreated for video-flow from the design used in Video Studio (no shared code).

CLI:  python depth_layers.py still.jpg out_prefix   -> out_prefix_L0.jpg, _L1.png ...
"""
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
MODEL = ROOT / "models" / "depth_anything_v2_small.onnx"
URLS = ["https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model.onnx",
        "https://huggingface.co/inference4j/depth-anything-v2-small/resolve/main/model.onnx"]
_sess = None


def _model(log=print):
    global _sess
    if _sess is not None:
        return _sess
    import onnxruntime as ort
    MODEL.parent.mkdir(exist_ok=True)
    if not (MODEL.exists() and MODEL.stat().st_size > 10_000_000):
        import requests
        for u in URLS:
            try:
                log("  downloading the depth model (one time, ~100 MB) ...")
                r = requests.get(u, timeout=600, stream=True)
                r.raise_for_status()
                tmp = MODEL.with_suffix(".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                if tmp.stat().st_size > 10_000_000:
                    tmp.replace(MODEL)
                    break
            except Exception as e:  # noqa: BLE001 - try the next mirror
                log(f"  depth model download failed from {u.split('/')[3]} ({str(e)[:80]})")
    if not MODEL.exists():
        raise RuntimeError("depth model unavailable")
    _sess = ort.InferenceSession(str(MODEL), providers=["CPUExecutionProvider"])
    return _sess


def depth_map(img, log=print):
    """img: PIL RGB -> float32 (H, W) in [0, 1], 1 = nearest."""
    from PIL import Image
    s = _model(log)
    w, h = img.size
    k = 518 / max(w, h)
    nw, nh = [int(np.ceil(x * k / 14) * 14) for x in (w, h)]
    x = np.asarray(img.resize((nw, nh), Image.BICUBIC), dtype=np.float32) / 255.0
    x = (x - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
    x = x.transpose(2, 0, 1)[None].astype(np.float32)
    out = s.run(None, {s.get_inputs()[0].name: x})[0]
    d = out.reshape(out.shape[-2], out.shape[-1])
    d = (d - d.min()) / max(1e-6, d.max() - d.min())
    return np.asarray(Image.fromarray((d * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC),
                      dtype=np.float32) / 255.0


def _components(m):
    """4-connected labels of a small bool mask (numpy only) -> (labels, sizes)."""
    H, W = m.shape
    lab = np.zeros((H, W), np.int32)
    sizes = [0]
    flat = m.ravel()
    lf = lab.ravel()
    for start in np.flatnonzero(flat):
        if lf[start]:
            continue
        n = len(sizes)
        lf[start] = n
        stack = [start]
        c = 0
        while stack:
            i = stack.pop()
            c += 1
            y, x = divmod(i, W)
            for j, ok in ((i - 1, x > 0), (i + 1, x < W - 1), (i - W, y > 0), (i + W, y < H - 1)):
                if ok and flat[j] and not lf[j]:
                    lf[j] = n
                    stack.append(j)
        sizes.append(c)
    return lab, sizes


def subject_mask(d):
    """The single dominant near object as one solid mask, or None (landscapes,
    crowds: the near region runs off the bottom edge or is spread out)."""
    from PIL import Image, ImageFilter
    h, w = d.shape
    sw, sh = 160, int(160 * h / w)
    small = np.asarray(Image.fromarray((d * 255).astype(np.uint8)).resize((sw, sh), Image.BILINEAR),
                       dtype=np.float32) / 255
    m = small > np.quantile(small, 0.55)
    mi = Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    m = np.asarray(mi) > 127
    lab, sizes = _components(m)
    edge = set(lab[-1, :].tolist())          # ground below usually touches the bottom edge
    cand = [(sizes[k], k) for k in range(1, len(sizes)) if k not in edge]
    if not cand:
        return None
    area, k = max(cand)
    if not (0.005 <= area / m.size <= 0.5) or area < 0.6 * sum(c for c, _ in cand):
        return None
    s = lab == k
    outside, _ = _components(~s)             # fill holes not reachable from the border
    border = (set(outside[0].tolist()) | set(outside[-1].tolist())
              | set(outside[:, 0].tolist()) | set(outside[:, -1].tolist()))
    s = s | ~np.isin(outside, list(border - {0}))
    mi = Image.fromarray((s * 255).astype(np.uint8)).resize((w, h), Image.BILINEAR).filter(ImageFilter.MaxFilter(13))
    return np.asarray(mi) > 127


def _fit(still, size):
    from PIL import Image
    im = Image.open(still).convert("RGB")
    W, H = size
    r = max(W / im.width, H / im.height)
    im = im.resize((int(im.width * r + 0.5), int(im.height * r + 0.5)), Image.LANCZOS)
    x0, y0 = (im.width - W) // 2, (im.height - H) // 2
    return im.crop((x0, y0, x0 + W, y0 + H))


def layers(still, out_prefix, log=print, size=(1920, 1080), max_layers=2):
    """Write the parallax layers for one still -> list of paths, back to front.
    max_layers=2 (default): the full picture + its nearer half - people stay
    whole. 3 adds the nearest ~20 %, which can slice a person in two (a head
    drifting from its body), so it is only for empty landscapes."""
    from PIL import Image, ImageFilter
    im = _fit(still, size)
    d = depth_map(im, log)
    p0 = Path(f"{out_prefix}_L0.jpg")
    s = None
    try:
        s = subject_mask(d)
    except Exception as e:  # noqa: BLE001 - fall back to plain depth layers
        log(f"  subject mask failed ({str(e)[:80]}); using 3 depth layers")
    if s is not None:
        hole = Image.fromarray((s * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(21))
        keep = Image.eval(hole, lambda v: 255 - v)
        a = np.asarray(im, dtype=np.float32)
        k = np.asarray(keep, dtype=np.float32)[..., None] / 255

        def bl(x):
            return np.asarray(Image.fromarray(x.clip(0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(40)),
                              dtype=np.float32)
        num = np.stack([bl(a[..., c] * k[..., 0]) for c in range(3)], -1)
        den = bl(k[..., 0] * 255)[..., None] / 255 + 1e-3
        bg = np.where(np.asarray(hole)[..., None] > 127, num / den, a)
        Image.fromarray(bg.clip(0, 255).astype(np.uint8)).save(p0, "JPEG", quality=93)
        fg = im.copy()
        fg.putalpha(Image.fromarray((s * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(3)))
        p1 = Path(f"{out_prefix}_L1.png")
        fg.save(p1)
        log(f"  parallax {Path(still).name}: 2 layers (one rigid subject)")
        return [p0, p1]
    paths = [p0]
    im.save(p0, "JPEG", quality=93)
    for i, q in enumerate((0.55, 0.82)[:max(1, max_layers - 1)], 1):
        thr = float(np.quantile(d, q))
        m = Image.fromarray(((d > thr) * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(9)).filter(
            ImageFilter.GaussianBlur(6))
        lay = im.copy()
        lay.putalpha(m)
        p = Path(f"{out_prefix}_L{i}.png")
        lay.save(p)
        paths.append(p)
    log(f"  parallax {Path(still).name}: {len(paths)} depth layers")
    return paths


if __name__ == "__main__":
    import sys
    print([str(p) for p in layers(sys.argv[1], sys.argv[2])])

"""Ghost-free 2.5D camera moves on a still: every frame is ONE image whose
pixels are shifted by their depth (near things move further than far ones).
Unlike stacked cut-out layers, nothing is ever drawn twice, so there is no
ghosting / double image and no "rift" where a layer edge slides.

render_shot(still, seconds, move, out_mp4)  ->  1920x1080 @ 30 fps, no audio

The depth comes from depth_layers.depth_map (Depth Anything V2 Small, ONNX).
The depth is smoothed and the parallax kept small (about 1 % of the frame), so
edges never tear; the camera move itself (push / pull / pan / rise) is the
same for every pixel.
"""
import subprocess
from pathlib import Path

import cv2
import numpy as np

from depth_layers import _fit, depth_map

W, H, FPS = 1920, 1080, 30
PARALLAX = 0.007     # max shift between nearest and farthest (fraction of width); small = faces never deform
MOVES = {            # camera: (x from,to) (y from,to) in fraction of frame, scale from,to
    "push":      ((0, 0), (0, -0.006), (1.07, 1.15)),
    "pull":      ((0, 0), (-0.006, 0), (1.15, 1.07)),
    "pan-left":  ((0.018, -0.018), (0, 0), (1.12, 1.12)),
    "pan-right": ((-0.018, 0.018), (0, 0), (1.12, 1.12)),
    "rise":      ((0, 0), (0.014, -0.014), (1.11, 1.13)),
}
_depth_cache = {}


def _ease(p):
    return 0.5 - 0.5 * np.cos(np.pi * p)


def _prepare(still, log):
    key = str(still)
    if key not in _depth_cache:
        im = _fit(still, (W, H))
        d = depth_map(im, log)
        d = cv2.GaussianBlur(d, (0, 0), 18)           # smooth: whole people shift together, no tearing at edges
        d = (d - d.mean())                             # centred: mid-depth stays put
        d /= max(1e-6, np.abs(d).max())
        _depth_cache[key] = (cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR), d.astype(np.float32))
    return _depth_cache[key]


def render_shot(still, seconds, move, out_mp4, log=print):
    img, d = _prepare(still, log)
    (x0, x1), (y0, y1), (s0, s1) = MOVES.get(move, MOVES["push"])
    n = max(1, int(round(seconds * FPS)))
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
    cx, cy = W / 2, H / 2
    # direction of the depth shift follows the camera: pans slide sideways,
    # push/pull/rise drift up a little and spread from the centre
    pan = x1 - x0
    out_mp4 = Path(out_mp4)
    ff = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
                           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-an", "-c:v", "libx264",
                           "-preset", "medium", "-crf", "15", "-pix_fmt", "yuv420p", str(out_mp4)],
                          stdin=subprocess.PIPE)
    try:
        for i in range(n):
            p = _ease(i / max(1, n - 1))
            s = s0 + (s1 - s0) * p
            tx = (x0 + (x1 - x0) * p) * W
            ty = (y0 + (y1 - y0) * p) * H
            k = (p - 0.5) * 2                          # -1 .. 1 across the shot
            if abs(pan) > 1e-6:                       # pans: near slides further sideways
                px = -np.sign(pan) * k * PARALLAX * W * d
                py = 0.0
            else:                                      # push / pull / rise: near spreads from the centre
                grow = float(np.sign(s1 - s0))
                px = grow * k * PARALLAX * 0.5 * W * d * (xs - cx) / cx
                py = grow * k * PARALLAX * 0.5 * H * d * (ys - cy) / cy
                if y1 != y0:                           # rise: near drifts a little further vertically
                    py = py - np.sign(y1 - y0) * k * PARALLAX * 0.5 * H * d
            # backward map: output pixel -> source pixel
            mx = (xs - cx - tx) / s + cx - px / s
            my = (ys - cy - ty) / s + cy - py / s
            frame = cv2.remap(img, mx.astype(np.float32), my.astype(np.float32), cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_REFLECT)
            ff.stdin.write(frame.tobytes())
    finally:
        ff.stdin.close()
        ff.wait()
    if ff.returncode != 0 or not out_mp4.exists():
        raise RuntimeError(f"depth warp encode failed for {Path(still).name}")
    return out_mp4


# ---------------------------------------------------------------- framed camera moves
# Real shots: each frame shows a window (rect) of the still; the window moves
# between a start and an end framing aimed at the subject (a face, or a point
# given in the shot list). Depth parallax is added on top, so it never looks flat.
FRAMED = {          # (start width, end width) as a fraction of the frame; path type
    "wide":      (1.00, 0.80, "zoom"),     # establishing: whole picture, clear ease in
    "push":      (1.00, 0.52, "zoom"),     # strong push in to the subject
    "close":     (0.58, 0.40, "zoom"),     # tight on the subject, still moving
    "pull":      (0.45, 1.00, "zoom"),     # start tight on the subject, reveal the scene
    "pan-lr":    (0.60, 0.60, "pan"),      # sweep across 40% of the picture
    "pan-rl":    (0.60, 0.60, "pan"),
    "tilt-down": (0.62, 0.55, "tilt"),     # from the top of the frame down onto the subject
    "dolly":     (1.00, 0.58, "dolly"),    # push in with a sideways drift
    "hold":      (0.42, 0.36, "zoom"),     # tight, slow creep (viewfinder POV)
    "spin":      (0.76, 0.50, "spin"),     # overhead drone: rotate while descending
}
_face = None


def find_focus(img_bgr, d=None):
    """(x, y) in 0..1: centre of the nearest object (top of the depth map,
    ignoring the bottom band where the ground is always near), else centre."""
    if d is None:
        return (0.5, 0.45)
    h = d.shape[0]
    dd = d[: int(h * 0.82)]
    thr = np.quantile(dd, 0.92)
    ys, xs = np.nonzero(dd >= thr)
    if len(xs) < 50:
        return (0.5, 0.45)
    return (float(np.median(xs)) / d.shape[1], float(np.median(ys)) / h * 0.9)


def _window(cx, cy, w):
    """Clamp a window of width w (fraction) centred at (cx, cy) inside the frame."""
    h = w
    x0 = min(max(cx - w / 2, 0.0), 1.0 - w)
    y0 = min(max(cy - h / 2, 0.0), 1.0 - h)
    return x0, y0, w


def _vanish_prep(img, v):
    """v = {"box": [x0,y0,x1,y1] fractions of the 16:9 frame, "t": start s, "len": s}.
    Clean plate by inpainting the box; the figure mask is where the picture
    differs from the clean plate."""
    h, w = img.shape[:2]
    x0, y0, x1, y1 = [int(round(a * b)) for a, b in zip(v["box"], (w, h, w, h))]
    m = np.zeros((h, w), np.uint8)
    m[y0:y1, x0:x1] = 255
    small = cv2.resize(img, (w // 4, h // 4), interpolation=cv2.INTER_AREA)
    sm = cv2.resize(m, (w // 4, h // 4), interpolation=cv2.INTER_NEAREST)
    clean_s = cv2.inpaint(small, sm, 6, cv2.INPAINT_TELEA)
    clean_s = cv2.GaussianBlur(clean_s, (0, 0), 2)
    clean = img.copy()
    up = cv2.resize(clean_s, (w, h), interpolation=cv2.INTER_CUBIC)
    feather = cv2.GaussianBlur(m, (0, 0), 6).astype(np.float32)[..., None] / 255
    clean = (img * (1 - feather) + up * feather).astype(np.uint8)
    diff = np.abs(img.astype(np.int16) - clean.astype(np.int16)).max(2)
    fig = cv2.GaussianBlur(((diff > 18) * 255).astype(np.uint8), (0, 0), 3).astype(np.float32) / 255
    fig *= m.astype(np.float32) / 255
    rng = np.random.default_rng(7)
    noise = cv2.resize(rng.random((h // 70, w // 70)).astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC)
    noise = cv2.GaussianBlur(noise, (0, 0), 1.5)
    noise = (noise - noise.min()) / (noise.max() - noise.min() + 1e-6)
    return dict(clean=clean.astype(np.float32), img=img.astype(np.float32), fig=fig, noise=noise,
                t=float(v.get("t", 1.0)), len=float(v.get("len", 1.6)))


def _vanish_frame(img, van, t):
    """Before t: untouched. Over len: the figure brightens, then dissolves in
    patches into light. After: the clean plate."""
    q = (t - van["t"]) / van["len"]
    if q <= 0:
        return img
    if q >= 1.25:
        return van["clean"].astype(np.uint8)
    glow = np.clip(1 - abs(q - 0.45) / 0.55, 0, 1)                   # light swells, then fades
    gone = np.clip((q * 1.15 - van["noise"]) * 6, 0, 1) * van["fig"]  # patchy dissolve
    edge = np.clip(1 - np.abs(q * 1.15 - van["noise"]) * 6, 0, 1) * van["fig"]
    a = gone[..., None]
    out = van["img"] * (1 - a) + van["clean"] * a
    halo = cv2.GaussianBlur(van["fig"], (0, 0), 14)[..., None]
    out = out + (255 - out) * np.clip(halo * glow * 0.9 + edge[..., None] * 0.8, 0, 1) * np.float32([0.85, 0.95, 1.0])
    return np.clip(out, 0, 255).astype(np.uint8)


def render_framed(still, seconds, move, out_mp4, focus=None, log=print, vanish=None):
    """Rigid camera move on the WHOLE picture: zoom/pan only, no depth warp.
    Every pixel moves together, so faces, legs, lines and windows can never
    bend, ripple or tear. The still is upscaled once (Lanczos) and each frame
    is a single affine resample; no sharpening (it makes fine texture shimmer)."""
    img = cv2.imread(str(still), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"cannot read {still}")
    # cover-fit to 16:9, then work at 2x output size for clean sub-pixel motion
    SW, SH = W * 2, H * 2
    ih, iw = img.shape[:2]
    sc = max(SW / iw, SH / ih)
    img = cv2.resize(img, (int(round(iw * sc)), int(round(ih * sc))), interpolation=cv2.INTER_LANCZOS4)
    oy, ox = (img.shape[0] - SH) // 2, (img.shape[1] - SW) // 2
    img = img[oy:oy + SH, ox:ox + SW]
    van = _vanish_prep(img, vanish) if vanish else None
    fx, fy = focus if focus else (0.5, 0.45)
    w0, w1, kind = FRAMED.get(move, FRAMED["push"])
    n = max(1, int(round(seconds * FPS)))
    ff = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
                           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-an",
                           "-c:v", "libx264", "-preset", "medium", "-crf", "15", "-pix_fmt", "yuv420p", str(out_mp4)],
                          stdin=subprocess.PIPE)
    try:
        for i in range(n):
            p = _ease(i / max(1, n - 1))
            w = w0 + (w1 - w0) * p
            if kind == "pan":
                a, b = (0.0, 1.0) if move == "pan-lr" else (1.0, 0.0)
                cx, cy = (w / 2) + (a + (b - a) * p) * (1 - w), fy
            elif kind == "tilt":
                cx = fx
                cy = (w / 2) + p * (fy - w / 2) if fy > w / 2 else w / 2
            elif kind == "dolly":             # push in while drifting sideways
                cx, cy = fx + (0.5 - p) * 0.08, fy
            else:
                cx, cy = fx, fy
            if kind == "spin":                 # keep a margin so the rotated corners stay inside the picture
                x0, y0, big = _window(cx, cy, w * 1.3)
                x0, y0, ww = x0 + (big - w) / 2, y0 + (big - w) / 2, w
            else:
                x0, y0, ww = _window(cx, cy, w)
            s = 1.0 / ww * (W / SW)            # source px -> output px
            M = np.float32([[s, 0, -x0 * SW * s], [0, s, -y0 * SH * s]])
            if kind == "spin":                 # rotate about the frame centre
                R = cv2.getRotationMatrix2D((W / 2, H / 2), -9.0 * p, 1.0)
                M = (np.vstack([R, [0, 0, 1]]) @ np.vstack([M, [0, 0, 1]]))[:2].astype(np.float32)
            src = _vanish_frame(img, van, i / FPS) if van else img
            frame = cv2.warpAffine(src, M, (W, H), flags=cv2.INTER_LINEAR,
                                   borderMode=cv2.BORDER_REFLECT)
            ff.stdin.write(frame.tobytes())
    finally:
        ff.stdin.close()
        ff.wait()
    if ff.returncode != 0:
        raise RuntimeError(f"framed render failed for {Path(still).name}")
    return out_mp4

"""
hunyuan3d_client.py - image -> textured mesh (GLB) via Hunyuan3D, with a
pluggable backend and a hard timeout. Used by the [3D_ORBIT] scene mode;
ANY failure raises, and the caller falls back to the 2.5D still.

Backends (first available wins; force with FLOW_3D_BACKEND):
  "api"   - POST the image to a hosted/Gradio endpoint: FLOW_HUNYUAN_URL
            (expects JSON {"mesh_b64"| "glb_url" | "mesh_path"} back)
  "local" - run the hy3dgen pipeline inside a local Hunyuan3D-2 checkout:
            FLOW_HUNYUAN_HOME = repo dir (expects venv at .venv or python
            on PATH with the `hy3dgen` package installed)
  "none"  - not configured (caller falls back)

Usage:
    from hunyuan3d_client import image_to_mesh, describe_backend
    glb = image_to_mesh("scene_04.jpg", "out/mesh.glb", timeout_s=900)
"""

import os
import sys
import json
import base64
import shutil
import subprocess


def describe_backend():
    """Human-readable backend status for the build log."""
    api = os.environ.get("FLOW_HUNYUAN_URL", "").strip()
    home = _home()
    forced = os.environ.get("FLOW_3D_BACKEND", "").strip().lower()
    parts =[f"api={'set' if api else 'unset'} ({api or '-'})",
             f"local={'set' if home else 'unset'} ({home or '-'})"]
    if home and os.path.isdir(home):
        parts.append("local-dir=exists")
    else:
        parts.append("local-dir=missing")
    parts.append(f"forced={forced or 'auto'}")
    return "; ".join(parts)


HERE = os.path.dirname(os.path.abspath(__file__))
# the local install made for this project: repo clone + its own venv
DEFAULT_HOME = os.path.join(HERE, "Hunyuan3D-2")
DEFAULT_PYTHON = os.path.join(HERE, ".venv3d", "Scripts", "python.exe")


def _home():
    home = os.environ.get("FLOW_HUNYUAN_HOME", "").strip()
    if not home and os.path.isdir(DEFAULT_HOME):
        home = DEFAULT_HOME
    return home


def _pick_backend():
    api = os.environ.get("FLOW_HUNYUAN_URL", "").strip()
    home = _home()
    forced = os.environ.get("FLOW_3D_BACKEND", "").strip().lower()
    if forced == "api" and api:
        return "api", api
    if forced == "local" and home:
        return "local", home
    if api:
        return "api", api
    if home and os.path.isdir(home):
        return "local", home
    return "none", None


def image_to_mesh(image_path, out_glb, timeout_s=900):
    """Return out_glb on success; raise RuntimeError on any failure
    (unconfigured, HTTP error, timeout, bad output). Never returns None."""
    backend, arg = _pick_backend()
    if backend == "none":
        raise RuntimeError(
            "no Hunyuan3D backend configured - set FLOW_HUNYUAN_URL (hosted "
            "endpoint) or FLOW_HUNYUAN_HOME (local Hunyuan3D-2 checkout); "
            "status: " + describe_backend())
    os.makedirs(os.path.dirname(os.path.abspath(out_glb)) or ".", exist_ok=True)
    if backend == "api":
        return _via_api(image_path, out_glb, arg, timeout_s)
    return _via_local(image_path, out_glb, arg, timeout_s)


def _via_api(image_path, out_glb, url, timeout_s):
    """POST the image as base64 JSON; accept a base64 mesh, a mesh URL, or a
    server-side path (same host share)."""
    import requests
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    print(f"hunyuan3d: api -> {url} (timeout {timeout_s}s)", flush=True)
    r = requests.post(url, json={"image_b64": b64, "format": "glb"},
                      timeout=timeout_s)
    r.raise_for_status()
    try:
        data = r.json()
    except Exception:
        raise RuntimeError(f"api returned non-JSON ({r.headers.get('content-type')})")
    if data.get("mesh_b64"):
        with open(out_glb, "wb") as f:
            f.write(base64.b64decode(data["mesh_b64"]))
    elif data.get("glb_url"):
        import requests as rq2
        g = rq2.get(data["glb_url"], timeout=120)
        g.raise_for_status()
        with open(out_glb, "wb") as f:
            f.write(g.content)
    elif data.get("mesh_path") and os.path.isfile(data["mesh_path"]):
        shutil.copyfile(data["mesh_path"], out_glb)
    else:
        raise RuntimeError(f"api response has no mesh: {json.dumps(data)[:200]}")
    _check_glb(out_glb)
    return out_glb


def _via_local(image_path, out_glb, home, timeout_s):
    """Run hy3dgen inside the Hunyuan3D-2 checkout's python."""
    py = None
    for cand in (os.environ.get("FLOW_HUNYUAN_PYTHON", "").strip(),
                 DEFAULT_PYTHON,
                 os.path.join(home, ".venv", "Scripts", "python.exe"),
                 os.path.join(home, "venv", "Scripts", "python.exe"),
                 os.path.join(home, "env", "Scripts", "python.exe")):
        if os.path.isfile(cand):
            py = cand
            break
    if not py:
        py = shutil.which("python")
    if not py:
        raise RuntimeError(f"no python found for Hunyuan3D-2 at {home}")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "_hy3dgen_run.py")
    cmd = [py, "-u", script, image_path, out_glb, home]
    print(f"hunyuan3d: local -> {home}", flush=True)
    import interlock   # the shared GPU lock (Video Studio's); the mesh process exits, freeing the VRAM, before release
    try:
        with interlock.gpu("Hunyuan3D mesh", log=lambda m: print("hunyuan3d: " + m, flush=True)):
            r = subprocess.run(cmd, cwd=home, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=timeout_s)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"hunyuan3d local run exceeded {timeout_s}s")
    if r.returncode != 0 or not os.path.isfile(out_glb):
        tail = (r.stdout or "")[-400:] + " | " + (r.stderr or "")[-200:]
        raise RuntimeError(f"hunyuan3d local run failed "
                           f"(rc={r.returncode}): {tail}")
    _check_glb(out_glb)
    return out_glb


def _check_glb(path):
    size = os.path.getsize(path)
    if size < 10000:
        raise RuntimeError(f"mesh suspiciously small ({size} bytes)")
    with open(path, "rb") as f:
        magic = f.read(4)
    if magic != b"glTF":
        raise RuntimeError(f"output is not glTF (magic={magic!r})")
    print(f"hunyuan3d: mesh OK ({size // 1024} KB)", flush=True)


if __name__ == "__main__":
    print("backend:", describe_backend())

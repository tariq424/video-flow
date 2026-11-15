"""
blender_motion.py - motion-from-stills renderer using Blender (headless).

SHOT-LIST renderer: every scene gets a DESIGNED SHOT - establishing wide,
dolly-in to close-up, tracking pan, true telephoto zoom (FOV animation),
top view, low-angle look-up, crane-down, hero push - not ambient drift.
The camera is a real perspective camera over the still as a textured plane,
so pans show parallax (near edges sweep faster than the far edge) and the
dolly moves have true depth.

FRAMING CONTRACT: distances are fractions OF THE IMAGE (not of the oversized
plane). zdist(f) places the camera so exactly f of the image width is in
frame: 0.98 = nearly the whole picture, 0.60 = a genuine close-up. The plane
carries 1.42x overscan so pans/tilts never reveal edges.

Sharpness: stills are pre-upscaled to UPSCALE_WIDTH (lanczos + light
sharpen) because Flow stills (1632x912) are below 1080p and camera moves
magnify. After upscaling, max on-screen magnification is ~1.02x.

    render_blender_storyboard(scenes, timeline_out, width, height, fps)
        scenes: [{"image": abs_path, "duration": seconds}]
        writes timeline_out (concat of the per-scene mp4s)

Blender exe: FLOW_BLENDER env or the default install paths.
"""

import os
import sys
import json
import glob
import math
import subprocess
import shutil

math_ceil = math.ceil


BLENDER_CANDIDATES = [
    os.environ.get("FLOW_BLENDER") or "",
    r"C:\Users\Tarri\AppData\Local\Programs\Blender Foundation\blender-5.2.3-windows-x64\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
]

# the shot list - cycled per SUB-SHOT; long scenes are cut into several
# sub-shots with HARD CUTS between them (a 47s scene with one slow move
# reads as "a zoomed image drifting" - cuts are what make shots visible)
SHOTS = ["establishing", "dolly-close", "tracking-pan", "zoom-tele",
         "top-view", "low-angle", "crane-down", "hero-push"]

SUB_SHOT_MAX_S = 14      # one designed shot lasts at most this long
UPSCALE_WIDTH = 2880

BPY_TEMPLATE = r'''
import bpy, json, sys, os, math

argv = sys.argv[sys.argv.index("--") + 1:]
cfg = json.load(open(argv[0], encoding="utf-8"))

bpy.ops.wm.read_homefile(use_empty=True)
scene = bpy.context.scene
scene.render.resolution_x = cfg["width"]
scene.render.resolution_y = cfg["height"]
scene.render.fps = int(cfg["fps"])
scene.render.image_settings.file_format = "JPEG"
scene.render.image_settings.quality = 96
scene.render.use_file_extension = True

# workbench TEXTURE shading: the still shown unlit, exactly as generated
scene.render.engine = "BLENDER_WORKBENCH"
scene.display.shading.color_type = "TEXTURE"
scene.display.shading.light = "FLAT"   # studio light MODULATES textures (-15% luma); flat = 1:1
# Blender 4+/5 default view transform is AgX - it mutes color and darkens
# mids. 'Standard' shows the still 1:1, no film curve.
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"

cam_data = bpy.data.cameras.new("cam")
cam_data.lens = 50
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam

IMG_W, IMG_H = 1.6, 0.9
OVER = 1.42                       # plane overscan (pans/tilts never show edges)
Z2W = 2.0 * math.tan(math.radians(19.8))   # 50mm lens: visible width = z * Z2W

def zdist(frac):
    """camera height placing exactly `frac` of the IMAGE width in frame"""
    return IMG_W * frac / Z2W

OUT = cfg["out_dir"]
for k, sc in enumerate(cfg["scenes"], 1):
    img = bpy.data.images.load(sc["image"])
    iw, ih = img.size

    cover = 1.0 * max(cfg["width"] / cfg["height"] / (iw / ih), 1.0)
    pw, ph = IMG_W * OVER * cover, IMG_H * OVER * cover
    mesh = bpy.data.meshes.new("plane%02d" % k)
    mesh.from_pydata([(-pw, -ph, 0), (pw, -ph, 0), (pw, ph, 0), (-pw, ph, 0)],
                     [], [(0, 1, 2, 3)])
    mesh.update()
    mesh.uv_layers.new()
    uvs = mesh.uv_layers[0].data
    for loop_id, uv in enumerate(uvs):
        uv.uv = [(0, 0), (1, 0), (1, 1), (0, 1)][loop_id]
    obj = bpy.data.objects.new("plane%02d" % k, mesh)
    scene.collection.objects.link(obj)

    mat = bpy.data.materials.new("mat%02d" % k)
    mat.use_nodes = True
    nt = mat.node_tree
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    nt.links.new(tex.outputs["Color"], nt.nodes["Principled BSDF"].inputs["Base Color"])
    obj.data.materials.append(mat)

    f0, f1 = 1, sc["frames"]
    shot = sc["shot"]

    cam_data.lens = 50
    cam.location = (0.0, 0.0, zdist(0.95))
    cam.rotation_euler = (0.0, 0.0, 0.0)
    f_end_loc = None
    f_end_rot = None
    f_end_lens = None

    if shot == "establishing":
        # nearly the whole picture, slow push to settle the framing
        cam.location = (0.0, 0.0, zdist(0.98)); f_end_loc = (0.0, 0.0, zdist(0.86))
    elif shot == "dolly-close":
        # genuine dolly: wide three-quarter start -> real close-up on a region
        cam.location = (-IMG_W * 0.12, IMG_H * 0.10, zdist(0.92))
        f_end_loc = (IMG_W * 0.10, -IMG_H * 0.06, zdist(0.56))
    elif shot == "tracking-pan":
        # lateral track across the image
        xmax = IMG_W * (1 - 0.80) * 0.45
        cam.location = (-xmax, 0.0, zdist(0.80)); f_end_loc = (xmax, 0.0, zdist(0.80))
    elif shot == "zoom-tele":
        # TRUE zoom: optics change, camera does not move
        cam.location = (IMG_W * 0.06, -IMG_H * 0.04, zdist(0.95))
        cam_data.lens = 38; f_end_lens = 88
    elif shot == "top-view":
        # flat, head-on: the still as-is, slow settle
        cam.location = (0.0, 0.0, zdist(1.00)); f_end_loc = (0.0, 0.0, zdist(0.90))
    elif shot == "low-angle":
        # camera low, pitched up: keystone perspective, "billboard from below"
        cam.location = (IMG_W * 0.05, -IMG_H * 0.55, zdist(0.88))
        cam.rotation_euler = (math.radians(6.0), 0.0, 0.0)
        f_end_loc = (-IMG_W * 0.04, -IMG_H * 0.30, zdist(0.72))
        f_end_rot = (math.radians(1.5), 0.0, 0.0)
    elif shot == "crane-down":
        ym = IMG_H * (1 - 0.78) * 0.45
        cam.location = (0.0, ym, zdist(0.78)); f_end_loc = (0.0, -ym, zdist(0.78))
    elif shot == "hero-push":
        # low three-quarter hero: offset, pitched, pushing in
        cam.location = (IMG_W * 0.10, -IMG_H * 0.12, zdist(0.88))
        cam.rotation_euler = (math.radians(4.0), 0.0, math.radians(-2.0))
        f_end_loc = (IMG_W * 0.04, -IMG_H * 0.04, zdist(0.64))
        f_end_rot = (math.radians(1.0), 0.0, math.radians(-0.5))
    else:
        cam.location = (0.0, 0.0, zdist(0.95)); f_end_loc = (0.0, 0.0, zdist(0.80))

    cam.keyframe_insert(data_path="location", frame=f0)
    if f_end_loc is not None:
        cam.location = f_end_loc
        cam.keyframe_insert(data_path="location", frame=f1)
    if f_end_rot is not None:
        cam.keyframe_insert(data_path="rotation_euler", frame=f0)
        cam.rotation_euler = f_end_rot
        cam.keyframe_insert(data_path="rotation_euler", frame=f1)
    if f_end_lens is not None:
        cam_data.keyframe_insert(data_path="lens", frame=f0)
        cam_data.lens = f_end_lens
        cam_data.keyframe_insert(data_path="lens", frame=f1)

    scene.frame_start = f0
    scene.frame_end = f1
    frames_dir = os.path.join(OUT, "frames_%02d" % k)
    os.makedirs(frames_dir, exist_ok=True)
    scene.render.filepath = os.path.join(frames_dir, "frame_")
    print("BLENDER_SCENE_START %d (%s)" % (k, shot), flush=True)
    bpy.ops.render.render(animation=True)
    print("BLENDER_SCENE_DONE %d frames=%d dir=%s"
          % (k, f1 - f0 + 1, frames_dir), flush=True)

json.dump({"ok": True}, open(os.path.join(OUT, "blender_done.json"), "w"))
print("BLENDER_ALL_DONE", flush=True)
'''


def find_blender():
    for cand in BLENDER_CANDIDATES:
        if cand and os.path.isfile(cand):
            return cand
    hits = glob.glob(r"C:\Users\Tarri\AppData\Local\Programs\Blender Foundation\*\blender.exe")
    if hits:
        return sorted(hits)[-1]
    hits = glob.glob(r"C:\Program Files\Blender Foundation\*\blender.exe")
    return sorted(hits)[-1] if hits else None


def render_blender_storyboard(scenes, timeline_out, width=1920, height=1080, fps=30):
    """scenes: [{"image", "duration", "mode"?, "video"?}] -> per-scene clips
    concatenated into timeline_out.

    Modes:
      blender (default) - the still is cut into SUB-SHOTS of <= SUB_SHOT_MAX_S
        with HARD CUTS between designed shots (wide -> dolly -> tele ...),
        so the camera language is visible even on long scenes
      3d_orbit - the still goes through Hunyuan3D -> Blender orbit composited
        over the static still; ANY failure falls back to the blender path
      video - scenes["video"] (scene_NN.mp4) is normalized and spliced as-is

    Raises RuntimeError with the failure reason when Blender is missing or a
    mandatory stage fails."""
    blender = find_blender()
    if not blender:
        raise RuntimeError("blender.exe not found - set FLOW_BLENDER")

    out_dir = os.path.join(os.path.dirname(timeline_out), "blender_work")
    os.makedirs(out_dir, exist_ok=True)
    for old in glob.glob(os.path.join(out_dir, "motion_scene_*.mp4")):
        os.remove(old)

    # upscale pool: every unique still once
    upscaled_map = {}
    for i, s in enumerate(scenes, 1):
        src = os.path.abspath(s["image"])
        if src in upscaled_map:
            continue
        up = os.path.join(out_dir, f"up_{i:02d}.jpg")
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", src,
             "-vf", f"scale={UPSCALE_WIDTH}:-2:flags=lanczos,"
                    f"unsharp=5:5:0.4:3:3:0.0",
             "-q:v", "2", up],
            capture_output=True, text=True)
        upscaled_map[src] = up if (r.returncode == 0 and os.path.isfile(up)) else src
    print(f"blender: {len(upscaled_map)} still(s) upscaled to "
          f"{UPSCALE_WIDTH}px (sharpened)")

    # plan: (kind, payload) per output part, in timeline order
    plan = []            # [("shots", cfg_scene), ("file", path)]
    shot_cursor = 0
    for i, s in enumerate(scenes, 1):
        frames = max(int(round(float(s["duration"]) * fps)), fps // 2)
        mode = (s.get("mode") or "blender").lower()
        image = upscaled_map.get(os.path.abspath(s["image"]),
                                 os.path.abspath(s["image"]))

        if mode == "3d_orbit":
            print(f"scene {i:02d}: [3D_ORBIT] {frames} frames "
                  f"({frames / fps:.1f}s)")
            try:
                from hunyuan3d_client import image_to_mesh, describe_backend
                glb = os.path.join(out_dir, f"mesh_{i:02d}.glb")
                image_to_mesh(s["image"], glb, timeout_s=int(
                    os.environ.get("FLOW_3D_TIMEOUT", "1200")))
                from blender_3d import render_3d_orbit
                part = os.path.join(out_dir, f"motion_scene_{i:02d}.mp4")
                render_3d_orbit(glb, image, part, frames / fps,
                                width=width, height=height, fps=fps)
                plan.append(("file", part))
                continue
            except Exception as e:
                print(f"scene {i:02d}: 3D ORBIT FAILED ({str(e)[:160]}) - "
                      f"falling back to the 2.5D shot list", flush=True)

        if mode == "video":
            vp = s.get("video")
            if vp and os.path.isfile(vp):
                part = os.path.join(out_dir, f"motion_scene_{i:02d}.mp4")
                r = subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error", "-i", vp,
                     "-vf", f"scale={width}:{height}:force_original_aspect_ratio="
                            f"decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                            f"fps={fps}",
                     "-frames:v", str(frames),
                     "-c:v", "libx264", "-preset", "slow", "-crf", "17",
                     "-pix_fmt", "yuv420p", part],
                    capture_output=True, text=True)
                if r.returncode == 0 and os.path.isfile(part):
                    print(f"scene {i:02d}: [VIDEO] spliced {os.path.basename(vp)}")
                    plan.append(("file", part))
                    continue
                print(f"scene {i:02d}: video splice failed ({r.stderr[:120]}) "
                      "- falling back to the 2.5D shot list")
            else:
                print(f"scene {i:02d}: [VIDEO] tag but no scene_{i:02d}.mp4 "
                      "found - falling back to the 2.5D shot list")

        # blender sub-shots: hard cuts every <= SUB_SHOT_MAX_S
        n_sub = max(1, math_ceil(frames / (SUB_SHOT_MAX_S * fps)))
        base = frames // n_sub
        extra = frames - base * n_sub
        print(f"scene {i:02d}: [BLENDER] {n_sub} shot(s) x ~"
              f"{base / fps:.1f}s")
        for j in range(n_sub):
            f = base + (1 if j < extra else 0)
            shot = SHOTS[shot_cursor % len(SHOTS)]
            shot_cursor += 1
            plan.append(("shots", {"image": image, "frames": f,
                                   "shot": shot, "n": len(plan) + 1}))

    # render every "shots" part via one Blender pass
    cfg_scenes = [p[1] for p in plan if p[0] == "shots"]
    if cfg_scenes:
        cfg_path = os.path.join(out_dir, "blender_cfg.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump({"scenes": cfg_scenes, "out_dir": out_dir,
                       "width": width, "height": height, "fps": fps}, f, indent=1)
        bpy_path = os.path.join(out_dir, "blender_build.py")
        with open(bpy_path, "w", encoding="utf-8") as f:
            f.write(BPY_TEMPLATE)

        total_frames = sum(s["frames"] for s in cfg_scenes)
        print(f"blender: {blender}")
        print("blender: shot plan -> " +
              ", ".join(f"{s['n']}:{s['shot']}" for s in cfg_scenes))
        print(f"blender: {len(cfg_scenes)} sub-shot(s), {total_frames} frames "
              f"@ {fps}fps ({total_frames / fps:.2f}s)")
        proc = subprocess.Popen(
            [blender, "-b", "--python", bpy_path, "--", cfg_path],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=out_dir, text=True, encoding="utf-8", errors="replace")
        done_scenes = 0
        for line in proc.stdout:
            line = line.rstrip()
            if line.startswith("BLENDER_SCENE_DONE"):
                done_scenes += 1
                print(f"blender: shot {line.split()[1]} rendered "
                      f"({done_scenes}/{len(cfg_scenes)})", flush=True)
            elif line.startswith("Fra:"):
                continue                  # per-frame spam - keep the log tight
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"blender exited {proc.returncode} "
                               f"({done_scenes}/{len(cfg_scenes)} shots rendered)")

        # encode each sub-shot's JPEG sequence, then clean the frames away
        for sc in cfg_scenes:
            k = sc["n"]
            frames_dir = os.path.join(out_dir, "frames_%02d" % k)
            seq = sorted(glob.glob(os.path.join(frames_dir, "frame_*.jpg")))
            if len(seq) < sc["frames"] - 1:
                raise RuntimeError(f"shot {k}: blender produced {len(seq)} of "
                                   f"{sc['frames']} frames")
            part = os.path.join(out_dir, f"motion_shot_{k:02d}.mp4")
            r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                                "-framerate", str(fps),
                                "-i", os.path.join(frames_dir, "frame_%04d.jpg"),
                                # Blender 5.2's new OCIO config darkens ~15%
                                # (out ~= in^1.15) even under the Standard view
                                # transform; ffmpeg eq gamma brightens as
                                # ^(1/g), so 1.15 restores the source luma
                                "-vf", "eq=gamma=1.15",
                                "-c:v", "libx264", "-preset", "slow", "-crf", "17",
                                "-pix_fmt", "yuv420p", part],
                               capture_output=True, text=True)
            if r.returncode != 0 or not os.path.isfile(part):
                raise RuntimeError(f"shot {k} encode failed: {r.stderr[:300]}")
            shutil.rmtree(frames_dir, ignore_errors=True)

    # assemble the timeline in PLAN order: sub-shot clips and spliced
    # files/orbits interleaved exactly as planned
    ordered = []
    for kind, payload in plan:
        if kind == "file":
            ordered.append(payload)
        else:
            ordered.append(os.path.join(out_dir,
                                        f"motion_shot_{payload['n']:02d}.mp4"))
    missing = [p for p in ordered if not os.path.isfile(p)]
    if missing:
        raise RuntimeError(f"{len(missing)} timeline part(s) missing: "
                           f"{[os.path.basename(m) for m in missing][:5]}")
    list_path = os.path.join(out_dir, "concat_motion.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in ordered:
            f.write("file '" + p.replace("\\", "/") + "'\n")
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat",
                        "-safe", "0", "-i", list_path, "-c", "copy", timeline_out],
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(timeline_out):
        raise RuntimeError(f"ffmpeg concat failed: {r.stderr[:300]}")
    print(f"blender: timeline written -> {timeline_out} "
          f"({os.path.getsize(timeline_out) // (1024 * 1024)} MB)")
    return timeline_out


if __name__ == "__main__":
    # standalone smoke test: ONE short scene with a given shot
    img, out, shot = sys.argv[1], sys.argv[2], (sys.argv[3] if len(sys.argv) > 3 else "dolly-close")
    import tempfile
    work = tempfile.mkdtemp(prefix="bm_")
    import json as _json
    # inline single-scene run through the same pipeline
    scenes = [{"image": os.path.abspath(img), "duration": 4}]
    globals()["SHOTS"] = [shot]
    render_blender_storyboard(scenes, out, width=1280, height=720, fps=30)

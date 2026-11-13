"""
blender_motion.py - motion-from-stills renderer using Blender (headless).

For each scene still it builds a 2.5D rig in bpy: the image as an oversized
textured plane on the XY ground plane and a perspective camera dollying over
it. Real perspective means edges move faster than the center - the depth
feeling flat Ken Burns never has. Motion variants per scene (push-in,
pan-right, pull-out, parallax-l, pan-left, parallax-r) cycle in scene order.

Renders with BLENDER_WORKBENCH in TEXTURE shading: the still is displayed
unlit and untouched (no relighting surprises), CPU-friendly, fast.

    render_blender_storyboard(scenes, timeline_out, width, height, fps)
        scenes: [{"image": abs_path, "duration": seconds, "motion": name}]
        writes timeline_out (concat of the per-scene mp4s)

Engine location is resolved from FLOW_BLENDER env or the default install
paths.
"""

import os
import sys
import json
import glob
import subprocess
import shutil


BLENDER_CANDIDATES = [
    os.environ.get("FLOW_BLENDER") or "",
    r"C:\Users\Tarri\AppData\Local\Programs\Blender Foundation\blender-5.2.3-windows-x64\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
]

# motion variants inside Blender (perspective camera on a textured plane)
MOTIONS_B = ["push-in", "pan-right", "pull-out", "parallax-l", "pan-left", "parallax-r"]

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
scene.render.image_settings.quality = 92
scene.render.use_file_extension = True

# workbench TEXTURE shading: the still shown unlit, exactly as generated
scene.render.engine = "BLENDER_WORKBENCH"
scene.display.shading.color_type = "TEXTURE"

cam_data = bpy.data.cameras.new("cam")
cam_data.lens = 50
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam

OUT = cfg["out_dir"]
import math as _m
Z2W = 2.0 * _m.tan(_m.radians(19.8))   # 50mm lens: visible width = z * Z2W

def dist(pw, frac):
    """camera height for viewing `frac` of the plane width"""
    return pw * frac / Z2W

for k, sc in enumerate(cfg["scenes"], 1):
    img = bpy.data.images.load(sc["image"])
    iw, ih = img.size

    # plane sized to COVER the 16:9 frame with 35% overscan (drift never
    # reveals edges), laid on the XY plane at z=0
    cover = 1.35 * max(cfg["width"] / cfg["height"] / (iw / ih), 1.0)
    pw, ph = 1.6 * cover, 0.9 * cover
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
    m = sc["motion"]
    z_far = dist(pw, 0.92)   # widest: ~92% of the image in frame
    z_near = dist(pw, 0.66)  # tightest: ~66% of the image in frame
    z_mid = dist(pw, 0.76)
    travel = (pw - pw * 0.76) * 0.8   # 80% of the safe pan range
    cam.location = (0.0, 0.0, z_far)
    cam.rotation_euler = (0.0, 0.0, 0.0)
    f_end_rot = None
    if m == "push-in":
        cam.location = (0.0, 0.0, z_far); f_end_loc = (0.0, 0.0, z_near)
    elif m == "pull-out":
        cam.location = (0.0, 0.0, z_near); f_end_loc = (0.0, 0.0, z_far)
    elif m == "pan-right":
        cam.location = (-travel, 0.0, z_mid); f_end_loc = (travel, 0.0, z_mid)
    elif m == "pan-left":
        cam.location = (travel, 0.0, z_mid); f_end_loc = (-travel, 0.0, z_mid)
    elif m == "parallax-l":
        cam.location = (travel * 0.5, 0.0, dist(pw, 0.85))
        f_end_loc = (-travel * 0.35, 0.0, dist(pw, 0.70))
        cam.rotation_euler = (0.0, 0.0, _m.radians(-2.5))
        f_end_rot = (0.0, 0.0, _m.radians(2.5))
    elif m == "parallax-r":
        cam.location = (-travel * 0.5, 0.0, dist(pw, 0.85))
        f_end_loc = (travel * 0.35, 0.0, dist(pw, 0.70))
        cam.rotation_euler = (0.0, 0.0, _m.radians(2.5))
        f_end_rot = (0.0, 0.0, _m.radians(-2.5))
    else:
        cam.location = (0.0, 0.0, z_far); f_end_loc = (0.0, 0.0, z_near)

    cam.keyframe_insert(data_path="location", frame=f0)
    cam.location = f_end_loc
    cam.keyframe_insert(data_path="location", frame=f1)
    if f_end_rot is not None:
        cam.keyframe_insert(data_path="rotation_euler", frame=f0)
        cam.rotation_euler = f_end_rot
        cam.keyframe_insert(data_path="rotation_euler", frame=f1)

    scene.frame_start = f0
    scene.frame_end = f1
    frames_dir = os.path.join(OUT, "frames_%02d" % k)
    os.makedirs(frames_dir, exist_ok=True)
    scene.render.filepath = os.path.join(frames_dir, "frame_")
    print("BLENDER_SCENE_START %d" % k, flush=True)
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
    """scenes: [{"image", "duration", "motion"}] -> renders each scene with a
    perspective camera move, concatenates into timeline_out. Raises
    RuntimeError with the failure reason when Blender is missing or fails."""
    blender = find_blender()
    if not blender:
        raise RuntimeError("blender.exe not found - set FLOW_BLENDER to the exe path")

    out_dir = os.path.join(os.path.dirname(timeline_out), "blender_work")
    os.makedirs(out_dir, exist_ok=True)
    for old in glob.glob(os.path.join(out_dir, "motion_scene_*.mp4")):
        os.remove(old)

    cfg_scenes = []
    for i, s in enumerate(scenes, 1):
        frames = max(int(round(float(s["duration"]) * fps)), fps // 2)
        motion = s.get("motion") if s.get("motion") in MOTIONS_B \
            else MOTIONS_B[(i - 1) % len(MOTIONS_B)]
        cfg_scenes.append({"image": os.path.abspath(s["image"]), "frames": frames,
                           "motion": motion})

    cfg_path = os.path.join(out_dir, "blender_cfg.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump({"scenes": cfg_scenes, "out_dir": out_dir,
                   "width": width, "height": height, "fps": fps}, f, indent=1)
    bpy_path = os.path.join(out_dir, "blender_build.py")
    with open(bpy_path, "w", encoding="utf-8") as f:
        f.write(BPY_TEMPLATE)

    total_frames = sum(s["frames"] for s in cfg_scenes)
    print(f"blender: {blender}")
    print(f"blender: {len(cfg_scenes)} scene(s), {total_frames} frames total "
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
            print(f"blender: scene {line.split()[1]} rendered ({done_scenes}/"
                  f"{len(cfg_scenes)})", flush=True)
        elif line.startswith("Fra:"):
            continue                      # per-frame spam - keep the log tight
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"blender exited {proc.returncode} "
                           f"({done_scenes}/{len(cfg_scenes)} scenes rendered)")

    # encode each scene's JPEG sequence to H.264, then clean the frames away
    parts = []
    for sc in cfg_scenes:
        k = cfg_scenes.index(sc) + 1
        frames_dir = os.path.join(out_dir, "frames_%02d" % k)
        seq = sorted(glob.glob(os.path.join(frames_dir, "frame_*.jpg")))
        if len(seq) < sc["frames"] - 1:
            raise RuntimeError(f"scene {k}: blender produced {len(seq)} of "
                               f"{sc['frames']} frames")
        part = os.path.join(out_dir, f"motion_scene_{k:02d}.mp4")
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                            "-framerate", str(fps),
                            "-i", os.path.join(frames_dir, "frame_%04d.jpg"),
                            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                            "-pix_fmt", "yuv420p", part],
                           capture_output=True, text=True)
        if r.returncode != 0 or not os.path.isfile(part):
            raise RuntimeError(f"scene {k} encode failed: {r.stderr[:300]}")
        parts.append(part)
        shutil.rmtree(frames_dir, ignore_errors=True)

    # same encoder settings everywhere -> plain concat with -c copy is safe
    list_path = os.path.join(out_dir, "concat_motion.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in parts:
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
    # standalone smoke test: render ONE short scene from an image
    img = sys.argv[1]
    out = sys.argv[2]
    render_blender_storyboard(
        [{"image": img, "duration": 3, "motion": "parallax-l"}], out,
        width=1280, height=720, fps=30)

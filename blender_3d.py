"""
blender_3d.py - [3D_ORBIT] renderer: a Hunyuan3D mesh orbiting in front of
the scene's static still.

Renders the mesh orbit with a transparent film (RGBA), then composites it
over the original still - the camera orbits the object while the background
stays static, per the arcology spec. Neutral three-point lighting, EEVEE
(worked headless at ~2.3s warm-up then fast frames).

    render_3d_orbit(glb_path, bg_image, out_mp4, duration_s,
                    width=1920, height=1080, fps=30, turn_deg=270)
"""

import os
import glob
import math
import shutil
import subprocess

from blender_motion import find_blender

BPY_TEMPLATE = r'''
import bpy, json, sys, os, math

argv = sys.argv[sys.argv.index("--") + 1:]
cfg = json.load(open(argv[0], encoding="utf-8"))

bpy.ops.wm.read_homefile(use_empty=True)
scene = bpy.context.scene
scene.render.resolution_x = cfg["width"]
scene.render.resolution_y = cfg["height"]
scene.render.fps = int(cfg["fps"])
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGBA"
scene.render.film_transparent = True      # RGBA: composite over the still
scene.render.engine = "BLENDER_EEVEE"
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"

# import the mesh
before = set(bpy.data.objects)
bpy.ops.import_scene.gltf(filepath=cfg["glb"])
new_objs = [o for o in bpy.data.objects if o not in before]

# single flat sun + soft fill: neutral, no color cast
sun = bpy.data.lights.new("key", "SUN")
sun.energy = 3.0
sun_obj = bpy.data.objects.new("key", sun)
sun_obj.rotation_euler = (math.radians(50), 0, math.radians(30))
scene.collection.objects.link(sun_obj)
fill = bpy.data.lights.new("fill", "AREA")
fill.energy = 400.0
fill.size = 5.0
fill_obj = bpy.data.objects.new("fill", fill)
fill_obj.location = (-4, -3, 3)
fill_obj.rotation_euler = (math.radians(55), 0, math.radians(-50))
scene.collection.objects.link(fill_obj)

# frame the object: bbox center + radius -> orbit radius
mins = [1e9] * 3
maxs = [-1e9] * 3
for o in new_objs:
    if o.type != "MESH":
        continue
    for corner in o.bound_box:
        world = o.matrix_world @ mathutils_vector(corner)
        for i in range(3):
            mins[i] = min(mins[i], world[i])
            maxs[i] = max(maxs[i], world[i])
center = [(mins[i] + maxs[i]) / 2 for i in range(3)]
radius = max(maxs[i] - mins[i] for i in range(3)) / 2
orbit_r = max(radius * 2.6, 1.2)
cam_z = center[2] + radius * 0.35

# orbit: an empty at the object center rotates; the camera tracks it
target = bpy.data.objects.new("target", None)
target.location = center
scene.collection.objects.link(target)
track = cam.constraints.new("TRACK_TO")
track.target = target
track.track_axis = "TRACK_NEGATIVE_Z"
track.up_axis = "UP_Y"

scene.frame_start = 1
scene.frame_end = cfg["frames"]
frames_dir = os.path.join(cfg["out_dir"], "orbit_frames")
os.makedirs(frames_dir, exist_ok=True)
scene.render.filepath = os.path.join(frames_dir, "orbit_")

for f in range(1, cfg["frames"] + 1):
    t = (f - 1) / max(cfg["frames"] - 1, 1)
    angle = math.radians(cfg["turn_deg"] * t)
    target.rotation_euler = (0, 0, angle)
    cam.location = (center[0] + orbit_r * math.sin(angle + math.radians(20)),
                    center[1] - orbit_r * math.cos(angle + math.radians(20)),
                    cam_z)
    cam.keyframe_insert(data_path="location", frame=f)
    if f == 1:
        pass
scene.frame_set(1)
bpy.ops.render.render(animation=True)
print("ORBIT_DONE frames=%d" % cfg["frames"], flush=True)
'''

# mathutils_vector: bound_box corners are 3-float tuples; use mathutils.Vector
BPY_TEMPLATE = BPY_TEMPLATE.replace(
    "import bpy, json, sys, os, math",
    "import bpy, json, sys, os, math\nfrom mathutils import Vector as mathutils_vector")


def render_3d_orbit(glb_path, bg_image, out_mp4, duration_s,
                    width=1920, height=1080, fps=30, turn_deg=270):
    """Orbit the mesh over the static still -> out_mp4. Raises RuntimeError
    with the reason when Blender is missing or a stage fails."""
    blender = find_blender()
    if not blender:
        raise RuntimeError("blender.exe not found - set FLOW_BLENDER")
    out_dir = os.path.join(os.path.dirname(os.path.abspath(out_mp4)), "orbit_work")
    os.makedirs(out_dir, exist_ok=True)
    frames = max(int(round(float(duration_s) * fps)), fps // 2)

    cfg_path = os.path.join(out_dir, "orbit_cfg.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump({"glb": os.path.abspath(glb_path), "frames": frames,
                   "out_dir": out_dir, "width": width, "height": height,
                   "fps": fps, "turn_deg": turn_deg}, f, indent=1)
    bpy_path = os.path.join(out_dir, "orbit_build.py")
    with open(bpy_path, "w", encoding="utf-8") as f:
        f.write(BPY_TEMPLATE)

    proc = subprocess.Popen(
        [blender, "-b", "--python", bpy_path, "--", cfg_path],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        cwd=out_dir, text=True, encoding="utf-8", errors="replace")
    for line in proc.stdout:
        line = line.rstrip()
        if line.startswith(("ORBIT_DONE", "Error", "Traceback")):
            print(f"blender-3d: {line[:160]}", flush=True)
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"orbit render exited {proc.returncode}")

    frames_dir = os.path.join(out_dir, "orbit_frames")
    seq = sorted(glob.glob(os.path.join(frames_dir, "orbit_*.png")))
    if len(seq) < frames - 1:
        raise RuntimeError(f"orbit produced {len(seq)} of {frames} frames")

    # composite: static still underneath, RGBA orbit on top
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-loop", "1", "-framerate", str(fps), "-i", os.path.abspath(bg_image),
         "-framerate", str(fps), "-i", os.path.join(frames_dir, "orbit_%04d.png"),
         "-filter_complex",
         "[0:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d[bg];"
         "[bg][1:v]overlay=0:0:format=auto" % (width, height, width, height),
         "-frames:v", str(frames),
         "-c:v", "libx264", "-preset", "slow", "-crf", "17",
         "-pix_fmt", "yuv420p", out_mp4],
        capture_output=True, text=True)
    if r.returncode != 0 or not os.path.isfile(out_mp4):
        raise RuntimeError(f"orbit composite failed: {r.stderr[:300]}")
    shutil.rmtree(frames_dir, ignore_errors=True)
    print(f"blender-3d: orbit clip -> {out_mp4} "
          f"({os.path.getsize(out_mp4) // 1024} KB)")
    return out_mp4

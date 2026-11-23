"""
blender_3d.py - [3D_ORBIT] renderer: a Hunyuan3D mesh orbiting in front of
the scene's static still.

Renders the mesh orbit with a transparent film (RGBA), then composites it
over the scene's still - the camera orbits the object while the background
stays static. The still is blurred and dimmed (shallow depth of field) so
the original 2D object in it does not compete with the 3D one.

Hunyuan meshes here are shape-only, so the mesh gets a material:
FLOW_3D_MATERIAL = pearl (default, white pearlescent) | clay.
Orbit speed: FLOW_3D_ORBIT_DPS degrees/second (default 10, max 360 total).

    render_3d_orbit(glb_path, bg_image, out_mp4, duration_s,
                    width=1920, height=1080, fps=30, turn_deg=None)
"""

import json
import os
import glob
import math
import shutil
import subprocess

from blender_motion import find_blender

BPY_TEMPLATE = r'''
import bpy, json, sys, os, math
from mathutils import Vector

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
if scene.world is None:
    scene.world = bpy.data.worlds.new("world")
scene.world.color = (0.35, 0.35, 0.38)    # soft ambient fill

# import the mesh
before = set(bpy.data.objects)
bpy.ops.import_scene.gltf(filepath=cfg["glb"])
meshes = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
if not meshes:
    raise SystemExit("ORBIT_FAIL no mesh in glb")

# Hunyuan meshes are shape-only: give them a material
mat = bpy.data.materials.new("hero")
mat.use_nodes = True
bsdf = mat.node_tree.nodes["Principled BSDF"]
if cfg["material"] == "pearl":
    bsdf.inputs["Base Color"].default_value = (0.92, 0.91, 0.89, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.22
    for name, val in (("Coat Weight", 0.6), ("Sheen Weight", 0.35),
                      ("Coat Roughness", 0.08)):
        if name in bsdf.inputs:
            bsdf.inputs[name].default_value = val
else:  # clay
    bsdf.inputs["Base Color"].default_value = (0.62, 0.60, 0.57, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.6
for o in meshes:
    o.data.materials.clear()
    o.data.materials.append(mat)
    for poly in o.data.polygons:
        poly.use_smooth = True

# bounding box -> centre + radius (refresh first: the glTF importer's Y-up
# correction is not in matrix_world until the view layer is evaluated)
bpy.context.view_layer.update()
mins = Vector((1e9, 1e9, 1e9)); maxs = Vector((-1e9, -1e9, -1e9))
for o in meshes:
    for v in o.data.vertices:
        w = o.matrix_world @ v.co
        mins = Vector(map(min, mins, w)); maxs = Vector(map(max, maxs, w))
center = (mins + maxs) / 2
dims = maxs - mins
# what the orbit must keep in frame: the height, and the widest horizontal
# extent the object can turn toward the camera
radius = max(dims.z, math.hypot(dims.x, dims.y)) / 2

# lights: warm key, cool rim, fill
def light(name, kind, energy, loc, size=None, color=(1, 1, 1)):
    d = bpy.data.lights.new(name, kind)
    d.energy = energy; d.color = color
    if size is not None:
        d.size = size
    ob = bpy.data.objects.new(name, d)
    ob.location = center + Vector(loc) * radius
    scene.collection.objects.link(ob)
    return ob
key = light("key", "SUN", 3.5, (2, -2, 3), color=(1.0, 0.96, 0.9))
key.rotation_euler = (math.radians(45), 0, math.radians(35))
rim = light("rim", "AREA", 900 * radius * radius, (-2.5, 3, 2), size=2 * radius, color=(0.85, 0.9, 1.0))
fill = light("fill", "AREA", 300 * radius * radius, (-3, -2, 0.5), size=3 * radius)
light_aim = bpy.data.objects.new("light_aim", None)
light_aim.location = center
scene.collection.objects.link(light_aim)
for ob in (rim, fill):
    tc = ob.constraints.new("TRACK_TO")
    tc.track_axis = "TRACK_NEGATIVE_Z"; tc.up_axis = "UP_Y"
    tc.target = light_aim

# camera on a pivot at the object centre; the pivot spins = orbit
cam_data = bpy.data.cameras.new("cam")
cam_data.lens = 50
cam_data.sensor_width = 36
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
pivot = bpy.data.objects.new("pivot", None)
pivot.location = center
scene.collection.objects.link(pivot)
cam.parent = pivot

half_v = math.atan(36 / (cfg["width"] / cfg["height"]) / 2 / 50)
dist = radius / math.sin(half_v) / cfg["fill"]
elev = math.radians(cfg["elevation_deg"])
cam.location = (0.0, -dist * math.cos(elev), dist * math.sin(elev))
aim = cam.constraints.new("TRACK_TO")
aim.target = pivot
aim.track_axis = "TRACK_NEGATIVE_Z"
aim.up_axis = "UP_Y"

f1 = cfg["frames"]
scene.frame_start, scene.frame_end = 1, f1
pivot.rotation_euler = (0, 0, 0)
pivot.keyframe_insert("rotation_euler", frame=1)
pivot.rotation_euler = (0, 0, math.radians(cfg["turn_deg"]))
pivot.keyframe_insert("rotation_euler", frame=f1)   # default bezier = ease in/out

frames_dir = os.path.join(cfg["out_dir"], "orbit_frames")
os.makedirs(frames_dir, exist_ok=True)
scene.render.filepath = os.path.join(frames_dir, "orbit_")
print("ORBIT_SETUP radius=%.3f dist=%.3f turn=%.0f frames=%d"
      % (radius, dist, cfg["turn_deg"], f1), flush=True)
bpy.ops.render.render(animation=True)
print("ORBIT_DONE frames=%d" % f1, flush=True)
'''


def render_3d_orbit(glb_path, bg_image, out_mp4, duration_s,
                    width=1920, height=1080, fps=30, turn_deg=None):
    """Orbit the mesh over the static still -> out_mp4. Raises RuntimeError
    with the reason when Blender is missing or a stage fails."""
    blender = find_blender()
    if not blender:
        raise RuntimeError("blender.exe not found - set FLOW_BLENDER")
    out_dir = os.path.join(os.path.dirname(os.path.abspath(out_mp4)), "orbit_work")
    os.makedirs(out_dir, exist_ok=True)
    frames = max(int(round(float(duration_s) * fps)), fps // 2)
    if turn_deg is None:
        dps = float(os.environ.get("FLOW_3D_ORBIT_DPS", "10"))
        turn_deg = min(360.0, dps * frames / fps)

    cfg_path = os.path.join(out_dir, "orbit_cfg.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump({"glb": os.path.abspath(glb_path), "frames": frames,
                   "out_dir": out_dir, "width": width, "height": height,
                   "fps": fps, "turn_deg": turn_deg,
                   "material": os.environ.get("FLOW_3D_MATERIAL", "pearl"),
                   "fill": 0.95,            # bounding radius ~95% of half-height
                   "elevation_deg": 18}, f, indent=1)
    bpy_path = os.path.join(out_dir, "orbit_build.py")
    with open(bpy_path, "w", encoding="utf-8") as f:
        f.write(BPY_TEMPLATE)

    import interlock   # the shared GPU lock (Video Studio's) for this orbit render only
    with interlock.gpu("Blender 3D orbit", log=lambda m: print("blender-3d: " + m, flush=True)):
        proc = subprocess.Popen(
            [blender, "-b", "--python", bpy_path, "--", cfg_path],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=out_dir, text=True, encoding="utf-8", errors="replace")
        tail = []
        for line in proc.stdout:
            line = line.rstrip()
            tail = (tail + [line])[-15:]
            if line.startswith(("ORBIT_", "Error", "Traceback")):
                print(f"blender-3d: {line[:160]}", flush=True)
        proc.wait()
    if proc.returncode != 0 or not any(l.startswith("ORBIT_DONE") for l in tail):
        raise RuntimeError(f"orbit render failed (rc={proc.returncode}): "
                           + " | ".join(tail[-6:])[:400])

    frames_dir = os.path.join(out_dir, "orbit_frames")
    seq = sorted(glob.glob(os.path.join(frames_dir, "orbit_*.png")))
    if len(seq) < frames - 1:
        raise RuntimeError(f"orbit produced {len(seq)} of {frames} frames")

    # composite: static still underneath (blurred + dimmed = shallow depth of
    # field, so the still's own 2D object doesn't compete), RGBA orbit on top
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-loop", "1", "-framerate", str(fps), "-i", os.path.abspath(bg_image),
         "-framerate", str(fps), "-i", os.path.join(frames_dir, "orbit_%04d.png"),
         "-filter_complex",
         "[0:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,"
         "gblur=sigma=%s,eq=brightness=-0.08[bg];"
         "[bg][1:v]overlay=0:0:format=auto"
         % (width, height, width, height, os.environ.get("FLOW_3D_BG_BLUR", "18")),
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

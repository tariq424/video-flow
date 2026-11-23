"""
blender_motion.py - motion-from-stills renderer using Blender (headless).

SHOT-LIST renderer: every scene gets a DESIGNED SHOT - establishing wide,
dolly-in to close-up, tracking pan, true telephoto zoom (FOV animation),
top view, low-angle look-up, crane-down, hero push - not ambient drift.
The camera is a real perspective camera over the still as a textured plane,
so pans show parallax (near edges sweep faster than the far edge) and the
dolly moves have true depth.

FRAMING CONTRACT: the plane IS the image (same aspect, no overscan).
zdist(f) places the camera so the frame spans f of the full-fit framing:
1.00 = the whole picture, 0.60 = a close-up. Every start/end keyframe goes
through safe(), which projects the real frame corners (lens + tilt + yaw)
onto the plane and pulls the camera in until no edge can show. Each shot's
actual coverage is logged as "blender: framing ...".

(Before 2026-11-14 the plane's half-width was mistaken for its width and
the texture was stretched over a 1.42x "overscan" plane, so a "98%" wide
shot really showed ~35% of the picture at ~4x blow-up.)

Sharpness: Flow stills are ~1376x768, below 1080p. Upscaling (lanczos +
light sharpen) adds no detail - a 60% close-up is a ~2.3x blow-up of the
source. Use Flow's 2K download for sharper close-ups.

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

from mathutils import Euler, Vector

ASPECT = cfg["width"] / cfg["height"]
SENSOR_W = 36.0                   # Blender default; sensor_fit AUTO = horizontal for 16:9
cam_data.sensor_width = SENSOR_W
cam_data.sensor_fit = "HORIZONTAL"
HW = 0.8                          # plane half-width; the plane IS the image, no overscan

def footprint(loc, rot, lens):
    """the 4 frame corners projected onto the image plane (z=0)"""
    m = Euler(rot, "XYZ").to_matrix()
    sw, sh = SENSOR_W / 2.0, SENSOR_W / ASPECT / 2.0
    pts = []
    for dx, dy in ((-sw, -sh), (sw, -sh), (sw, sh), (-sw, sh)):
        d = m @ Vector((dx, dy, -lens))
        if d.z >= -1e-6:
            return None           # ray never hits the plane
        t = -loc[2] / d.z
        pts.append((loc[0] + t * d.x, loc[1] + t * d.y))
    return pts

def inside(loc, rot, lens):
    pts = footprint(loc, rot, lens)
    return pts is not None and all(abs(x) <= HW + 1e-6 and abs(y) <= HH + 1e-6
                                   for x, y in pts)

def safe(loc, rot, lens):
    """pull the camera toward centre / down until the frame stays on the image"""
    x, y, z = loc
    for _ in range(400):
        if inside((x, y, z), rot, lens):
            return (x, y, z)
        x, y, z = x * 0.95, y * 0.95, z * 0.99
    return (0.0, 0.0, z)

def coverage(loc, rot, lens):
    """fraction of the image WIDTH the frame spans (for the log)"""
    pts = footprint(loc, rot, lens) or [(0, 0)]
    xs = [p[0] for p in pts]
    return (max(xs) - min(xs)) / (2 * HW)

OUT = cfg["out_dir"]
for k, sc in enumerate(cfg["scenes"], 1):
    img = bpy.data.images.load(sc["image"])
    iw, ih = img.size

    HH = HW * ih / iw                         # plane keeps the image's own aspect
    IMG_W, IMG_H = 2 * HW, 2 * HH
    FIT_W = min(IMG_W, IMG_H * ASPECT)        # widest frame that stays on the image

    def zdist(frac, lens=50.0):
        """camera height showing `frac` of the full-fit framing (1.0 = whole picture)"""
        return FIT_W * frac * lens / SENSOR_W

    pw, ph = HW, HH
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

    # the camera is shared by every shot: drop the previous shot's keyframes,
    # or its lens zoom / tilt fcurves silently override this shot's values
    cam.animation_data_clear()
    cam_data.animation_data_clear()
    cam_data.lens = 50
    cam.location = (0.0, 0.0, zdist(0.95))
    cam.rotation_euler = (0.0, 0.0, 0.0)
    f_end_loc = None
    f_end_rot = None
    f_end_lens = None

    # every offset is a fraction of the IMAGE half-size; every distance a
    # fraction of the full-fit framing. safe() then guarantees no edge shows.
    if shot == "establishing":
        # the whole picture, slow push to settle the framing
        cam.location = (0.0, 0.0, zdist(1.00)); f_end_loc = (0.0, 0.0, zdist(0.85))
    elif shot == "dolly-close":
        # wide three-quarter start -> close-up on a region (60% of the frame)
        cam.location = (-HW * 0.06, HH * 0.04, zdist(0.95))
        f_end_loc = (HW * 0.25, -HH * 0.20, zdist(0.60))
    elif shot == "tracking-pan":
        # lateral track edge to edge at 70% framing
        xmax = HW * (1 - 0.70)
        cam.location = (-xmax, 0.0, zdist(0.70)); f_end_loc = (xmax, 0.0, zdist(0.70))
    elif shot == "zoom-tele":
        # TRUE zoom: optics change, camera does not move (whole frame -> 60%)
        cam_data.lens = 38
        cam.location = (HW * 0.05, -HH * 0.05, zdist(1.00, 38))
        f_end_lens = 38 / 0.60
    elif shot == "top-view":
        # flat, head-on: the still as-is, slow settle
        cam.location = (0.0, 0.0, zdist(1.00)); f_end_loc = (0.0, 0.0, zdist(0.92))
    elif shot == "low-angle":
        # camera low, pitched up: keystone perspective, "billboard from below"
        cam.location = (HW * 0.05, -HH * 0.45, zdist(0.70))
        cam.rotation_euler = (math.radians(10.0), 0.0, 0.0)
        f_end_loc = (-HW * 0.05, -HH * 0.15, zdist(0.70))
        f_end_rot = (math.radians(3.0), 0.0, 0.0)
    elif shot == "crane-down":
        # vertical move top -> bottom at 70% framing
        ym = HH * (1 - 0.70)
        cam.location = (0.0, ym, zdist(0.70)); f_end_loc = (0.0, -ym, zdist(0.70))
    elif shot == "hero-push":
        # low three-quarter hero: offset, pitched, yawed, pushing in
        cam.location = (HW * 0.06, -HH * 0.10, zdist(0.72))
        cam.rotation_euler = (math.radians(5.0), 0.0, math.radians(-2.5))
        f_end_loc = (HW * 0.05, -HH * 0.05, zdist(0.50))
        f_end_rot = (math.radians(2.0), 0.0, math.radians(-1.0))
    else:
        cam.location = (0.0, 0.0, zdist(0.95)); f_end_loc = (0.0, 0.0, zdist(0.80))

    lens0 = cam_data.lens
    lens1 = f_end_lens if f_end_lens is not None else lens0
    rot0 = tuple(cam.rotation_euler)
    rot1 = f_end_rot if f_end_rot is not None else rot0
    cam.location = safe(tuple(cam.location), rot0, lens0)
    if f_end_loc is not None:
        f_end_loc = safe(f_end_loc, rot1, lens1)
    end_for_log = f_end_loc if f_end_loc is not None else tuple(cam.location)
    if f_end_loc is None and f_end_lens is not None:
        if not inside(end_for_log, rot1, lens1):     # lens change alone can't leave the image
            lens1 = f_end_lens = lens0
    print("BLENDER_FRAMING %d %s start=%.0f%% end=%.0f%% of image width"
          % (k, shot, 100 * coverage(tuple(cam.location), rot0, lens0),
             100 * coverage(end_for_log, rot1, lens1)), flush=True)

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
    # named by the shot's TIMELINE number (sc["n"]), which is what the encoder
    # looks for - not k, which skips spliced clips / orbits
    frames_dir = os.path.join(OUT, "frames_%02d" % sc["n"])
    os.makedirs(frames_dir, exist_ok=True)
    scene.render.filepath = os.path.join(frames_dir, "frame_")
    print("BLENDER_SCENE_START %d (%s)" % (k, shot), flush=True)
    bpy.ops.render.render(animation=True)
    print("BLENDER_SCENE_DONE %d frames=%d dir=%s"
          % (k, f1 - f0 + 1, frames_dir), flush=True)
    # this shot's plane must not overlap (z-fight with) the next shot's
    bpy.data.objects.remove(obj, do_unlink=True)

json.dump({"ok": True}, open(os.path.join(OUT, "blender_done.json"), "w"))
print("BLENDER_ALL_DONE", flush=True)
'''


def _probe_duration(path):
    """Seconds of a media file (0.0 when ffprobe can't read it)."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


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
        # a scene's own still plus any extra stills ([VIDEO] scenes)
        for k, raw in enumerate([s["image"]] + list(s.get("extras") or [])):
            src = os.path.abspath(raw)
            if src in upscaled_map:
                continue
            up = os.path.join(out_dir, f"up_{i:02d}{'' if k == 0 else chr(97 + k)}.jpg")
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

        continue_from_clip = False
        if mode == "video":
            vp = s.get("video")
            if vp and os.path.isfile(vp):
                part = os.path.join(out_dir, f"motion_scene_{i:02d}.mp4")
                clip_frames = int(_probe_duration(vp) * fps)
                rem = frames - clip_frames
                # a clip at most 2s short: hold its last frame to fill the slot;
                # much shorter: play it whole, then continue with camera shots
                # over its last frame (scene_NN.jpg); longer: trim
                hold = rem > 0 and rem <= 2 * fps
                use = frames if (hold or rem <= 0) else clip_frames
                vf = (f"scale={width}:{height}:force_original_aspect_ratio="
                      f"decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps}")
                if hold:
                    vf += f",tpad=stop_mode=clone:stop={rem + fps}"
                r = subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error", "-i", vp, "-vf", vf,
                     "-frames:v", str(use), "-an",       # narration is the only audio
                     "-c:v", "libx264", "-preset", "slow", "-crf", "17",
                     "-pix_fmt", "yuv420p", part],
                    capture_output=True, text=True)
                if r.returncode == 0 and os.path.isfile(part) and clip_frames > 0:
                    plan.append(("file", part))
                    if use >= frames:
                        how = (f"last frame held {rem / fps:.1f}s" if hold
                               else "trimmed to its slot" if rem < 0 else "exact fit")
                        print(f"scene {i:02d}: [VIDEO] spliced {os.path.basename(vp)} "
                              f"({clip_frames / fps:.1f}s clip, {how})")
                        continue
                    n_extra = len(s.get("extras") or [])
                    print(f"scene {i:02d}: [VIDEO] spliced {os.path.basename(vp)} "
                          f"({clip_frames / fps:.1f}s), then {rem / fps:.1f}s of camera "
                          f"shots over " + (f"{n_extra} extra still(s)" if n_extra
                                            else "its last frame"))
                    frames = rem
                    continue_from_clip = True
                else:
                    print(f"scene {i:02d}: video splice failed ({r.stderr[:120]}) "
                          "- falling back to the 2.5D shot list")
            else:
                print(f"scene {i:02d}: [VIDEO] tag but no scene_{i:02d}.mp4 "
                      "found - falling back to the 2.5D shot list")

        # after a clip, the time left is covered by the scene's extra stills
        # (one or more shots each, hard cuts between them); without extras,
        # by camera shots over the clip's last frame
        extras_up = [upscaled_map.get(os.path.abspath(e), os.path.abspath(e))
                     for e in (s.get("extras") or [])] if continue_from_clip else []

        # blender sub-shots: hard cuts every <= SUB_SHOT_MAX_S
        n_sub = max(1, math_ceil(frames / (SUB_SHOT_MAX_S * fps)), len(extras_up))
        n_sub = min(n_sub, max(1, frames // max(1, fps)))   # no shot under ~1s
        base = frames // n_sub
        extra = frames - base * n_sub
        print(f"scene {i:02d}: [BLENDER] {n_sub} shot(s) x ~"
              f"{base / fps:.1f}s")
        for j in range(n_sub):
            f = base + (1 if j < extra else 0)
            shot_image = extras_up[j % len(extras_up)] if extras_up else image
            if continue_from_clip and j == 0 and not extras_up:
                shot = "establishing"     # starts on the full frame = the clip's end
            else:
                shot = SHOTS[shot_cursor % len(SHOTS)]
                shot_cursor += 1
            plan.append(("shots", {"image": shot_image, "frames": f,
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
        import interlock   # the shared GPU lock (Video Studio's) for this render only; Blender's exit frees the VRAM
        gpu = interlock.gpu("Blender render, %d shot(s)" % len(cfg_scenes), log=lambda m: print("blender: " + m, flush=True))
        done_scenes = 0
        with gpu:
            proc = subprocess.Popen(
                [blender, "-b", "--python", bpy_path, "--", cfg_path],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                cwd=out_dir, text=True, encoding="utf-8", errors="replace")
            try:
                for line in proc.stdout:
                    line = line.rstrip()
                    if line.startswith("BLENDER_SCENE_DONE"):
                        done_scenes += 1
                        print(f"blender: shot {line.split()[1]} rendered "
                              f"({done_scenes}/{len(cfg_scenes)})", flush=True)
                    elif line.startswith("BLENDER_FRAMING"):
                        print("blender: framing " + line.split(" ", 2)[2], flush=True)
                    elif line.startswith("Fra:"):
                        continue                  # per-frame spam - keep the log tight
                proc.wait()
            finally:
                if proc.poll() is None:   # never release the GPU while our Blender still runs on it
                    proc.kill()
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

# video-flow — script parser, Flow batch/import, TTS and motion renderers

No imports from any other project. Consumed by the NAZA Video Builder GUI
(`F:\videos\google-flow-videos\app.py`) via subprocess and import, and usable
standalone. The workflow and script format are documented in
`F:\videos\google-flow-videos\Video_Factory_Spec.md`.

**Flow generation is manual.** The GUI prepares one batch text; the user
pastes it into Google Flow; this project then imports the results. Scripting
Flow's composer gets the session flagged by Google ("unusual activity") —
the automated runners below stop on that flag and are not the recommended path.

## Components

| File | Role |
|---|---|
| `flow_generate.py` | Script parser (`parse_prompts_text`: scenes, `NARRATION:`, tags `[STILL]`/`[BLENDER]`/`[VIDEO]`/`[3D_ORBIT]`, pasted-back batch text converted back), `scene_plan` (the one scene list: number, prompt as written, hash, narration, mode, asset file), batch text (`--prepare-batch`), import (`--import-batch` from a project URL or `--from-folder`), `tts_lines` (one TTS line per scene). Also the older automated runners (`run_batch`, `--agent-batch`). |
| `flow_automation.py` | Playwright/Chrome helpers for flow.google.com: persistent profile, sign-in gate, media discovery (`frames_imgs`, `frames_videos`), validated downloads (JPEG/PNG, `ftyp` MP4/WebM). Session checks: Google's automation flag raises `AutomationFlagged` (stop, record nothing); real content refusals are reported separately. |
| `tts.py` | Edge-TTS (`en-US-ChristopherNeural`, `FLOW_TTS_VOICE` overrides). One line per scene, each voiced and measured separately (`[SILENCE]` = pause), joined into one mp3; per-scene lengths saved as `segments` in `<out>.meta.json`. |
| `blender_motion.py` | Headless Blender renderer. Per scene by mode: 2.5D shot list over the still (plane = the image, every keyframe clamped so no edge shows; ≤14 s sub-shots with hard cuts), `[VIDEO]` clip splice (trim / hold ≤2 s / continue with shots over the last frame; audio dropped), `[3D_ORBIT]` via `blender_3d.py` with fallback to 2.5D. Logs each shot's real coverage (`blender: framing ...`). |
| `blender_3d.py`, `hunyuan3d_client.py`, `_hy3dgen_run.py` | `[3D_ORBIT]`: still → Hunyuan3D-2mv mesh → eased Blender orbit over the blurred still (see *3D orbit setup*). |
| `publish_meta.py` | Publishing metadata by AI (as Video Studio does): `make_meta` has DeepSeek write the title, description, tags, chapters (checked against YouTube's rules) and a thumbnail headline from the timed narration; `make_thumbnail` draws a 1280x720 thumbnail (the video's own picture, dark fade, headline with one highlighted line). Faceless: no owner or channel name. |
| `interlock.py` | Machine-wide job interlocks (OS byte-range locks in `.locks/`, freed when the holder exits; holder shown in `<name>.lock.json`). `workspace`: one factory job at a time. `flow`: one driver of the Flow browser at a time (taken in `flow_automation.attach_or_launch`). `python interlock.py status` shows the holders. |
| `stock_stills.py` | Stock stand-in images for the automated runner when Flow fails (reported as stand-ins, never as successes). |
| `render_storyboard.py`, `remotion/` | The older Remotion Ken Burns renderer (`--engine remotion`). |

## Configuration (env vars)

| Var | Default | Meaning |
|---|---|---|
| `FLOW_PROFILE_DIR` | `F:\code\video-flow\profiles\flow` | Chrome profile used for the URL import |
| `FLOW_OUTPUT_DIR` | `F:\code\video-flow\output` | Staging for downloaded assets |
| `FLOW_TARGET_DIR` | `F:\videos\google-flow-videos` | Default `--target` |
| `FLOW_TTS_VOICE` | `en-US-ChristopherNeural` | Edge-TTS voice |
| `FLOW_TTS_GAP_S` | `3` | Seconds of silence for a scene without narration |
| `FLOW_BLENDER` | Blender 5.2 install | Path to `blender.exe` |
| `FLOW_BUSY_MARKERS` | the video-studio project's Flow profile | Chrome command-line markers for the passive busy monitor |

## Usage

```bash
# sign-in / profile check (submits nothing, spends nothing)
python flow_generate.py --check

# batch text for the scenes that need assets -> agent_batch.txt + scenes_todo.json (+ clipboard)
python flow_generate.py episode_01.txt --target "F:\videos\google-flow-videos" --prepare-batch

# import from a Flow project (same Google account as profiles\flow)
python flow_generate.py episode_01.txt --target "F:\videos\google-flow-videos" --import-batch --project-url https://flow.google.com/project/...

# import from a folder of downloads named in scene order (01.jpg, 02.mp4, ...) - any account
python flow_generate.py episode_01.txt --target "F:\videos\google-flow-videos" --import-batch --from-folder D:\downloads\episode_01

# voice a TTS script (one line per scene) -> narration.mp3 + narration.mp3.meta.json
python tts.py narration_script.txt narration.mp3

# render one still with one shot type (smoke test)
python blender_motion.py still.jpg out.mp4 establishing
```

Imports map images to still scenes and clips to `[VIDEO]` scenes; a
shortfall maps nothing, and a folder file of the wrong kind for its position
stops the import. The command-line imports do not write the GUI's storyboard
or voice the narration — the GUI's *Import Flow stills* does.

## 3D orbit setup ([3D_ORBIT] scenes)

A scene tagged `[3D_ORBIT]` goes still → background cutout → Hunyuan3D mesh
(shape only) → Blender orbit over the blurred still. Any failure falls back
to the 2.5D shot list. ~3–5 min per scene on the RTX 5060 Ti (12 GB VRAM
headroom needed: ~5 GB model + Blender).

| Piece | Where |
|---|---|
| `hunyuan3d_client.py` | picks the backend; local is the default when `Hunyuan3D-2/` exists |
| `_hy3dgen_run.py` | runs inside `.venv3d`: rembg cutout + `Hunyuan3D-2mv` shape pipeline |
| `blender_3d.py` | EEVEE orbit (pearl/clay material, eased turn) + ffmpeg composite |
| `Hunyuan3D-2/` | clone of github.com/Tencent-Hunyuan/Hunyuan3D-2 at `f8db630` (gitignored) |
| `.venv3d/` | Python 3.12 venv, exact versions in `requirements-3d.txt` (gitignored) |
| weights | HF cache `tencent/Hunyuan3D-2mv`, subfolder `hunyuan3d-dit-v2-mv` (9 GB) |

Rebuild from scratch:

```
git clone https://github.com/Tencent-Hunyuan/Hunyuan3D-2 && git -C Hunyuan3D-2 checkout f8db630
py -3.12 -m venv .venv3d
.venv3d\Scripts\python -m pip install -r requirements-3d.txt --extra-index-url https://download.pytorch.org/whl/cu128
```

The `-mv` weights are loaded with `Hunyuan3DDiTFlowMatchingPipeline` and a
single `{"front": image}` view. The texture stage is not used (it needs
custom CUDA extensions that don't build here); the mesh gets a material.

| Var | Default | Meaning |
|---|---|---|
| `FLOW_HUNYUAN_HOME` | `./Hunyuan3D-2` | Hunyuan checkout |
| `FLOW_HUNYUAN_PYTHON` | `./.venv3d/Scripts/python.exe` | its interpreter |
| `FLOW_HUNYUAN_URL` | — | hosted endpoint instead of local |
| `FLOW_3D_TIMEOUT` | `1200` | seconds before falling back to 2.5D |
| `FLOW_3D_MATERIAL` | `pearl` | `pearl` or `clay` |
| `FLOW_3D_ORBIT_DPS` | `10` | orbit speed, degrees/second (max 360 total) |
| `FLOW_3D_BG_BLUR` | `18` | background blur sigma |
| `FLOW_3D_STEPS` / `FLOW_3D_OCTREE` | `30` / `256` | mesh quality vs time |

Works best when the still shows **one clear object** (a tower, a statue, a
vehicle) — the cutout keeps the main subject, and busy effects (sparks,
debris) become noisy geometry.

## Profile sign-in

If the gate reports *signed out*:

```
start chrome --user-data-dir="F:\code\video-flow\profiles\flow" "https://flow.google.com/"
```

Sign in, close that window, run again. **Never commit `profiles/`** — it holds
login cookies (gitignored).

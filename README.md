# video-flow — standalone Google Flow automation + TTS + storyboard renderer

No imports from any other project. Consumed by the NAZA Video Builder GUI
(`F:\videos\google-flow-videos\app.py`) via subprocess, and usable standalone.

## Components

| File | Role |
|---|---|
| `flow_automation.py` | Playwright driver for flow.google.com: persistent Chrome profile (`channel="chrome"`, `headless=False`), sign-in gate, CAPTCHA wait (10 min, solved by hand), popup/"what's new" closing, in-frame composer send (Enter + 12-try send-arrow), cost-card regex `costing\s+(\d+)\s+credits?`, header-validated downloads (`ftyp` MP4/WebM, JPEG/PNG), plain-Approve (never "Always approve") / stills-only-at-0-credits policy, passive busy monitor |
| `flow_generate.py` | CLI batch runner: `<prompts.json|.txt> [--target DIR] [--skip-existing]` or `--check` (sign-in gate only). `.txt` = plain paragraphs → image-only items; `NARRATION:` lines are stripped from prompts and carried separately |
| `tts.py` | Edge-TTS narration (`en-US-ChristopherNeural`, `FLOW_TTS_VOICE` overrides), one retry on transient failures |
| `render_storyboard.py` | Remotion storyboard renderer (Ken Burns + two-layer parallax + crossfades). Long-render flush errors are recovered: a complete output after a non-zero exit is validated (header + size/duration) and accepted; browser-start flakes retry (3 attempts) |
| `remotion/` | The Remotion project (`src/Storyboard.jsx` = the motion engine; `npm install` done) |

## Configuration (env vars)

| Var | Default | Meaning |
|---|---|---|
| `FLOW_PROFILE_DIR` | `F:\code\video-flow\profiles\flow` | Chrome profile used by the automation |
| `FLOW_OUTPUT_DIR` | `F:\code\video-flow\output` | Staging for downloaded assets |
| `FLOW_TARGET_DIR` | `F:\videos\google-flow-videos` | Default `--target` for batch runs |
| `FLOW_TTS_VOICE` | `en-US-ChristopherNeural` | Edge-TTS voice |
| `FLOW_BUSY_MARKERS` | the video-studio project's Flow profile | Semicolon-separated chrome command-line markers for the passive monitor |

## Busy monitor

`wait_until_flow_free()` scans `chrome.exe` command lines (PowerShell CIM) for
Flow automation profile markers and pauses (up to 15 min) while another
automation is driving Flow. Our own profile is always excluded — it can never
deadlock against itself. Manual browsing in a normal Chrome window is not
detected.

## Usage

```bash
# sign-in / profile check (submits nothing, spends nothing)
python flow_generate.py --check

# batch-generate stills into the target folder as scene_NN.jpg
python flow_generate.py episode_01.txt --target "F:\videos\google-flow-videos" --skip-existing

# synthesize narration
python tts.py narration_script.txt narration.mp3

# render a storyboard json directly
python render_storyboard.py storyboard.json out.mp4
```

Prompts `.txt` format: one scene per paragraph; a trailing `NARRATION: <text>`
per paragraph is narration, not prompt. `--skip-existing` keeps existing
`scene_NN.jpg` files (resume).

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

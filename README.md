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

## Profile sign-in

If the gate reports *signed out*:

```
start chrome --user-data-dir="F:\code\video-flow\profiles\flow" "https://flow.google.com/"
```

Sign in, close that window, run again. **Never commit `profiles/`** — it holds
login cookies (gitignored).

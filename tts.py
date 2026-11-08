"""
tts.py - narration synthesis via edge-tts for the video-flow project.

    python tts.py <script.txt> <out.mp3> [voice]

Library use:
    from tts import synth
    synth(["line one.", "line two."], r"F:...\\narration.mp3")

Default voice: en-US-ChristopherNeural (standard deep male documentary
voice). Override with the FLOW_TTS_VOICE env var or the CLI argument.
Requires internet (Microsoft's TTS service).
"""

import asyncio
import os
import sys
import time

DEFAULT_VOICE = os.environ.get("FLOW_TTS_VOICE", "en-US-ChristopherNeural")


async def _save(text, voice, out_path):
    import edge_tts
    tts = edge_tts.Communicate(text, voice=voice)
    await tts.save(out_path)


def synth(lines, out_mp3, voice=None):
    """Synthesize narration lines (joined with spaces) -> out_mp3.
    Retries once on failure (the TTS service drops connections now and then).
    -> the text that was spoken."""
    text = " ".join(l.strip() for l in lines if l and l.strip())
    if not text:
        raise RuntimeError("narration script is empty")
    last_err = None
    for attempt in (1, 2):
        try:
            asyncio.run(_save(text, voice or DEFAULT_VOICE, str(out_mp3)))
            return text
        except Exception as e:
            last_err = e
            if attempt == 1:
                print(f"TTS: {type(e).__name__} - retrying once in 5s ...", flush=True)
                time.sleep(5)
    raise last_err


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: python tts.py <script.txt> <out.mp3> [voice]")
        sys.exit(1)
    script_path, out_path = sys.argv[1], sys.argv[2]
    voice = sys.argv[3] if len(sys.argv) > 3 else None
    with open(script_path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    print(f"TTS: synthesizing {sum(1 for l in lines if l.strip())} line(s) "
          f"with {voice or DEFAULT_VOICE} ...", flush=True)
    text = synth(lines, out_path, voice)
    size = os.path.getsize(out_path) // 1024
    print(f"TTS: done -> {out_path} ({size} KB, {len(text)} chars)", flush=True)

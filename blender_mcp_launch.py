"""Start a Blender window for the Blender MCP, isolated from every other Blender use.

- Own Blender profile (BLENDER_USER_RESOURCES = .blender_mcp_profile/): the
  shared Blender 5.2 preferences and add-ons folder (used by other projects,
  e.g. Video Studio) are never read or written.
- Own add-on copy: the add-on bundled with the pinned mcp-for-blender release
  is copied into that profile only.
- Own port: 9877 (the stock add-on default 9876 is left free for anything else).

The MCP server side is registered for this project with:
  claude mcp add blender -s local -e BLENDER_PORT=9877 -e DISABLE_TELEMETRY=true
      -e BLENDER_MCP_DISABLE_TELEMETRY=true -- uvx mcp-for-blender==2.3.0

Usage:  python blender_mcp_launch.py          (starts Blender, returns at once)
        python blender_mcp_launch.py --check  (only reports whether 9877 answers)
"""
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROFILE = HERE / ".blender_mcp_profile"
ADDON_NAME = "factory_blender_mcp"
PORT = 9877
MCP_PKG = "mcp-for-blender==2.3.0"

sys.path.insert(0, str(HERE))
from blender_motion import find_blender  # noqa: E402  (same Blender the renders use)


def port_open(port=PORT):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def bundled_addon():
    out = subprocess.run(
        ["uvx", "--from", MCP_PKG, "python", "-c",
         "from blender_mcp.addon_manager import get_bundled_addon_path as g; print(g())"],
        capture_output=True, text=True, check=True,
        env={**os.environ, "PYTHONIOENCODING": "utf8"})
    return Path(out.stdout.strip().splitlines()[-1])


def install_addon():
    dst_dir = PROFILE / "scripts" / "addons"
    dst_dir.mkdir(parents=True, exist_ok=True)
    src = bundled_addon()
    dst = dst_dir / f"{ADDON_NAME}.py"
    if not dst.is_file() or dst.read_bytes() != src.read_bytes():
        shutil.copyfile(src, dst)
    return dst


STARTUP = r'''
import bpy, addon_utils
from bpy.app.handlers import persistent
PORT = %d
addon_utils.enable("%s", default_set=True, persistent=True)

def _set_port():
    sc = bpy.context.scene
    if sc is not None:
        sc.blendermcp_port = PORT
        sc.blendermcp_auto_start_server = True

@persistent
def _keep_port(_):
    _set_port()

bpy.app.handlers.load_post.insert(0, _keep_port)
_set_port()
print("factory Blender MCP: add-on enabled, port", PORT)
''' % (PORT, ADDON_NAME)


def launch():
    if port_open():
        print(f"Blender MCP already answering on 127.0.0.1:{PORT}")
        return
    install_addon()
    startup = PROFILE / "factory_startup.py"
    startup.write_text(STARTUP, encoding="utf8")
    env = {**os.environ, "BLENDER_USER_RESOURCES": str(PROFILE)}
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen([find_blender(), "--python", str(startup)], env=env,
                     stdout=open(PROFILE / "blender_mcp.log", "w"), stderr=subprocess.STDOUT,
                     creationflags=flags, close_fds=True)
    print(f"Blender started (profile {PROFILE}); MCP add-on will listen on {PORT}")


if __name__ == "__main__":
    if "--check" in sys.argv:
        print("open" if port_open() else "closed")
    else:
        launch()

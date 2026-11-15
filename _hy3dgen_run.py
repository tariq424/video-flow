"""
_hy3dgen_run.py - runs INSIDE the Hunyuan3D environment (.venv3d python):
    python _hy3dgen_run.py <image_in> <glb_out> <hunyuan_home>

Shape-only image -> mesh with the Hunyuan3D-2mv weights (multi-view DiT,
fed a single "front" view - the only weights in this machine's cache).
The background is removed first: a photographed backdrop otherwise becomes
blob geometry. No texture stage (it needs custom CUDA extensions that do
not build on this Windows/Blackwell setup); the orbit renderer gives the
mesh a material instead.

Kept separate so the video-flow environment never needs torch/hy3dgen.
Env: FLOW_3D_STEPS (30), FLOW_3D_OCTREE (256), FLOW_3D_SEED (1).
"""

import os
import sys
import time


def main():
    image_in, glb_out, home = sys.argv[1], sys.argv[2], sys.argv[3]
    if home not in sys.path:
        sys.path.insert(0, home)
    t0 = time.time()

    import torch
    from PIL import Image
    from hy3dgen.rembg import BackgroundRemover
    from hy3dgen.shapegen import Hunyuan3DDiTFlowMatchingPipeline

    img = Image.open(image_in).convert("RGB")
    img = BackgroundRemover()(img)          # -> RGBA, object only
    img.save(glb_out + ".cutout.png")
    print("HY3D rembg done %.0fs" % (time.time() - t0), flush=True)

    pipe = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
        "tencent/Hunyuan3D-2mv", subfolder="hunyuan3d-dit-v2-mv", variant="fp16")
    print("HY3D model loaded %.0fs" % (time.time() - t0), flush=True)

    mesh = pipe(image={"front": img},
                num_inference_steps=int(os.environ.get("FLOW_3D_STEPS", "30")),
                octree_resolution=int(os.environ.get("FLOW_3D_OCTREE", "256")),
                num_chunks=20000,
                generator=torch.manual_seed(int(os.environ.get("FLOW_3D_SEED", "1"))),
                output_type="trimesh")[0]
    mesh.export(glb_out)
    print("HY3D mesh %d verts %d faces %.0fs" % (len(mesh.vertices), len(mesh.faces),
                                                 time.time() - t0), flush=True)
    print("HY3DGEN_OK", flush=True)


if __name__ == "__main__":
    main()

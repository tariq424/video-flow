"""
_hy3dgen_run.py - runs INSIDE the Hunyuan3D-2 environment (its venv python):
    python _hy3dgen_run.py <image_in> <glb_out> <hunyuan_home>

Loads the standard Hunyuan3D-2 shape pipeline (hy3dgen), generates a mesh
from the image, exports GLB. Kept separate so the video-flow environment
never needs the torch/hy3dgen stack.
"""

import os
import sys


def main():
    image_in, glb_out, home = sys.argv[1], sys.argv[2], sys.argv[3]
    if home not in sys.path:
        sys.path.insert(0, home)
    os.chdir(home)   # hy3dgen resolves model weights relative to the checkout

    from hy3dgen.shapegen import Hunyuan3DDiTFlowPipeline

    pipeline = Hunyuan3DDiTFlowPipeline.from_pretrained("tencent/Hunyuan3D-2")
    pipeline.to("cuda" if os.environ.get("FLOW_3D_CPU", "") != "1" else "cpu")
    mesh = pipeline(image=image_in,
                    num_inference_steps=int(os.environ.get("FLOW_3D_STEPS", "30")),
                    octree_resolution=int(os.environ.get("FLOW_3D_OCTREE", "256")))[0]
    mesh.export(glb_out)
    print("HY3DGEN_OK", flush=True)


if __name__ == "__main__":
    main()

"""
Experiment harness for the BG2 (far background — mountains, clouds, sky) strip.

Generates several wide tiles via image_gen_server's mode="bg" and lays them
side by side into one long strip PNG so we can eyeball whether SD1.5 can
produce usable, seamless-ish panoramic background art before wiring this
into the real pipeline. Not part of the pipeline itself — throwaway/review
script, output goes to output/bg_experiments/.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bg_strip_common import run_strip_experiment

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "bg_experiments")
TILE_W, TILE_H = 768, 384

BG2_PROMPTS = [
    "layered mountain range silhouettes at dawn, soft clouds, distant hills",
    "rolling hills and distant misty mountains under a pale sky",
    "wide open sky with soft rounded cloud shapes, faint mountain ridge on horizon, flat poster illustration",
    "flat mountain skyline with a few clouds drifting, dusk gradient sky",
]


def main():
    run_strip_experiment("bg2", BG2_PROMPTS, OUT_DIR, TILE_W, TILE_H)


if __name__ == "__main__":
    main()

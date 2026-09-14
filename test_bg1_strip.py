"""
Experiment harness for the BG1 (near background — buildings, cars) strip.

Same approach as test_bg2_strip.py but taller tiles (buildings need vertical
extent) and street-level prompt vocabulary. Throwaway/review script — output
goes to output/bg_experiments/.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bg_strip_common import run_strip_experiment

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "bg_experiments")
TILE_W, TILE_H = 768, 512

BG1_PROMPTS = [
    "front view of a row of city building facades along a street, eye-level, simple geometric shapes",
    "street level side view of low buildings and shops with parked cars in front, eye-level view",
    "front elevation of apartment building facades with windows, parked cars at street level, eye-level view",
    "front view of small town shopfronts and streetlamps along a sidewalk, eye-level street scene",
]


def main():
    run_strip_experiment("bg1", BG1_PROMPTS, OUT_DIR, TILE_W, TILE_H)


if __name__ == "__main__":
    main()

"""
Builds the final BG1 (near background — buildings, cars) strip as a
COMPOSITION OF ASSETS:

  Step 1: generate multiple individual building/car assets (isolated cutout,
          mode="subject", rembg-processed transparent PNG)
  Step 2: run every asset through an automated quality gate (bg_strip_common.
          generate_asset_with_gate) that rejects morphed/fragmented/broken
          cutouts and retries — see bg_strip_common._check_asset_quality for
          the rejection criteria (no alpha, cutout ate the subject or did
          nothing, opaque region fragmented into disconnected pieces)
  Step 3: assemble the accepted transparent assets onto one long strip,
          ground-aligned, side by side

Supersedes the earlier painted-tile version of this script (one full scene
per call, no transparency) — that approach is still validated and useful
elsewhere (see build_bg2_variations.py) but the user wants BG1 specifically
built from individually-cut assets. Output: output/bg_experiments/.
"""

import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bg_strip_common import ensure_server_running, generate_asset_with_gate

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "bg_experiments")
ASSET_DIR = os.path.join(OUT_DIR, "bg1_assets")
REJECTED_DIR = os.path.join(OUT_DIR, "bg1_assets_rejected")

STRIP_W, STRIP_H = 4096, 512
GROUND_Y = STRIP_H - 40  # baseline every building/car sits on

BUILDING_PROMPTS = [
    "one whole small city building from roof to ground, front elevation, flat cartoon style, on a plain white background",
    "one whole shopfront building from roof to ground, front elevation, flat cartoon style, on a plain white background",
    "one whole apartment building from roof to ground, front elevation, a few simple windows, flat cartoon style, on a plain white background",
    "one whole small house from roof to ground, front elevation, flat cartoon style, on a plain white background",
    "one whole short office building from roof to ground, front elevation, flat cartoon style, on a plain white background",
    "one whole corner store building from roof to ground, front elevation, flat cartoon style, on a plain white background",
]
CAR_PROMPTS = [
    "one whole car, side view, flat cartoon style, on a plain white background",
    "one whole small car, side view, flat cartoon style, on a plain white background",
]


def build_strip(building_paths, car_paths, out_path):
    """Repeats the accepted building set in a 1-2-3-4-1-2-3-4... cycle to
    densely fill the whole strip width — a handful of unique generations,
    tiled, rather than leaving big gaps or stopping once the unique set runs
    out. Buildings are placed edge-to-edge (assets are already cropped tight
    to their content bbox, so a small fixed gap reads as a real street gap,
    not leftover canvas padding). A car is dropped in front of every other
    building, cycling through the accepted car set the same way."""
    strip = Image.new("RGBA", (STRIP_W, STRIP_H), (0, 0, 0, 0))
    x = 10
    gap = 6
    target_h = 420
    car_h = 90
    building_i = 0
    car_i = 0

    while x < STRIP_W:
        path = building_paths[building_i % len(building_paths)]
        building_i += 1
        asset = Image.open(path).convert("RGBA")
        w = int(asset.width * (target_h / asset.height))
        asset = asset.resize((w, target_h))
        strip.alpha_composite(asset, (x, GROUND_Y - target_h))

        if car_paths and building_i % 2 == 0:
            car_path = car_paths[car_i % len(car_paths)]
            car_i += 1
            car = Image.open(car_path).convert("RGBA")
            car_w = int(car.width * (car_h / car.height))
            car = car.resize((car_w, car_h))
            strip.alpha_composite(car, (x + w - car_w - 8, GROUND_Y - car_h))

        x += w + gap

    strip.save(out_path)
    return out_path


def main():
    os.makedirs(ASSET_DIR, exist_ok=True)
    ensure_server_running()

    accepted_buildings = []
    accepted_cars = []
    rejections = 0

    print("=== Step 1+2: generate + quality-gate building assets ===")
    for prompt in BUILDING_PROMPTS:
        print(f"[building] {prompt}")
        result = generate_asset_with_gate(prompt, ASSET_DIR, REJECTED_DIR)
        if result.accepted:
            accepted_buildings.append(result.path)
        else:
            rejections += 1
            print(f"  ALL ATTEMPTS REJECTED: {result.reason}")

    print("\n=== Step 1+2: generate + quality-gate car assets ===")
    for prompt in CAR_PROMPTS:
        print(f"[car] {prompt}")
        result = generate_asset_with_gate(prompt, ASSET_DIR, REJECTED_DIR)
        if result.accepted:
            accepted_cars.append(result.path)
        else:
            rejections += 1
            print(f"  ALL ATTEMPTS REJECTED: {result.reason}")

    print(f"\n=== Step 3: assemble strip ({len(accepted_buildings)} buildings, "
          f"{len(accepted_cars)} cars accepted, {rejections} prompts fully rejected) ===")
    if not accepted_buildings:
        print("No accepted building assets — nothing to assemble.")
        return

    strip_path = os.path.join(OUT_DIR, "bg1_asset_strip.png")
    build_strip(accepted_buildings, accepted_cars, strip_path)
    print(f"Strip saved: {strip_path}")


if __name__ == "__main__":
    main()

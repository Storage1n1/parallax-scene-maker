"""
Generates multiple candidate variations per BG2 (far background — mountains,
clouds, sky) strip position, using the painted-tile approach validated in
DEV_LOG.md Step 8 (mode="bg", full scene per call, no isolated-cutout
watermark risk from Step 11). Does NOT auto-pick a winner per slot — that
selection is deferred to a later VLM check. Saves every variant plus a
manifest.json and a per-slot contact sheet for quick human/VLM review.

Output: output/bg_experiments/bg2_variations/slot_<i>/variant_<j>.png
        output/bg_experiments/bg2_variations/slot_<i>_contact_sheet.png
        output/bg_experiments/bg2_variations/manifest.json
"""

import json
import os
import sys
import time

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bg_strip_common import generate_tile, ensure_server_running

OUT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "output", "bg_experiments", "bg2_variations"
)
TILE_W, TILE_H = 768, 384
VARIANTS_PER_SLOT = 3

SLOT_PROMPTS = [
    "layered mountain range silhouettes at dawn, soft clouds, distant hills",
    "rolling hills and distant misty mountains under a pale sky",
    "wide open sky with soft rounded cloud shapes, faint mountain ridge on horizon, flat poster illustration",
    "flat mountain skyline with a few clouds drifting, dusk gradient sky",
]


def build_contact_sheet(paths, out_path):
    tiles = [Image.open(p).convert("RGB") for p in paths]
    sheet = Image.new("RGB", (TILE_W * len(tiles), TILE_H))
    for i, tile in enumerate(tiles):
        sheet.paste(tile, (i * TILE_W, 0))
    sheet.save(out_path)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    ensure_server_running()

    manifest = {"tile_size": [TILE_W, TILE_H], "variants_per_slot": VARIANTS_PER_SLOT, "slots": []}
    run_t0 = time.time()

    for slot_i, prompt in enumerate(SLOT_PROMPTS):
        slot_dir = os.path.join(OUT_DIR, f"slot_{slot_i}")
        os.makedirs(slot_dir, exist_ok=True)
        print(f"[slot {slot_i}] {prompt}")

        variant_paths = []
        variant_timings = []
        for v in range(VARIANTS_PER_SLOT):
            out_path = os.path.join(slot_dir, f"variant_{v}.png")
            elapsed = generate_tile(prompt, out_path, TILE_W, TILE_H)
            variant_paths.append(out_path)
            variant_timings.append(elapsed)

        sheet_path = os.path.join(OUT_DIR, f"slot_{slot_i}_contact_sheet.png")
        build_contact_sheet(variant_paths, sheet_path)
        print(f"  contact sheet -> {sheet_path}")

        manifest["slots"].append({
            "slot_index": slot_i,
            "prompt": prompt,
            "variants": [os.path.relpath(p, OUT_DIR) for p in variant_paths],
            "contact_sheet": os.path.relpath(sheet_path, OUT_DIR),
            "timings_sec": variant_timings,
            "selected_variant": None,  # filled in later by the VLM check
        })

    total_elapsed = time.time() - run_t0
    manifest["total_elapsed_sec"] = total_elapsed

    manifest_path = os.path.join(OUT_DIR, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    n_generated = len(SLOT_PROMPTS) * VARIANTS_PER_SLOT
    print(f"\n{n_generated} variants across {len(SLOT_PROMPTS)} slots in {total_elapsed:.1f}s")
    print(f"Manifest: {manifest_path}")
    print("selected_variant is null for every slot — pending later VLM check.")


if __name__ == "__main__":
    main()

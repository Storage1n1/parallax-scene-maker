"""
Experiment harness for BG2 as a COMPOSITION OF ASSETS, not one painted tile.

Corrects the earlier bg2_strip approach: that generated one solid-background
tile per call (baked-in sky, no transparency), which isn't what "composition
of many generated assets" means. This version generates individual isolated
elements (single mountain, single cloud, ...) with a transparent background
via image_gen_server's mode="subject" (isolated cutout, rembg-processed),
same as MG props, then places many of them onto one procedural-gradient sky
canvas to build the strip. Throwaway/review script — output goes to
output/bg_experiments/.
"""

import os
import random
import sys
import time

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from local_gen import generate_asset, ensure_server_running

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "bg_experiments")
ASSET_DIR = os.path.join(OUT_DIR, "bg2_assets")

STRIP_W, STRIP_H = 3072, 384
ASSET_SIZE = 384  # square isolated-cutout generations, scaled down when placed

MOUNTAIN_PROMPTS = [
    "one mountain silhouette shape on a plain white background, no sky, no other elements",
    "one rolling hill silhouette shape on a plain white background, no sky, no other elements",
    "one jagged mountain peak silhouette on a plain white background, no sky, no other elements",
]
CLOUD_PROMPTS = [
    "one fluffy white weather cloud shape on a plain white background, no sky, no other elements",
    "one small wispy weather cloud shape on a plain white background, no sky, no other elements",
]


def sky_gradient(w, h, top=(60, 110, 160), bottom=(230, 200, 170)):
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(w):
            px[x, y] = (r, g, b)
    return img


def check_transparency(path) -> str:
    img = Image.open(path)
    if img.mode != "RGBA":
        return f"NO ALPHA CHANNEL (mode={img.mode})"
    alpha = img.getchannel("A")
    minv, maxv = alpha.getextrema()
    if minv == 255:
        return "NO TRANSPARENT PIXELS (fully opaque)"
    transparent_frac = sum(1 for v in alpha.getdata() if v < 10) / (img.width * img.height)
    return f"OK — {transparent_frac*100:.0f}% fully-transparent pixels"


def main():
    os.makedirs(ASSET_DIR, exist_ok=True)
    ensure_server_running()

    all_prompts = [("mountain", p) for p in MOUNTAIN_PROMPTS] + [("cloud", p) for p in CLOUD_PROMPTS]
    assets = []
    timings = []
    for kind, prompt in all_prompts:
        print(f"[{kind}] {prompt}")
        t0 = time.time()
        path = generate_asset(prompt, ASSET_DIR, steps=25)
        elapsed = time.time() - t0
        status = check_transparency(path)
        print(f"  -> {path} ({elapsed:.1f}s) [{status}]")
        timings.append((prompt, elapsed, status))
        assets.append((kind, path))

    # Build strip: procedural gradient sky + scattered transparent assets
    strip = sky_gradient(STRIP_W, STRIP_H).convert("RGBA")
    random.seed(42)

    mountains = [a for a in assets if a[0] == "mountain"]
    clouds = [a for a in assets if a[0] == "cloud"]

    # mountains along the bottom, overlapping, biggest fixed scale
    x = -100
    while x < STRIP_W:
        kind, path = random.choice(mountains)
        asset = Image.open(path).convert("RGBA")
        scale = random.uniform(0.9, 1.3)
        w = int(ASSET_SIZE * scale)
        h = int(asset.height * (w / asset.width))
        asset = asset.resize((w, h))
        strip.alpha_composite(asset, (x, STRIP_H - h))
        x += int(w * 0.6)

    # clouds scattered in the upper half
    for _ in range(6):
        kind, path = random.choice(clouds)
        asset = Image.open(path).convert("RGBA")
        scale = random.uniform(0.25, 0.45)
        w = int(ASSET_SIZE * scale)
        h = int(asset.height * (w / asset.width))
        asset = asset.resize((w, h))
        cx = random.randint(0, STRIP_W - w)
        cy = random.randint(0, STRIP_H // 2)
        strip.alpha_composite(asset, (cx, cy))

    strip_path = os.path.join(OUT_DIR, "bg2_asset_strip.png")
    strip.convert("RGB").save(strip_path)
    print(f"\nComposited strip saved: {strip_path} ({strip.width}x{strip.height})")

    print("\nTransparency check summary:")
    for prompt, elapsed, status in timings:
        print(f"  {elapsed:5.1f}s  [{status}]  {prompt}")


if __name__ == "__main__":
    main()

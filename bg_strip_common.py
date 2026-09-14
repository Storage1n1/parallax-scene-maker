"""
Shared helpers for the BG2/BG1 strip experiment scripts (test_bg2_strip.py,
test_bg1_strip.py). Generates wide tiles via image_gen_server's mode="bg",
stitches them into one strip, and logs timings.
"""

import os
import time

import numpy as np
import requests
from PIL import Image
from scipy import ndimage

from local_gen import SERVER_URL, ensure_server_running, generate_asset

__all__ = [
    "ensure_server_running", "generate_tile", "run_strip_experiment",
    "generate_asset_with_gate", "QualityGateResult",
]


class QualityGateResult:
    def __init__(self, accepted: bool, reason: str, path: str = None):
        self.accepted = accepted
        self.reason = reason
        self.path = path

    def __repr__(self):
        status = "ACCEPT" if self.accepted else "REJECT"
        return f"{status}: {self.reason}"


def _check_asset_quality(path: str, min_opaque_frac=0.03, max_opaque_frac=0.97,
                          min_component_frac=0.70) -> QualityGateResult:
    """Rejects isolated-cutout assets that are morphed/broken:
    - no alpha channel (rembg didn't run / cutout failed)
    - almost nothing opaque (cutout ate the whole subject) or almost nothing
      transparent (cutout did nothing, background never removed)
    - opaque region is fragmented into scattered blobs instead of one coherent
      subject (the "morphed"/garbled failure mode — multiple disconnected
      pieces means the model produced noise or an incoherent shape, not one
      clean object rembg could isolate)
    """
    img = Image.open(path)
    if img.mode != "RGBA":
        return QualityGateResult(False, f"no alpha channel (mode={img.mode})")

    alpha = np.array(img.getchannel("A"))
    opaque_mask = alpha > 128
    opaque_frac = opaque_mask.mean()

    if opaque_frac < min_opaque_frac:
        return QualityGateResult(False, f"almost nothing opaque ({opaque_frac*100:.1f}%) — cutout ate the subject")
    if opaque_frac > max_opaque_frac:
        return QualityGateResult(False, f"almost nothing transparent ({(1-opaque_frac)*100:.1f}% opaque) — background not removed")

    labeled, n_components = ndimage.label(opaque_mask)
    if n_components == 0:
        return QualityGateResult(False, "no opaque region found")

    component_sizes = ndimage.sum(opaque_mask, labeled, range(1, n_components + 1))
    largest_frac = component_sizes.max() / component_sizes.sum()
    if largest_frac < min_component_frac:
        return QualityGateResult(
            False,
            f"fragmented into {n_components} disconnected pieces, largest is only "
            f"{largest_frac*100:.0f}% of opaque area — likely morphed/incoherent shape",
        )

    return QualityGateResult(True, f"ok ({opaque_frac*100:.0f}% opaque, largest component {largest_frac*100:.0f}%)", path=path)


def _isolate_largest_component_and_crop(path: str, alpha_threshold=128, pad=4):
    """Two cleanup passes on an accepted cutout, applied before edge hardening:
    1. zero out alpha everywhere except the single largest connected opaque
       blob — the quality gate only *measures* fragmentation, it doesn't
       remove the stray noise/speckle pixels rembg leaves outside the main
       subject, so without this those specks stay in the asset.
    2. crop the canvas down to that blob's bounding box (+pad). Without this
       every asset keeps the full generation canvas's empty margin baked in,
       which is what makes assets look "far apart" even when placed with a
       small gap — the gap code sees a much wider transparent image than the
       actual building silhouette.
    Overwrites the file in place.
    """
    img = Image.open(path).convert("RGBA")
    arr = np.array(img)
    opaque_mask = arr[:, :, 3] > alpha_threshold

    labeled, n_components = ndimage.label(opaque_mask)
    if n_components > 1:
        sizes = ndimage.sum(opaque_mask, labeled, range(1, n_components + 1))
        largest_label = int(np.argmax(sizes)) + 1
        keep_mask = labeled == largest_label
        arr[~keep_mask, 3] = 0

    ys, xs = np.where(arr[:, :, 3] > 0)
    if len(xs) == 0:
        Image.fromarray(arr, "RGBA").save(path)
        return
    x0, x1 = max(xs.min() - pad, 0), min(xs.max() + pad + 1, arr.shape[1])
    y0, y1 = max(ys.min() - pad, 0), min(ys.max() + pad + 1, arr.shape[0])
    cropped = Image.fromarray(arr[y0:y1, x0:x1], "RGBA")
    cropped.save(path)


def _harden_edges(path: str, low=60, high=200):
    """rembg sometimes leaves a soft/translucent halo around glossy or
    reflective subjects (glass windows on buildings were the case that
    prompted this) — a doubled-ghost-edge look when composited over other
    layers. Remaps alpha with a threshold ramp (below `low` -> fully
    transparent, above `high` -> fully opaque, linear between) to force a
    crisp matte. Overwrites the file in place."""
    img = Image.open(path).convert("RGBA")
    arr = np.array(img)
    alpha = arr[:, :, 3].astype(np.float32)
    hardened = np.clip((alpha - low) / (high - low), 0, 1) * 255
    arr[:, :, 3] = hardened.astype(np.uint8)
    Image.fromarray(arr, "RGBA").save(path)


def generate_asset_with_gate(prompt: str, out_dir: str, rejected_dir: str, max_attempts: int = 3,
                              harden_edges: bool = True, **quality_kwargs) -> QualityGateResult:
    """Generates an isolated-cutout asset and runs it through the quality gate,
    retrying on rejection up to max_attempts. Rejected attempts are kept in
    rejected_dir (renamed with the attempt number) so failures are inspectable
    rather than silently discarded. Accepted assets get their alpha matte
    hardened (see _harden_edges) to remove soft-halo ghosting by default."""
    os.makedirs(rejected_dir, exist_ok=True)
    last_result = None
    for attempt in range(1, max_attempts + 1):
        path = generate_asset(prompt, out_dir)
        result = _check_asset_quality(path, **quality_kwargs)
        print(f"    attempt {attempt}/{max_attempts}: {result}")
        if result.accepted:
            _isolate_largest_component_and_crop(path)
            if harden_edges:
                _harden_edges(path)
            return result
        rejected_path = os.path.join(
            rejected_dir, f"attempt{attempt}_{os.path.basename(path)}"
        )
        os.replace(path, rejected_path)
        last_result = QualityGateResult(False, result.reason, path=rejected_path)
    return last_result


def generate_tile(prompt: str, out_path: str, width: int, height: int, steps: int = 25) -> float:
    payload = {"prompt": prompt, "mode": "bg", "width": width, "height": height, "steps": steps}
    t0 = time.time()
    r = requests.post(f"{SERVER_URL}/generate", json=payload, timeout=180)
    r.raise_for_status()
    elapsed = time.time() - t0
    with open(out_path, "wb") as f:
        f.write(r.content)
    print(f"  -> {out_path} ({elapsed:.1f}s)")
    return elapsed


def run_strip_experiment(label: str, prompts: list[str], out_dir: str, tile_w: int, tile_h: int):
    """Generates one tile per prompt, stitches them into a strip named
    {label}_strip.png, and appends timings to {out_dir}/timings.log."""
    os.makedirs(out_dir, exist_ok=True)
    ensure_server_running()

    tile_paths = []
    timings = []
    run_t0 = time.time()
    for i, prompt in enumerate(prompts):
        print(f"[{i+1}/{len(prompts)}] {prompt}")
        out_path = os.path.join(out_dir, f"{label}_tile_{i}.png")
        elapsed = generate_tile(prompt, out_path, tile_w, tile_h)
        tile_paths.append(out_path)
        timings.append((prompt, elapsed))
    total_elapsed = time.time() - run_t0

    tiles = [Image.open(p).convert("RGB") for p in tile_paths]
    strip = Image.new("RGB", (tile_w * len(tiles), tile_h))
    for i, tile in enumerate(tiles):
        strip.paste(tile, (i * tile_w, 0))

    strip_path = os.path.join(out_dir, f"{label}_strip.png")
    strip.save(strip_path)
    print(f"\nStrip saved: {strip_path} ({strip.width}x{strip.height})")

    avg = sum(t for _, t in timings) / len(timings)
    print(f"\nTimings: total={total_elapsed:.1f}s, avg/tile={avg:.1f}s, n={len(timings)}")

    timings_path = os.path.join(out_dir, "timings.log")
    with open(timings_path, "a", encoding="utf-8") as f:
        f.write(f"\n=== {label} run {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        f.write(f"tile size: {tile_w}x{tile_h}\n")
        for prompt, elapsed in timings:
            f.write(f"  {elapsed:6.1f}s  {prompt}\n")
        f.write(f"  total={total_elapsed:.1f}s avg={avg:.1f}s n={len(timings)}\n")
    print(f"Timings logged: {timings_path}")

    return strip_path

"""
Orchestrator: scene brief -> asset resolution -> 3D staging -> VLM review loop
-> atmosphere/text finalize -> parallax render.

Phases (see chat plan for the full animator-workflow mapping):
  0. Scene brief -> depth-layer queries + atmosphere preset
  1. Asset resolution (download.py) per layer, transparent-PNG preferred
  2. Staging: place each asset in the 3D scene at its layer's depth
  3. Render camera snapshot
  4. VLM review (vlm_client.py, contract-validated)
  5. Apply fix (reposition, or re-resolve a replacement asset) -> back to 3
     Hard stop at MAX_ITERATIONS regardless of verdict.
  6. Finalize: atmosphere + text, contact sheet, animated parallax render
"""

import json
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "capture"))
sys.path.insert(0, str(ROOT / "vlm"))
sys.path.insert(0, str(ROOT))

from capture import SceneDriver          # noqa: E402
from contact_sheet import build_contact_sheet  # noqa: E402
import downloader                        # noqa: E402
import vlm_client                        # noqa: E402
import camera_choreographer              # noqa: E402

MAX_ITERATIONS = 8
LAYERS = ["background", "midground", "foreground", "subject"]
DEFAULT_LAYOUT = {
    "background": {"x": 0.0, "y": 0.0, "scaleFrac": 1.1},
    "midground": {"x": 0.0, "y": -0.05, "scaleFrac": 0.75},
    "foreground": {"x": 0.0, "y": -0.25, "scaleFrac": 0.5},
    "subject": {"x": 0.0, "y": -0.15, "scaleFrac": 0.55},
}

# scene.js LAYER_DEPTH: background=-900, midground=-450, foreground=-150, subject=0.
# The camera (default position z=600, looking at origin) always stays at a positive
# z — i.e. in front of every layer — so "getting closer" to a layer just means
# dollying z down toward it without ever crossing z=0 and clipping through a plane.
# These are the baseline framing distances for a plain "focus" on each layer;
# zoom_in tightens from there, zoom_out widens.
FOCUS_CAMERA_Z = {
    "background": 1400,
    "midground": 950,
    "foreground": 550,
    "subject": 350,
}
MIN_CAMERA_Z = 250  # never dolly closer than this — avoids clipping into the subject plane


def run(brief: str, atmosphere: dict = None, caption: str = None,
        run_id: str = None, out_root: str = None, headless: bool = True,
        duration_s: float = 3.0, storyboard_scene: dict = None) -> dict:
    """Runs the full pipeline for one scene brief. Returns a summary dict with
    paths to the contact sheet, final render, and the VLM decision log."""
    run_id = run_id or f"run_{int(time.time())}"
    out_root = pathlib.Path(out_root or ROOT / "output")
    assets_dir = out_root / "assets" / run_id
    frames_dir = out_root / "renders" / run_id
    sheets_dir = out_root / "sheets"
    logs_dir = out_root / "logs"
    for d in (assets_dir, frames_dir, sheets_dir, logs_dir):
        d.mkdir(parents=True, exist_ok=True)
    vlm_log_path = logs_dir / f"{run_id}_vlm_decisions.jsonl"

    # Camera motion: prefer the storyboard's camera_moments (an ordered list of
    # {action, target_layer} steps parsed from the Director's Vision narrative —
    # see storyboard_generator.py) when available. That drives a REAL 3D camera
    # dolly (driver.set_camera) toward/away from each layer's actual depth,
    # rather than faking parallax by sliding each layer's own sprite position —
    # camera_choreographer's older ASCII-map-based per-layer system is now only
    # a fallback for when no storyboard was passed in (e.g. plain CLI usage).
    camera_moments = (storyboard_scene or {}).get("camera_moments")
    camera_keyframes = None
    transforms = None
    if camera_moments:
        print(f"[{run_id}] Phase 0 — camera moments (from storyboard): {camera_moments}")
        camera_keyframes = _camera_moments_to_keyframes(camera_moments)
    else:
        print(f"[{run_id}] Phase 0 — generating choreography")
        choreo_result = camera_choreographer.generate_choreography(brief)
        if not choreo_result:
            raise RuntimeError("Failed to generate choreography")
        timeline_data = choreo_result.get("timeline", {})
        transforms = camera_choreographer.generate_3d_transformations(timeline_data)

    # Queries + initial layout: prefer the storyboard's director-generated per-layer
    # query/layout when one was passed in (storyboard_generator.py — richer, scene-
    # specific "visual_context"-informed choices) over the generic per-layer query
    # call.
    story_layout = (storyboard_scene or {}).get("layout", {})
    if storyboard_scene:
        queries = {layer: story_layout.get(layer, {}).get("query", brief) for layer in LAYERS}
        print(f"[{run_id}] layer queries (from storyboard): {queries}")
    else:
        queries = vlm_client.generate_layer_queries(brief, LAYERS)
        print(f"[{run_id}] layer queries: {queries}")

    print(f"[{run_id}] Phase 1 — resolving assets")
    element_assets = {}  # element_id -> resolved asset dict
    for layer in LAYERS:
        query = queries[layer]
        result, used_query = _resolve_with_fallback(query, str(assets_dir))
        if result is None:
            print(f"[{run_id}]   {layer}: NO RESULT for {query!r} (tried simplified variants too) — layer will be left empty")
            continue
        element_assets[f"{layer}1"] = {**result, "layer": layer, "query": used_query}
        got_path = result["transparent_path"] or result["path"]
        note = "" if used_query == query else f" (simplified from {query!r})"
        print(f"[{run_id}]   {layer}: {used_query!r}{note} -> {pathlib.Path(got_path).name}")

    print(f"[{run_id}] Phase 2 — staging scene")
    driver = SceneDriver(headless=headless)
    frame_paths, frame_captions = [], []
    transform_map = {t["layer"].lower(): t for t in transforms} if transforms else {}

    try:
        for element_id, asset in element_assets.items():
            layer_name = asset["layer"].lower()
            t_data = transform_map.get(layer_name, {})
            # Place at from_x, from_y initially (normalized / 100 for screen space)
            x_val = t_data.get("from_x", 0) / 100.0
            y_val = t_data.get("from_y", 0) / 100.0
            
            layout = story_layout.get(asset["layer"]) or DEFAULT_LAYOUT.get(asset["layer"], {"scaleFrac": 1.0})
            image_path = asset["transparent_path"] or asset["path"]
            # Choreographer's from_x/from_y (when present) win for initial placement;
            # otherwise fall back to the storyboard/default layout's x/y.
            if not transform_map.get(layer_name):
                x_val = layout.get("x", x_val)
                y_val = layout.get("y", y_val)
            add_opts = {"x": x_val, "y": y_val, "scaleFrac": layout.get("scaleFrac", 1.0)}
            driver.add_element(element_id, asset["layer"], image_path, **add_opts)

        if camera_keyframes:
            first = camera_keyframes[0]
            driver.set_camera(x=first["x"], y=first["y"], z=first["z"])

        if atmosphere:
            driver.set_atmosphere(**atmosphere)

        print(f"[{run_id}] Phase 3-5 — VLM review loop (max {MAX_ITERATIONS} iterations)")
        final_decision = None
        for iteration in range(1, MAX_ITERATIONS + 1):
            snapshot_path = frames_dir / f"iter_{iteration}.png"
            driver.snapshot(str(snapshot_path))
            state = driver.get_state()

            decision = vlm_client.review_composition(str(snapshot_path), state, brief, str(vlm_log_path))
            final_decision = decision
            note = "; ".join(i.get("note", i["type"]) for i in decision["issues"]) or "-"
            frame_paths.append(str(snapshot_path))
            frame_captions.append(
                f"#{iteration} verdict={decision['verdict']} conf={decision['confidence']:.2f} {note}"
            )
            print(f"[{run_id}]   iter {iteration}: verdict={decision['verdict']} conf={decision['confidence']:.2f} "
                  f"issues={len(decision['issues'])}{' [contract_violation]' if decision.get('contract_violation') else ''}")

            if decision["verdict"] == "ok":
                break
            if decision.get("contract_violation"):
                break  # fail closed — see CONTRACT.md

            _apply_fixes(driver, decision["issues"], element_assets, assets_dir, run_id, transform_map, story_layout)

        print(f"[{run_id}] Phase 6 — finalize")
        if caption:
            driver.set_text("caption", content=caption, x=0, y=-0.85, size=44, color="#ffffff")
        final_render_path = frames_dir / "final.png"
        driver.snapshot(str(final_render_path))

        sheet_path = sheets_dir / f"{run_id}_contact_sheet.png"
        build_contact_sheet(frame_paths, str(sheet_path), frame_captions, title=f"{run_id} — {brief}")

        video_path = None
        try:
            video_path = _render_parallax_video(
                driver, frames_dir, run_id,
                transforms=transforms, camera_keyframes=camera_keyframes, duration_s=duration_s,
            )
        except Exception as e:
            print(f"[{run_id}]   parallax video render skipped: {e}")

    finally:
        driver.close()

    summary = {
        "run_id": run_id,
        "brief": brief,
        "final_verdict": final_decision["verdict"] if final_decision else None,
        "iterations": len(frame_paths),
        "contact_sheet": str(sheet_path),
        "final_render": str(final_render_path),
        "video": video_path,
        "vlm_log": str(vlm_log_path),
        "assets": {eid: {"source": a["source"], "license": a["license"], "page_url": a["page_url"]}
                   for eid, a in element_assets.items()},
    }
    with open(logs_dir / f"{run_id}_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"[{run_id}] done. contact sheet: {sheet_path}")
    return summary


_FILLER_PREFIX_RE = re.compile(
    r"^(the |an? )?(main )?(subject|scene|image|illustration|graphic|background|"
    r"midground|foreground|layer|composition)?\s*(is|shows|showing|depicting|"
    r"displaying|features|of|contains)\b\s*",
    re.IGNORECASE,
)


def _clean_query_text(text: str) -> str:
    """Strips common LLM lead-in filler ("the main subject is an ...", "an image
    showing ...") that otherwise survives to the front of every truncated variant
    and pushes the real subject out of a short query's word budget."""
    cleaned = text.strip().strip("\"'")
    # Filler prefixes can stack (e.g. "The subject is a vector illustration of a
    # dog" — two matches in sequence), so strip repeatedly until it stops matching.
    for _ in range(3):
        new = _FILLER_PREFIX_RE.sub("", cleaned).strip()
        if new == cleaned:
            break
        cleaned = new
    return cleaned.strip(",.;: \"'") or text.strip()


def _resolve_with_fallback(query: str, out_dir: str):
    """Vector-only libraries are far sparser than photo libraries, so a specific
    multi-word LLM-generated query (often a full sentence with filler, e.g. "the
    main subject is an air mattress on the floor") frequently has zero hits.
    Strips filler lead-ins, then tries progressively simpler variants — the
    cleaned full phrase, its first few words, its last few words, its last word —
    before giving up, and reports which query actually landed."""
    cleaned = _clean_query_text(query)
    words = cleaned.split()

    variants = [query]
    if cleaned != query:
        variants.append(cleaned)
    if len(words) > 4:
        variants.append(" ".join(words[:4]))
    if len(words) > 2:
        variants.append(" ".join(words[-2:]))
    if len(words) > 1:
        variants.append(words[-1])

    seen = set()
    for variant in variants:
        variant = variant.strip()
        if not variant or variant.lower() in seen:
            continue
        seen.add(variant.lower())
        result = downloader.resolve_asset(variant, out_dir)  # uses downloader.ACTIVE_SOURCES
        if result is not None:
            return result, variant
    return None, query


def _apply_fixes(driver, issues, element_assets, assets_dir, run_id, transform_map=None, story_layout=None):
    story_layout = story_layout or {}
    for issue in issues:
        target = issue["target"]
        if issue["type"] in ("position", "scale"):
            driver.move_element(target, **issue["move"])
            
            # Step 3B: Sync VLM spatial offset to the keyframes.
            # NOTE: the contract (contract.py) always emits move.dx/dy/dScale, never
            # move.x/y — this previously checked the wrong keys and silently never fired.
            if transform_map and "move" in issue:
                layer_name = target[:-1].lower()  # e.g. "background1" -> "background"
                t_data = transform_map.get(layer_name)
                if t_data:
                    if "dx" in issue["move"]:
                        dx = issue["move"]["dx"] * 100.0
                        t_data["from_x"] = t_data.get("from_x", 0) + dx
                        t_data["to_x"] = t_data.get("to_x", 0) + dx
                    if "dy" in issue["move"]:
                        dy = issue["move"]["dy"] * 100.0
                        t_data["from_y"] = t_data.get("from_y", 0) + dy
                        t_data["to_y"] = t_data.get("to_y", 0) + dy
                        
        elif issue["type"] in ("missing_asset", "wrong_asset"):
            asset = element_assets.get(target)
            if not asset:
                continue
            new_query = issue["replace_query"]
            result, used_query = _resolve_with_fallback(new_query, str(assets_dir))
            if result is None:
                print(f"[{run_id}]   replace failed for {target}: no result for {new_query!r}")
                continue
            layout = story_layout.get(asset["layer"]) or DEFAULT_LAYOUT[asset["layer"]]
            driver.remove_element(target)
            image_path = result["transparent_path"] or result["path"]
            driver.add_element(target, asset["layer"], image_path, **layout)
            element_assets[target] = {**result, "layer": asset["layer"], "query": used_query}
        # depth_order / empty_space / occlusion are informational only in v1 —
        # logged via the note, no automatic scene edit (ambiguous to act on safely).


def _camera_moments_to_keyframes(camera_moments):
    """Converts an ordered [{action, target_layer}, ...] list (see
    storyboard_generator.py) into concrete camera states — one per moment — for
    _render_parallax_video to interpolate between. x/y stay centered; only z
    (dolly distance) varies, since layers are centered by default and z alone
    already produces a convincing zoom/reveal per layer via scene.js's
    visibleSizeAtDepth()-based plane scaling. This drives a real camera dolly
    (driver.set_camera) instead of sliding layer sprites around to fake it."""
    keyframes = []
    for moment in camera_moments:
        layer = moment.get("target_layer", "subject")
        action = moment.get("action", "focus")
        base_z = FOCUS_CAMERA_Z.get(layer, FOCUS_CAMERA_Z["subject"])
        if action == "zoom_in":
            z = base_z - 150
        elif action == "zoom_out":
            z = base_z + 350
        else:  # focus, pan, hold
            z = base_z
        keyframes.append({"x": 0.0, "y": 0.0, "z": max(MIN_CAMERA_Z, z)})
    return keyframes or [{"x": 0.0, "y": 0.0, "z": FOCUS_CAMERA_Z["subject"]}]


def _interpolate_camera(keyframes, t):
    """t in [0, 1] across the whole sequence; keyframes are evenly spaced beats."""
    if len(keyframes) == 1:
        return keyframes[0]
    n = len(keyframes) - 1
    pos = t * n
    idx = min(int(pos), n - 1)
    local_t = pos - idx
    a, b = keyframes[idx], keyframes[idx + 1]
    return {
        "x": a["x"] + (b["x"] - a["x"]) * local_t,
        "y": a["y"] + (b["y"] - a["y"]) * local_t,
        "z": a["z"] + (b["z"] - a["z"]) * local_t,
    }


def _render_parallax_video(driver, frames_dir, run_id, transforms=None, camera_keyframes=None,
                            duration_s=3.0, fps=24):
    """Animates either a real camera dolly (camera_keyframes, preferred — see
    _camera_moments_to_keyframes) or, as a fallback when no storyboard camera
    plan was given, the older per-layer sprite-position animation (transforms,
    from camera_choreographer.py)."""
    import subprocess

    n_frames = int(duration_s * fps)
    seq_dir = frames_dir / "sequence"
    seq_dir.mkdir(exist_ok=True)

    for i in range(n_frames):
        t = i / max(n_frames - 1, 1)  # 0.0 to 1.0

        if camera_keyframes:
            cam = _interpolate_camera(camera_keyframes, t)
            driver.set_camera(x=cam["x"], y=cam["y"], z=cam["z"])
        elif transforms:
            for t_data in transforms:
                layer_name = t_data["layer"].lower()
                # target_id is e.g. 'background1'
                if layer_name in ("bg", "background"):
                    target_id = "background1"
                elif layer_name in ("mg", "midground"):
                    target_id = "midground1"
                elif layer_name in ("fg", "foreground"):
                    target_id = "foreground1"
                elif layer_name in ("su", "subject"):
                    # camera_choreographer's schema currently only ever emits
                    # BG/MG/FG keyframes (never "subject"), so this branch is a
                    # no-op today — kept so a "subject" entry animates correctly
                    # the moment the choreography prompt/schema is extended to
                    # include one, instead of silently falling through to `else`.
                    target_id = "subject1"
                else:
                    continue
                
                cur_x = t_data.get("from_x", 0) + (t_data.get("to_x", 0) - t_data.get("from_x", 0)) * t
                cur_y = t_data.get("from_y", 0) + (t_data.get("to_y", 0) - t_data.get("from_y", 0)) * t
                
                # set_element expects absolute values, normalized / 100
                driver.set_element(target_id, x=cur_x / 100.0, y=cur_y / 100.0)
            
        driver.snapshot(str(seq_dir / f"f_{i:04d}.png"))

    out_path = frames_dir / f"{run_id}_parallax.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-framerate", str(fps), "-i", str(seq_dir / "f_%04d.png"),
         "-pix_fmt", "yuv420p", str(out_path)],
        check=True, capture_output=True,
    )
    return str(out_path)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the parallax scene pipeline for one brief.")
    parser.add_argument("brief", help='Scene brief, e.g. "a misty mountain valley at dawn"')
    parser.add_argument("--caption", default=None)
    parser.add_argument("--headed", action="store_true", help="Show the browser window")
    args = parser.parse_args()

    run(args.brief, caption=args.caption, headless=not args.headed)

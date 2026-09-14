#!/usr/bin/env python3
import argparse
import json
import os
import pathlib
import random
import requests

from PIL import Image, ImageDraw, ImageFont

# Add vlm to path so we can import vlm_client
import sys
ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "vlm"))
sys.path.insert(0, str(ROOT))
import vlm_client

OLLAMA_URL = "http://localhost:11434/api/chat"
LLM_MODEL = "gemma4:e2b"

def _call_llm_json(prompt: str) -> dict:
    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "format": "json",
        "stream": False,
        "think": False,
        "options": {"temperature": 0.3},
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=90)
        r.raise_for_status()
        content = r.json()["message"]["content"].strip()
        # qwen3.5 sometimes wraps JSON in a ```json ... ``` fence despite
        # format:"json" — strip the fence before parsing rather than failing.
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        return json.loads(content)
    except Exception as e:
        print(f"LLM call failed: {e}")
        return {}

VISION_EXAMPLE_BRIEF = "two broke roommates in San Francisco couldn't cover rent"
VISION_EXAMPLE_OUTPUT = (
    "Camera starts on the background — San Francisco buildings, vector-based — "
    "zoomed in, then zooms out revealing two broke roommates standing in the "
    "midground. Camera then moves in to focus on the subject, a single worried "
    "face expressing money stress, while an empty wallet sits in the foreground. "
    "For vector reference we can use: vector images of San Francisco for the "
    "background, vectors of two people for the midground, vectors of an empty "
    "wallet for the foreground, and a vector of a worried face for the subject."
)

CAMERA_ACTIONS = ("zoom_in", "zoom_out", "focus", "pan", "hold")
LAYER_NAMES = ("background", "midground", "foreground", "subject")


def generate_scene_storyboard(brief: str, scene_idx: int) -> dict:
    """Uses advanced Director Workflow to build scene context, assets, layout, and dynamic camera."""

    # Task 1: Director's Vision — one narrative paragraph naming camera moves
    # against layer targets ("camera starts on X, zooms out revealing Y, then
    # focuses on Z") plus explicit vector-reference call-outs per layer
    # ("for vector reference we can use vector images of ..."). This single
    # readable format is what Tasks 2/3 below then parse into structured data —
    # a real example is given since the small model follows a concrete template
    # far more reliably than an abstract instruction alone.
    print(f"  [Scene {scene_idx}] Generating Director's Vision...")
    vision_prompt = f"""Topic: "{brief}"

Answer 4 short questions about this topic, 2-4 words each, plain nouns only:
1. background: what place or setting fits this topic?
2. midground: what object or group fits this topic?
3. foreground: what object fits this topic?
4. subject: what single object or person fits this topic?

Respond ONLY with JSON:
{{ "background": "...", "midground": "...", "foreground": "...", "subject": "..." }}
"""
    vision_res = _call_llm_json(vision_prompt)
    queries_res = {
        name: str(vision_res.get(name, "")).strip() or brief
        for name in LAYER_NAMES
    }
    vision_text = (
        f"Background: {queries_res['background']}. "
        f"Midground: {queries_res['midground']}. "
        f"Foreground: {queries_res['foreground']}. "
        f"Subject: {queries_res['subject']}."
    )

    # Camera move sequence: fixed default rather than LLM-generated — a 4-word
    # noun answer per layer gives the small model nothing to reason about for
    # camera direction, and past attempts to ask it for this anyway produced
    # off-topic or malformed steps. Same default path every scene, still
    # covers all 4 layers in order.
    camera_moments = [
        {"action": "zoom_in", "target_layer": "background"},
        {"action": "zoom_out", "target_layer": "midground"},
        {"action": "focus", "target_layer": "subject"},
    ]

    # Layout (x, y, scaleFrac per layer): fixed defaults, same reasoning as
    # camera_moments above — position doesn't depend on scene content, so
    # don't spend an LLM call (and a chance to hallucinate) on it.
    print(f"  [Scene {scene_idx}] Using default layout...")

    def get_layer(name, dx, dy, ds):
        return {
            "x": dx,
            "y": dy,
            "scaleFrac": ds,
            "query": queries_res[name],
        }

    return {
        "scene": scene_idx,
        "brief": brief,
        "visual_context": vision_text,
        "camera_moments": camera_moments,
        "layout": {
            "background": get_layer("background", 0.0, 0.0, 1.1),
            "midground": get_layer("midground", 0.0, -0.05, 0.75),
            "foreground": get_layer("foreground", 0.0, -0.25, 0.5),
            "subject": get_layer("subject", 0.0, -0.15, 0.55),
        }
    }

def draw_mockup(storyboard: list, out_path: str):
    """Draws a contact sheet with a text header (brief, camera moves, vision) above
    a colored-bounding-box diagram of layer placement, per scene."""
    import textwrap

    W, H = 900, 780
    HEADER_H = 260  # header text lives in its own region — boxes never start above this
    DIAGRAM_H = H - HEADER_H
    cols = 2
    rows = (len(storyboard) + 1) // cols

    sheet_w = cols * W
    sheet_h = rows * H
    sheet = Image.new("RGB", (sheet_w, sheet_h), "white")
    draw = ImageDraw.Draw(sheet)

    try:
        header_font = ImageFont.truetype("arial.ttf", 16)
        label_font = ImageFont.truetype("arial.ttf", 14)
    except Exception:
        header_font = label_font = ImageFont.load_default()

    colors = {
        "background": "lightblue",
        "midground": "lightgreen",
        "foreground": "orange",
        "subject": "pink",
    }

    def draw_wrapped(text, x, y, width_chars, font, line_h, fill="black", max_lines=None):
        lines = textwrap.wrap(text, width=width_chars) or [""]
        if max_lines:
            lines = lines[:max_lines]
        for line in lines:
            draw.text((x, y), line, fill=fill, font=font)
            y += line_h
        return y

    for i, scene in enumerate(storyboard):
        col = i % cols
        row = i // cols
        x_offset = col * W
        y_offset = row * H

        draw.rectangle([x_offset, y_offset, x_offset + W, y_offset + H], outline="black", width=2)
        draw.line([x_offset, y_offset + HEADER_H, x_offset + W, y_offset + HEADER_H], fill="gray", width=1)

        # -- header: brief, camera_moments, visual_context — each in its own wrapped block
        ty = y_offset + 10
        ty = draw_wrapped(f"Scene {scene['scene']}: {scene['brief']}", x_offset + 10, ty, 70, header_font, 20, max_lines=2) + 6

        moments = scene.get("camera_moments", [])
        cam_str = " -> ".join(f"{m.get('action', '?')}({m.get('target_layer', '?')})" for m in moments) or "(none)"
        ty = draw_wrapped(f"Camera: {cam_str}", x_offset + 10, ty, 70, header_font, 20, max_lines=2) + 6

        ctx = textwrap.shorten(scene.get("visual_context", ""), width=280, placeholder="...")
        ty = draw_wrapped(f"Vision: {ctx}", x_offset + 10, ty, 75, label_font, 17, max_lines=6)

        # -- diagram: layer bounding boxes, below the header line
        diagram_top = y_offset + HEADER_H
        center_x = x_offset + W / 2
        center_y = diagram_top + DIAGRAM_H / 2

        for layer, data in scene.get("layout", {}).items():
            lx = data.get("x", 0)
            ly = data.get("y", 0)
            scale = data.get("scaleFrac", 0.5)

            px = center_x + (lx * W / 2)
            py = center_y + (ly * DIAGRAM_H / 2)

            box_w = W * scale * 0.6
            box_h = DIAGRAM_H * scale * 0.6

            x0, y0 = px - box_w / 2, py - box_h / 2
            x1, y1 = px + box_w / 2, py + box_h / 2

            draw.rectangle([x0, y0, x1, y1], outline=colors.get(layer, "black"), width=3)
            draw_wrapped(f"{layer}: {data.get('query', '')}", x0 + 4, y0 + 4, 24, label_font, 15, max_lines=3)

    sheet.save(out_path)
    print(f"Saved visual mockup to {out_path}")

def main():
    parser = argparse.ArgumentParser(description="Generate a storyboard JSON and visual mockup from a script.")
    parser.add_argument("script_file", help="Text file with one scene brief per line")
    parser.add_argument("--out", default="output/storyboard", help="Output directory")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    
    with open(args.script_file, "r", encoding="utf-8") as f:
        briefs = [line.strip() for line in f if line.strip()]
        
    print(f"Generating storyboard for {len(briefs)} scenes...")
    
    storyboard = []
    for i, brief in enumerate(briefs, 1):
        print(f"Processing Scene {i}...")
        scene_data = generate_scene_storyboard(brief, i)
        storyboard.append(scene_data)
        
    json_path = os.path.join(args.out, "storyboard.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(storyboard, f, indent=2)
    print(f"Saved storyboard JSON to {json_path}")
    
    mockup_path = os.path.join(args.out, "mockup.png")
    draw_mockup(storyboard, mockup_path)

if __name__ == "__main__":
    main()

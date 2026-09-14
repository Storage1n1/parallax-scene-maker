"""
Ollama client for the composition-review VLM. Wraps a snapshot + scene state
into a contract-constrained prompt, calls qwen3.5:0.8B (vision), validates the
response against contract.py, and appends every turn to the audit log.
"""

import base64
import json
import pathlib
import time

import requests

from contract import ContractViolation, extract_json, fallback_decision, validate

OLLAMA_URL = "http://localhost:11434/api/chat"
VLM_MODEL = "qwen3.5:0.8B"
LLM_MODEL = "qwen3.5:0.8B"  # same model doubles as the main text LLM (query generation, briefs)

SYSTEM_PROMPT = """You are a film compositing director reviewing a parallax scene render.
You will be shown one image (the current camera snapshot) and a JSON list of the
elements currently in the scene with their layer, position (x,y in [-1,1], 0=center)
and scale.

Judge the composition: is it balanced, is the subject readable, is anything missing,
misplaced, wrong-looking, or badly scaled, does depth order look right (background
behind midground behind foreground behind subject)?

Respond with ONLY a single JSON object, no markdown, no explanation outside the JSON:
{
  "verdict": "ok" or "fix",
  "confidence": 0.0 to 1.0,
  "issues": [
    {
      "type": "position" | "scale" | "depth_order" | "missing_asset" | "wrong_asset" | "empty_space" | "occlusion",
      "target": "<element id from the list>",
      "move": {"dx": -0.3..0.3, "dy": -0.3..0.3, "dScale": -0.2..0.2},
      "replace_query": "<new search query, only for missing_asset/wrong_asset>",
      "note": "short reason"
    }
  ]
}
"move" is required for type position/scale. "replace_query" is required for
type missing_asset/wrong_asset. Only include fields relevant to the issue type.
If the composition looks good, return verdict "ok" with an empty issues list."""

REPAIR_SUFFIX = "\n\nYour previous response was not valid JSON matching the schema. Return ONLY the JSON object, nothing else."


def _encode_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _call_ollama(prompt: str, image_path: str, retries: int = 2, timeout: int = 120) -> str:
    payload = {
        "model": VLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt, "images": [_encode_image(image_path)]},
        ],
        "format": "json",
        "stream": False,
        "think": False,  # qwen3.5's default reasoning trace runs to thousands of tokens
                         # per call and adds nothing the JSON contract needs — disable it
        "options": {"temperature": 0.2},
    }
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
            r.raise_for_status()
            return r.json()["message"]["content"]
        except (requests.RequestException, KeyError) as e:
            last_err = e
            time.sleep(1.5)
    raise RuntimeError(f"Ollama call failed after {retries} attempts: {last_err}")


def review_composition(image_path: str, scene_state: dict, brief: str, log_path: str) -> dict:
    """Calls the VLM once (with one repair retry on contract violation), validates,
    logs the full turn, and returns a contract-valid decision dict."""
    element_summary = [
        {"id": e["id"], "layer": e["layer"], "x": e["x"], "y": e["y"], "scale": e["scaleFrac"]}
        for e in scene_state["elements"]
    ]
    known_ids = {e["id"] for e in element_summary}

    prompt = (
        f"Scene brief: {brief}\n"
        f"Current elements: {json.dumps(element_summary)}\n"
        f"Camera: {json.dumps(scene_state['camera'])}"
    )

    decision = None
    raw = None
    violation_reason = None

    for pass_num in range(2):
        this_prompt = prompt if pass_num == 0 else prompt + REPAIR_SUFFIX
        try:
            raw = _call_ollama(this_prompt, image_path)
            decision = validate(raw, known_ids)
            break
        except ContractViolation as e:
            violation_reason = str(e)
            continue
        except RuntimeError as e:
            violation_reason = str(e)
            break

    if decision is None:
        decision = fallback_decision()
        decision["violation_reason"] = violation_reason

    _log_turn(log_path, image_path, prompt, raw, decision)
    return decision


def _log_turn(log_path, image_path, prompt, raw, decision):
    entry = {
        "timestamp": time.time(),
        "image": str(image_path),
        "prompt": prompt,
        "raw_response": raw,
        "decision": decision,
    }
    pathlib.Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


ASSET_CHECK_PROMPT = """You are a QA reviewer for a clip-art asset library used in vector motion
graphics. You will be shown one candidate image that was downloaded for the search query
"{query}" (intended to depict/represent: {intent}).

Answer honestly, even if it means rejecting the image:
- Does the image actually depict or clearly represent that subject?
- Is it a clean, generic clip-art/vector-style illustration — NOT a corporate/brand logo,
  a flag, a coat of arms, a map, a diagram, a chart, a screenshot, or a photo of a real
  person/place/event?
- Is it free of any text/watermark/company name that would look out of place in an
  unrelated scene?

Respond with ONLY a single JSON object:
{{
  "depicts_subject": true or false,
  "is_generic_clipart": true or false,
  "confidence": 0.0 to 1.0,
  "reason": "short reason, <=15 words"
}}"""


def check_asset(image_path: str, query: str, intent: str = None) -> dict:
    """Caption/verify a downloaded candidate asset before it's trusted in a scene.
    Returns {"accept": bool, "confidence": float, "reason": str}. Fails OPEN (accepts,
    with a logged reason) on infra errors (Ollama down/timeout) so a model hiccup
    doesn't stall asset resolution — but fails CLOSED (rejects) on any parsed content
    mismatch, since a wrong/inappropriate asset silently landing in a scene is the
    worse failure mode (see: a Wikimedia SVG search once returning a mass-shooting
    venue map and a rental-car logo for unrelated queries)."""
    prompt = ASSET_CHECK_PROMPT.format(query=query, intent=intent or query)
    try:
        raw = _call_ollama(prompt, image_path, retries=1, timeout=60)
        data = extract_json(raw)
    except Exception as e:
        return {"accept": True, "confidence": 0.0, "reason": f"VLM check unavailable ({e}); accepted by default"}

    depicts = bool(data.get("depicts_subject", False))
    is_clipart = bool(data.get("is_generic_clipart", False))
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    reason = str(data.get("reason", ""))[:200]

    accept = depicts and is_clipart
    return {"accept": accept, "confidence": confidence, "reason": reason,
            "depicts_subject": depicts, "is_generic_clipart": is_clipart}


def generate_layer_queries(brief: str, layers=("background", "midground", "foreground", "subject")) -> dict:
    """Uses the same small model as a plain text LLM to turn a scene brief into a
    search query per depth layer. Falls back to the raw brief per layer if the
    model output can't be parsed — asset search still works, just less targeted."""
    prompt = (
        f'Scene brief: "{brief}"\n'
        f"For each of these depth layers: {list(layers)}, give a short (2-5 word) "
        "image search query for a clip-art/vector asset that would fit that layer. "
        'Respond with ONLY a JSON object mapping layer name to query string, e.g. '
        '{"background": "mountain silhouette", "midground": "pine trees", '
        '"foreground": "tall grass", "subject": "hiker character"}'
    )
    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "format": "json",
        "stream": False,
        "think": False,
        "options": {"temperature": 0.4},
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=60)
        r.raise_for_status()
        content = r.json()["message"]["content"]
        parsed = json.loads(content)
        return {layer: parsed.get(layer, brief) for layer in layers}
    except Exception:
        return {layer: f"{brief} {layer}" for layer in layers}

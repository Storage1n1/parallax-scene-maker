"""
Validation + repair for the VLM decision contract (see CONTRACT.md).
The pipeline must never act on a raw VLM response — only on the output of validate().
"""

import json
import re

CONFIDENCE_FLOOR = 0.35
VALID_TYPES = {"position", "scale", "depth_order", "missing_asset", "wrong_asset", "empty_space", "occlusion"}
MOVE_TYPES = {"position", "scale"}
REPLACE_TYPES = {"missing_asset", "wrong_asset"}
CLAMP = {"dx": 0.3, "dy": 0.3, "dScale": 0.2}


class ContractViolation(Exception):
    pass


def extract_json(raw: str) -> dict:
    """Strips markdown fences / stray text and parses the first JSON value found.
    The small model sometimes drops the {verdict, confidence, issues} envelope and
    returns a bare issues array — that's coerced into the envelope (verdict "fix",
    since it named concrete problems) rather than discarded, so validate() still
    gets a chance to salvage individual well-formed issues from it."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    data = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", text, flags=re.DOTALL)
        if match:
            data = json.loads(match.group(0))
        else:
            raise ContractViolation(f"No JSON value found in response: {raw[:200]!r}")

    if isinstance(data, list):
        return {"verdict": "fix", "confidence": 0.5, "issues": data}
    return data


def _clamp(value, limit):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(-limit, min(limit, value))


def validate(raw_response: str, known_element_ids: set) -> dict:
    """Parses + validates a VLM response against CONTRACT.md.
    Raises ContractViolation if the response can't be made to satisfy the contract."""
    data = extract_json(raw_response)

    verdict = data.get("verdict")
    if verdict not in ("ok", "fix"):
        raise ContractViolation(f"verdict must be 'ok' or 'fix', got {verdict!r}")

    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    if confidence < CONFIDENCE_FLOOR:
        verdict = "fix"

    issues_in = data.get("issues", [])
    if not isinstance(issues_in, list):
        issues_in = []

    issues_out = []
    seen_targets = set()
    for item in issues_in:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        target = item.get("target")
        if itype not in VALID_TYPES or target not in known_element_ids:
            continue
        if target in seen_targets:
            continue  # only one issue per element per turn

        issue = {"type": itype, "target": target, "note": str(item.get("note", ""))[:200]}

        if itype in MOVE_TYPES:
            move = item.get("move")
            if not isinstance(move, dict):
                continue
            issue["move"] = {
                "dx": _clamp(move.get("dx", 0), CLAMP["dx"]),
                "dy": _clamp(move.get("dy", 0), CLAMP["dy"]),
                "dScale": _clamp(move.get("dScale", 0), CLAMP["dScale"]),
            }
        elif itype in REPLACE_TYPES:
            replace_query = item.get("replace_query")
            if not replace_query or not isinstance(replace_query, str):
                continue
            issue["replace_query"] = replace_query.strip()[:100]
        # depth_order / empty_space / occlusion carry only target + note (informational)

        issues_out.append(issue)
        seen_targets.add(target)

    if verdict == "fix" and not issues_out:
        raise ContractViolation("verdict 'fix' with no actionable issues after filtering")

    return {"verdict": verdict, "confidence": confidence, "issues": issues_out}


def fallback_decision() -> dict:
    """Used when validation fails twice in a row (see CONTRACT.md 'fail closed')."""
    return {"verdict": "ok", "confidence": 0.0, "issues": [], "contract_violation": True}

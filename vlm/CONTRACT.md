# VLM Decision Contract

Every call to the composition-review VLM (`qwen3.5:0.8B` via Ollama, vision-capable)
must return JSON matching this schema. `vlm/contract.py` validates and repairs
responses against it — the pipeline never acts on unvalidated VLM output.

## Schema

```jsonc
{
  "verdict": "ok" | "fix",          // required. "ok" ends the review loop.
  "confidence": 0.0-1.0,            // required. Below CONFIDENCE_FLOOR (0.35) is treated as "fix" regardless of verdict.
  "issues": [                       // required, may be empty. Ignored if verdict == "ok".
    {
      "type": "position" | "scale" | "depth_order" | "missing_asset" |
               "wrong_asset" | "empty_space" | "occlusion",
      "target": "<element_id>",     // required. Must match an id from the state snapshot given to the VLM.
      "move": {                     // required only for type in {position, scale}
        "dx": -0.3..0.3,            // normalized screen-space delta
        "dy": -0.3..0.3,
        "dScale": -0.2..0.2
      },
      "replace_query": "<string>",  // required only for type in {missing_asset, wrong_asset}: new search query
      "note": "<short reason, <=15 words>"
    }
  ]
}
```

## Field rules (enforced by `contract.py:validate`)

- `verdict` must be exactly `"ok"` or `"fix"` — anything else is a contract violation.
- `confidence` must parse as a float in `[0, 1]`. Missing/unparseable → treated as `0.0` (forces a retry, not a guess).
- Every `issues[].target` must be an element id that exists in the scene state passed to the VLM this turn. Unknown targets are dropped (not trusted), and if that empties `issues` while `verdict == "fix"`, the response is rejected as invalid (a "fix" verdict with no actionable issue is incoherent).
- `move.dx` / `move.dy` / `move.dScale` are clamped to their ranges above — the VLM cannot move an element off-frame in one step. This bounds worst-case damage from a bad decision.
- `type: position` or `scale` **must** include `move`; `type: missing_asset` or `wrong_asset` **must** include `replace_query`. An issue missing its required field is dropped.
- At most **one** issue per element per turn is applied (first valid one wins) — prevents the small model from stacking contradictory edits in a single response.

## Failure handling

1. Response isn't valid JSON → strip markdown fences / leading text, retry parse once.
2. Still invalid, or fails schema validation → re-prompt with the same image, once, appending: *"Your last response was not valid JSON matching the schema. Return ONLY the JSON object."*
3. Second failure → **fail closed**: treat as `{"verdict": "fix", "confidence": 0, "issues": []}` is invalid (no issues), so instead the pipeline logs a `contract_violation` entry and advances to the next scene iteration untouched, rather than looping forever or guessing at a fix. This counts as one used iteration.

## Loop termination (enforced by the pipeline, not the VLM)

The VLM is never trusted to end the loop on its own beyond returning `verdict: "ok"`. The pipeline additionally hard-stops after `MAX_ITERATIONS` (default 8 — matching the 8-frame contact sheet) regardless of verdict, and logs whatever the last state was as final.

## Audit trail

Every request/response pair (image path, prompt, raw response, validated decision, action taken) is appended to `output/logs/vlm_decisions.jsonl` — one JSON object per line — so any run can be replayed and checked without re-calling the model.

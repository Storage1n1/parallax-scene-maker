# Dev Log — Parallax Scene Maker

Running log of every code change / experiment, newest entry on top. One entry per step.

---

## 2026-08-01 — BG2/BG1/MG layering: kickoff

**Plan set by user:**
- Two background depths: `BG2` (far — mountains, clouds, sky) and `BG1` (near-far — buildings, cars).
- `MG` (midground) has fixed/universal placement: trees, benches, poles, etc.
- BG2 and BG1 are each a *composition of many generated assets* assembled into one big wide strip, so dolly/camera moves can travel across it. BG2 renders larger/more distant.
- Subjects (e.g. "two broke people") get 3-4 focus-subject generations depending on pacing.
- **First milestone: BG2 strip.** Try different prompt/negative strategies, confirm the local SD1.5 generator can produce usable wide background art before wiring anything into the pipeline.

**Step 1 — `image_gen_server.py`: added a `mode` param (`subject` vs `bg`)**
- Reason: subjects/props need isolated cutouts (white bg → rembg → transparent PNG); BG2/BG1 need full-bleed painted scenes, no cutout, wide aspect.
- Added `BG_STYLE_SUFFIX` ("wide panoramic landscape, single continuous scene, seamless composition") and `BG_DEFAULT_NEGATIVE` (adds "people, characters, white background, cropped" on top of the grid/collage blockers).
- Also hardened the existing subject `DEFAULT_NEGATIVE`/`STYLE_SUFFIX` against the grid/collage artifact reported by the user (multi-subject prompts like "two people" were tiling into panels): added `grid, collage, multiple panels, tiled, comic panels, split screen, borders, frame, contact sheet, diptych, triptych, multiple images, thumbnail grid` to negatives and `single centered object, isolated on background` to the subject style suffix.
- `do_POST` now accepts `width`/`height` (default 512x512) so BG requests can ask for wide aspect ratios, and defaults `remove_bg` to `False` when `mode=bg`.
- File: `image_gen_server.py`

**Step 2 — added `test_bg2_strip.py`**
- Standalone experiment script (not part of pipeline): generates 4 wide (768x384) BG2 tiles via `mode=bg`, composites side-by-side into one strip PNG, saves to `output/bg_experiments/`.
- Also tracks per-tile and total generation timings, printed to console and appended to `output/bg_experiments/timings.log` (per user request: "track the image gen timings also").

**Step 3 — first run: hit a hang, root-caused and fixed a subprocess pipe-deadlock bug in `local_gen.py`**
- Symptom: tiles 0 and 1 generated fine in ~3s each, tile 2 ("wide open sky with scattered fluffy clouds...") hung for the full 180s request timeout and killed the run.
- Root cause: `ensure_server_running()` launches `image_gen_server.py` with `stdout=subprocess.PIPE, stderr=subprocess.STDOUT`, but nothing ever reads that pipe after the startup health-check loop. Windows/OS pipe buffers are finite (~64KB); once the server's `print()` output filled it, the child process blocked on its next `write()` call and stopped processing HTTP requests entirely — looks exactly like "server randomly hangs after a couple of requests."
- Fix: added `_drain_pipe_to_file()`, run on a daemon thread, that continuously reads the child's stdout and writes it to `output/image_gen_server.log`. This both fixes the deadlock and gives us a persistent server log to check for CUDA/OOM errors going forward.
- Files: `local_gen.py`

**Step 4 — re-run succeeded: pipe-drain fix confirmed, first BG2 strip reviewed**
- All 4 tiles generated cleanly, no hang: 3.0s, 2.8s, 2.8s, 2.8s (total 11.3s, avg 2.8s/tile). Output: `output/bg_experiments/bg2_strip.png` (3072x384), timings in `output/bg_experiments/timings.log`, server log in `output/image_gen_server.log`.
- Visual review (4 tiles, left to right):
  1. "layered mountain range silhouettes at dawn, soft clouds, distant hills" — clean flat-vector sunset panorama, good horizon, no artifacts. **Usable.**
  2. "rolling hills and distant misty mountains under a pale sky" — clean flat-vector, layered mountain silhouettes with soft clouds. **Usable.**
  3. "wide open sky with scattered fluffy clouds, faint mountain ridge on horizon" — drifted photoreal (realistic clouds/grass texture, not flat vector) despite the style suffix. **Needs prompt tightening** — likely "fluffy clouds" pulls SD1.5 toward photo training data; try "soft rounded cloud shapes" or add "no photo texture" to that specific negative.
  4. "flat mountain skyline with a few clouds drifting, dusk gradient sky" — clean flat-vector, good layered silhouette. **Usable.**
- Grid/collage negative-prompt fix from Step 1 held: zero tiling/panel artifacts across all 4 images.
- Verdict: 3/4 tiles are pipeline-ready quality on the first real attempt. BG2 strip concept (many generated wide tiles stitched into one long strip) is validated as viable.

**Step 5 — reran with tightened cloud prompt; learned SD1.5 has no fixed seed here, so results aren't apples-to-apples run to run**
- Swapped tile 3's prompt to "wide open sky with soft rounded cloud shapes, faint mountain ridge on horizon, flat poster illustration" and reran all 4.
- Result was a completely different set of images (no seed pinned in image_gen_server.py — every call is a fresh random draw), so this wasn't a clean single-variable test. New tile 3 was still photoreal-leaning mountains, and new tile 4 picked up a stray gray bar artifact at the bottom.
- Takeaway for later: once we're doing real prompt tuning (not just architecture validation), pin a `generator`/seed in `image_gen_server.py`'s `_pipe(...)` call so reruns are comparable, and/or generate N candidates per tile and pick the best rather than accepting whatever comes back.
- Not blocking — the goal of this phase was validating the BG2 strip *concept* (wide tiles, no grid artifacts, strip stitching), which is confirmed. Fine-grained prompt curation is a later pass.

**Step 6 — refactored shared strip logic into `bg_strip_common.py`, added `test_bg1_strip.py`**
- Pulled `generate_tile`/`run_strip_experiment` out of `test_bg2_strip.py` into `bg_strip_common.py` so BG1 doesn't duplicate the generation/stitching/timing-log code.
- `test_bg1_strip.py`: BG1 = near-background buildings/cars layer. Uses taller tiles (768x512, vs BG2's 768x384) since buildings need vertical extent that a flat mountain horizon doesn't. Prompts: rows of city buildings/shops, street with parked cars, apartment blocks, small-town shopfronts with streetlamps.
- Files: `bg_strip_common.py` (new), `test_bg1_strip.py` (new), `test_bg2_strip.py` (now imports from bg_strip_common — logic unchanged, just deduplicated).

**Step 7 — ran `test_bg1_strip.py`: BG1 needs different prompt vocabulary than BG2, not usable yet**
- All 4 tiles generated cleanly, no hangs: 3.9s, 3.7s, 3.8s, 3.8s (total 15.3s, avg 3.8s/tile). Output: `output/bg_experiments/bg1_strip.png` (3072x512).
- Visual review — this batch is **not pipeline-ready**, different problem than BG2 had:
  1. "row of flat city buildings..." — came out as an isometric/aerial street view (looking down at roofs and road), not a front elevation. Wrong camera angle for a horizontal-dolly parallax layer, which needs to look *at* building facades, not down on them.
  2. "city street with parked cars..." — same isometric-aerial problem, worse: buildings and cars are scattered/overlapping with no ground plane, reads more like a cluttered icon pile than a scene.
  3. "block of apartment buildings..." — aerial road-intersection view (looks like a traffic-map illustration). Same camera-angle problem.
  4. "small town street with shopfronts, streetlamps..." — the only one with the right camera angle (front-on rowhouse facades, ground-level), **but** it has a duplicated top/bottom seam — looks like two near-identical rows of buildings stacked, a mild version of the grid/tiling artifact the negative prompt is supposed to block.
  - Root cause read: BG2's mountain/sky prompts have no ambiguity about viewing angle (landscape is always seen from the side), but BG1's generic "street"/"buildings" prompts let SD1.5 default to its most common training association for those words, which skews aerial/isometric map-style. Need to force the camera angle explicitly.
  - Fix for next attempt: add explicit angle language to every BG1 prompt — "front view", "eye-level view", "side elevation of buildings facing the street" — and add "aerial view, top-down, isometric, birds eye view, map view" to `BG_DEFAULT_NEGATIVE` (currently only excludes people/white-bg/cropped, not camera angle).
- Files: none changed yet this step — findings only, fix queued for next.

**Step 8 — angle fix applied, BG1 rerun: validated**
- `image_gen_server.py`: extended `BG_DEFAULT_NEGATIVE` with `aerial view, top-down, top down view, isometric, birds eye view, bird's eye view, map view, overhead view`.
- `test_bg1_strip.py`: rewrote all 4 prompts to explicitly state "front view" / "eye-level" / "front elevation" / "street level side view" instead of generic "row of buildings" wording.
- Result: 3.9s/3.7s/3.8s/3.8s tiles, all 4 now correct camera angle — front-on building facades at eye level, zero aerial/isometric drift, zero grid-duplication seam (the tile-4 seam bug from Step 7 is gone).
  1. Close-up ochre apartment facade with balconies — usable, reads as a nearer building.
  2. Row of colorful shopfront buildings with 3 cars parked at street level and a sidewalk figure silhouette at far right edge — usable; note a tiny person-shaped silhouette slipped past the people negative, worth watching for at scale.
  3. Large beige high-rise apartment block, slightly photoreal/3D-shaded rather than flat vector — usable as a background element but stylistically the outlier of the batch.
  4. Red-brick corner shopfront, clean flat vector, eye-level — usable.
- Verdict: **BG1 strip concept validated**, same as BG2. Both background layers now have working prompt templates. Files: `image_gen_server.py`, `test_bg1_strip.py`.

**Status: both BG2 and BG1 strip concepts validated on the local generator.** Remaining before pipeline integration: seed pinning for repeatable comparisons, curating a bigger prompt bank per layer, deciding tile overlap/seam-blending strategy for the final strip, and wiring MG (fixed-placement trees/benches/poles) + 3-4 focus subjects on top.

**Step 9 — CORRECTION: earlier BG2/BG1 approach was wrong, images had baked-in backgrounds, not transparent**
- User caught it: the "strip" tiles from Steps 4-8 (`bg2_strip.png`, `bg1_strip.png`) are plain `RGB` with a solid painted sky/background baked into every tile (confirmed via `Image.open(...).mode == "RGB"`, no alpha at all). That's not what "composition of many generated assets" means per the original brief — BG2/BG1 should be built from individual isolated transparent assets (single mountain, single building, single cloud, single car) placed onto a strip, the same way MG props work, not one monolithic painted tile per call.
- Added `test_bg2_assets.py`: generates individual isolated assets via `local_gen.generate_asset()` (mode=subject, rembg cutout → real transparent PNG), then composites many of them onto a procedural-gradient sky canvas (gradient painted in code, not AI-generated) to build the strip.
- Added an automated transparency check (`check_transparency()`) that opens each generated asset and reports alpha-channel stats.

**Step 10 — ran it: transparency check passed but content is broken, NOT usable**
- All 5 assets technically had real alpha channels with transparent regions (confirmed: 25-90% transparent pixels each) — so the pipe-level mechanism (rembg cutout via image_gen_server) works correctly.
- But the *content* is wrong:
  - Mountain prompts rendered as **circular badge/icon logos** — a solid-color disc/vignette behind the mountain shape, styled like an app icon. rembg's cutout model treated that opaque colored circle as foreground (it's not a clean white background), so it never got removed. The composited strip is covered in ugly gray/green circles instead of clean silhouettes — visually broken.
  - Cloud prompts didn't generate clouds at all — output was an unrelated cartoon sleeping-cat/crescent-moon icon. Prompt/style combination is not reliably steering SD1.5 toward the intended subject at this small isolated-asset scale.
- Root cause: the subject `STYLE_SUFFIX` ("clip art style... minimalist icon design") biases SD1.5 toward literal circular icon/badge compositions when the prompt itself has no scene context (a bare "single mountain silhouette shape" reads to the model like "make me a mountain *icon*", i.e. a badge), and rembg can't cut out a background that isn't a clean flat color distinct from the subject.
- Correction to the earlier "OK" verdict: passing the alpha-channel check is necessary but not sufficient — it does not verify the cutout region is clean (no leftover badge/circle) or that the content matches the prompt. Need a content check too (visual review every time, not just alpha stats) before calling anything "usable."
- Files: `test_bg2_assets.py` (new, needs prompt rework before reuse).

**Step 11 — reworked prompts, alpha stats improved, but visual review found a WORSE problem: a stock-photo watermark leaking through**
- `image_gen_server.py`: removed "clip art style"/"minimalist icon design" from `STYLE_SUFFIX` (that phrasing was inviting circular badge/app-icon compositions), added `icon, badge, app icon, sticker, circle background, circular frame, vignette, emblem, coin, button, seal, stamp` to `DEFAULT_NEGATIVE`.
- `test_bg2_assets.py`: reworded prompts from "flat vector icon" to "on a plain white background... no other elements" (mirrors how rembg actually expects input — clean subject on flat white, not "icon" framing).
- Alpha-channel stats improved a lot (79-97% transparent vs 25-90% before, no more circle-badge backgrounds) — so the mechanical fix worked as intended.
- **But direct visual inspection (not just the alpha-stat check) found a worse problem than the one being fixed:** the "mountain silhouette" asset has a **visible stock-photo watermark reading "dreamstime" baked into the image**, both in the standalone asset PNG and in the composited strip. SD1.5's training data includes watermarked stock photography, and for this simple-silhouette-style prompt it reproduced the watermark instead of pure original synthesis.
- This directly contradicts the earlier working assumption (recorded when local gen was first wired in) that local SD1.5 generation "solves the relevance/copyright problems web search had" — it does solve *relevance* (on-topic, controllable) but does **not** guarantee copyright-clean output; it can still regurgitate memorized/watermarked training data, especially for simple, common compositions (mountain silhouette, generic clip-art-style shapes) that were probably overrepresented by stock-photo sites in the training set.
- Secondary problems: the mountain shape itself is wrong (reads as a bat/mask silhouette, not a mountain — likely the "silhouette shape... no other elements" phrasing is too abstract for SD1.5 to anchor on "mountain"), and overlapping mountain silhouettes in the composite create unintended bird-leg-like negative-space shapes. The cloud asset is very low-contrast/near-invisible against white, will likely be nearly invisible against a sky gradient too.
- **Not resolved — flagged to user rather than continuing to iterate blindly, given the copyright-risk implication is bigger than this one experiment.**
- Files: `image_gen_server.py`, `test_bg2_assets.py`.

**User decision on the watermark question: pivot back to the painted-tile approach, defer QA to a VLM check**
- User direction: build BG1's final strip from multiple painted tiles (the Step 8 approach, which never showed watermark issues — that was specific to the bare-silhouette isolated-cutout prompts from Steps 9-11). For BG2, generate multiple variations per strip position now; picking the best/cleanest one per position is deferred to a later automated VLM check rather than manual review now.
- This sidesteps the isolated-transparent-asset approach entirely (abandoned, not deleted — `test_bg2_assets.py` stays as a record of what didn't work, see Step 9-11).

**Step 12 — `build_bg1_strip.py`: final 8-tile BG1 strip, validated**
- Expanded the Step 8 prompt set from 4 to 8 tiles (front-elevation/eye-level wording throughout, same style that fixed the aerial-view problem): rowhouses, apartment blocks, shopfronts, office building, corner store with bus stop, terraced houses, etc. Reuses `bg_strip_common.run_strip_experiment`.
- Output: `output/bg_experiments/bg1_final_strip.png` (6144x512).
- Visual review: all 8 tiles correct camera angle (front-on, eye-level), no aerial/isometric drift, no grid-duplication seams, good stylistic variety. One caveat: several tiles have gibberish text on signage/shopfronts (expected SD1.5 text-rendering limitation) — fine as background texture/flavor, NOT usable if a shot ever needs legible signage.
- Files: `build_bg1_strip.py` (new).

**Step 13 — `build_bg2_variations.py`: multi-variant generator for later VLM selection**
- For each of BG2's 4 slot prompts (same wording as the validated Step 4/5 set), generates `VARIANTS_PER_SLOT=3` candidate tiles instead of committing to one. Saves every variant to `bg2_variations/slot_<i>/variant_<j>.png`, builds a per-slot contact sheet for quick review, and writes `manifest.json` (prompt, variant paths, timings, `selected_variant: null`) so a later VLM pass can fill in the pick per slot without regenerating anything.
- Files: `build_bg2_variations.py` (new).

**Step 14 — user redirected BG1 back to isolated-asset composition, this time with an automated quality gate**
- User instruction: for BG1, generate multiple building/asset images, remove background, assemble into a strip, and reject any morphed/wrong images. This supersedes Step 12's painted-tile BG1 (which stays valid/useful as a reference, just not what's wanted for BG1 going forward).
- `bg_strip_common.py`: added `generate_asset_with_gate()` + `_check_asset_quality()`, an automated rejection gate for isolated-cutout assets (uses numpy/scipy, both already available in the venv). Rejects and retries (up to 3 attempts) on:
  - no alpha channel (cutout didn't run)
  - opaque fraction outside [3%, 97%] (cutout ate the whole subject, or never removed the background)
  - opaque region fragmented into disconnected pieces where the largest connected component is <70% of total opaque area (`scipy.ndimage.label`) — this is the "morphed/wrong image" catch: an incoherent/noisy generation cuts out as scattered blobs instead of one clean shape.
  - Rejected attempts are preserved in a `_rejected` dir (not deleted) so failures stay inspectable.
- `build_bg1_strip.py` rewritten to the 3-step process: generate 6 building + 2 car prompts individually (isolated cutout, front-view/eye-level wording), quality-gate every one, assemble accepted assets onto a transparent strip canvas (`RGBA`, ground-aligned at a shared baseline, cars placed in front of alternating buildings).
- First run: gate caught a real failure — "apartment building facade" attempt 1 came back with rembg eating 99.9% of the subject (0.1% opaque) — REJECTED, retried, attempt 2 passed clean. All 8 prompts eventually accepted, 0 fully rejected after retries.
- Visual review of the first assembled strip found a quality issue the mechanical gate didn't catch: several buildings (especially glass-heavy office towers) had soft translucent halo/ghosting at the edges — a doubled-silhouette look — from rembg producing a soft alpha matte around reflective glass. The opacity/connected-component checks don't look at edge sharpness, so this passed the gate despite being visually broken.
- Fix: added `_harden_edges()` to `bg_strip_common.py` — remaps alpha with a threshold ramp (below 60 → fully transparent, above 200 → fully opaque, linear between) to force a crisp matte, applied automatically to every accepted asset in `generate_asset_with_gate()`.
- Reran: all 8 assets accepted first try (no rejections needed this run — different random seed), edges now crisp with no ghosting. Verified transparency is real (`img.getpixel((5,5)) == (0,0,0,0)` at strip corners, confirmed via direct pixel check — the black appearance in image previews is just how the viewer renders unpainted alpha, not a baked-in background).
- Remaining known issue: one building's shop sign renders as gibberish text ("OOCRRE") — same SD1.5 text-rendering limitation flagged in Step 12, cosmetically fine as background flavor, not usable where legible signage matters.
- Files: `bg_strip_common.py`, `build_bg1_strip.py`. Output: `output/bg_experiments/bg1_asset_strip.png`, individual assets in `output/bg_experiments/bg1_assets/`, rejected attempts in `output/bg_experiments/bg1_assets_rejected/`.
- **Verdict: BG1 isolated-asset-composition approach with automated quality gate is validated and working.**

**Step 15 — user feedback: bg removal not great, assets placed too far apart; requested a repeating 1-2-3-4 tile pattern**
- Root cause of "too far apart": assets were never cropped to their content bounding box — each accepted PNG still carried the full generation canvas's empty transparent margin, so even a small fixed pixel gap in the assembly code produced a big visual gap once the padding was included.
- Root cause of "bg removal not great": the quality gate only *measured* fragmentation (largest-component fraction) to decide accept/reject, it never removed the smaller stray blobs/speckle noise rembg leaves outside the main subject on an accepted asset.
- Fixes in `bg_strip_common.py`:
  - `_isolate_largest_component_and_crop()` — zeroes alpha everywhere except the single largest connected opaque blob (removes stray noise), then crops the canvas to that blob's bounding box (+4px pad). Runs on every accepted asset before edge hardening.
  - `build_bg1_strip.py` rewritten to tile the accepted building set in a repeating cycle (`building_i % len(building_paths)`) with a small fixed 6px gap, continuing until the whole `STRIP_W` (4096px) is filled, instead of placing each unique asset once and stopping. Cars cycle the same way, dropped in front of every other building.
- Reran: gate rejected 3 more attempts this run (fragmented shopfront, subject-eaten narrow building) before accepting replacements — confirms the gate is doing real filtering work, not just passing everything. Final strip: 6 unique buildings + 2 cars, tiled edge-to-edge across 4096px, assets now genuinely adjacent (no leftover-padding gaps).
- Visual review: gap problem fixed, repeat pattern confirmed working (clear 1-2-3-4-5-6 cycle visible in the strip). New finding: one accepted building asset (the "narrow building facade" prompt) reads as a thin ladder/fence-like sliver rather than a building — passed the mechanical gate (single coherent component, opaque fraction in range) but is a weak content match. Expected limitation of a shape-coherence gate: it filters "is this one clean blob" not "is this the right subject" — a content-correctness check (e.g. a VLM pass, same idea already planned for BG2) would be the next layer needed to catch this class of failure.
- Files: `bg_strip_common.py`, `build_bg1_strip.py`.

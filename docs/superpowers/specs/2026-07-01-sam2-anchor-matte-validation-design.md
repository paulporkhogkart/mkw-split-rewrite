# SAM2-anchor matte validation — design

**Date:** 2026-07-01
**Branch:** `asset-clip-sweep`
**Status:** validation experiment (no production integration this session)

## Problem

The chip matte engine is birefnet-anchored MatAnyone2: per segment, `birefnet(anchor_frame) > 0.5`
gives a binary mask (`matte_blankplate.py:312-314`) that MatAnyone2 memory-propagates into a
temporally stable alpha. It works, but the **anchor** is a carryover — birefnet was the old
per-frame matte engine, reused as the seed, never chosen as the optimal seed.

birefnet has a **saliency blind spot**: it drops solid low-saliency regions that are genuinely part
of the subject — the canonical case is Mario's **brown blob behind the neck**, given zero alpha on
every frame, so the propagated matte flickers there. This is a *quality* gap in the anchor, not a
quantity gap: more birefnet anchors, softer thresholds, and every birefnet variant were all ruled
out (see the `matanyone-matting-engine` memory) — none see the blob.

A prior session also tried **SAM2 as a full replacement** for MatAnyone2 (SAM2 video-predictor
propagating a *fixed* box `[0.12W, 0.02H, 0.88W, 0.99H]` across the loop). The settled idle frames
matted cleanly, but the **spawn / first frame smeared background** — the fixed box grabbed scene
garbage whenever the subject wasn't centered. That is the "subject isn't always center-frame" worry,
confirmed.

## Goal

Validate whether a **SAM2-refined anchor mask**, localized by birefnet (never a fixed box), produces
a better MatAnyone2 matte than today's birefnet-only anchor — specifically recovering the neck-blob
and staying robust off-center — **decided by eye in a side-by-side viewer, with no production code
touched this session.**

## Why SAM2 image-predictor for the anchor + MatAnyone2 for propagation (not SAM2-video)

- **MatAnyone2 is a matting engine** — soft alpha with fine edge detail (hair, thin wheel spokes).
  **SAM2 is a segmentation engine** — hard, object-level masks with coarse edges. The final result
  needs MatAnyone2's soft alpha, so MatAnyone2 must own propagation. SAM2-video would trade away
  edge quality.
- SAM2's one real strength here is the anchor problem: *given a hint inside the object, return the
  whole coherent object* — no saliency bias. That is exactly what recovers the neck-blob, and it's a
  single-frame job (all an anchor is).
- MatAnyone-family models are designed to be seeded by a SAM-style segmentation mask. This pairs
  each engine to the job it is built for; birefnet stays only as the cheap localizer.

## The pipeline under test (per segment)

1. **birefnet** → rough soft mask on the anchor frame. Used *only to localize* the subject
   (bounding box + confident-pixel samples), not as the final mask.
2. **SAM2 image predictor** (`sam2.1_hiera_base_plus`, config `sam2.1_hiera_b+.yaml`), prompted from
   that localization → a complete, saliency-unbiased binary mask.
3. **MatAnyone2** propagates the anchor across the segment — the existing worker, unchanged.

### Prompt derivation (birefnet → SAM2)

Combo prompt, robust to off-center and compound (rider+kart) objects:

- **Box** = padded bounding box of the birefnet mask (follows the subject — no fixed fraction).
- **Positive points** = a few samples from birefnet's high-confidence pixels (anchors SAM2 onto the
  right object; for kart+rider, one on rider + one on kart so SAM2 merges them into one mask).
- **Negative points** = frame corners (rejects background grabbed inside the box).
- `multimask_output=True`, then **select the SAM2 candidate with highest IoU against the birefnet
  mask** — picks the mask that agrees with birefnet where birefnet is confident while filling its
  blind spots. Self-tuning, no magic threshold.

## Execution — sequential, separate processes (sidesteps GPU coexistence)

The onnxruntime-vs-torch GPU-monopoly problem is **out of scope** for validation. The harness runs
each engine offline in its own process, writing intermediates to disk:

1. **birefnet** (`temp/asset-venv-matte`): reuse `extract_loop` + `matte_blankplate._build_predark_frames`
   to get predark segment frames; dump the birefnet anchor mask per segment.
2. **SAM2** (`temp/sam2-venv`): read the anchor frame + birefnet mask → write the SAM2 anchor mask.
3. **MatAnyone2** (`temp/asset-venv-matte` worker): run the segment **twice** — once per anchor →
   two alpha stacks.
4. **Viewer**: build it.

Sequential separate processes make the three-engine coexistence a later (integration-time) problem.

## Viewer

An HTML scrubber (in the style of `tools/asset_matte/make_viewer.py`), per subject, on a checker
background:

- **birefnet-anchor matte** vs **SAM2-anchor matte**, side by side, scrub / step / zoom.
- The two **anchor masks on frame 0**, overlaid, so the difference (the neck-blob) is directly
  visible.

Output under the session scratchpad, not the repo.

## Test subjects & segments

The 5 clips in `D:\kartoff\captures_sdr\en_uk\clips`:

- `mario__base` — char idle → the neck-blob case (must-pass).
- `mario__base__hot_rod`, `__plushbuggy`, `__standard_kart`, `__zoom_buggy` — compound rider+kart.

Run the **idle** segment for all 5 (where flicker/blob matters). Also run the **spawn** segment for
one kart (most off-center → tests the fixed-box worry directly).

## Success criterion

By eye across the 5 subjects: SAM2 anchor recovers the neck-blob and any other birefnet saliency
drops, stays robust off-center, and introduces **no** new errors (background grabs, lost thin parts)
→ greenlights a future integration session (wiring SAM2 into `matte_blankplate` behind a flag, and
solving three-engine coexistence then).

## Non-goals (YAGNI)

- No production integration into `matte_blankplate.py`.
- No three-engine in-process GPU coexistence.
- No SAM2-video (MatAnyone2 stays the propagator).
- No MatAnyone2 param retuning.
- Low-contrast subject≈bg parts (the cream-collar wall) stay AI-inpaint territory — noted if SAM2
  happens to help, but not pass/fail.

## Risks & mitigations

- SAM2 grabs in-box background → corner negatives + IoU selection.
- SAM2 under-segments kart+rider → multi-point + box + best-IoU pick.
- Anchor edges differ slightly from birefnet → fine; MatAnyone2 refines edges from memory, the
  anchor only needs correct topology/coverage.

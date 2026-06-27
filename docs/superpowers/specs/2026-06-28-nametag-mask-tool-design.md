# Nametag Mask Tool — Design

**Date:** 2026-06-28
**Status:** Approved design, pending spec review
**Scope:** Mask-only. Produce one canonical soft-alpha mask of the static nametag
plate, plus a tool to create / verify / validate it. The downstream "un-darkening"
(recovering vehicle pixels that dip under the plate) is a **separate later effort**
that will consume this mask; it is explicitly out of scope here.

## Background

On the kart/character select screen a **static "nametag" plate** sits at a fixed
screen position: a dark, semi-opaque, tire-track-textured strip with serrated /
perforated edges, carrying the item's name (yellow text) and a 1-UP FUEL badge.

Some vehicles dip into it (B-Dasher's white nose + wheels, frame 4), where the
plate darkens/tints the model — the artifact the later un-darkening must fix. Some
vehicles **never touch it**: Rally Bike floats entirely above the plate, giving an
unoccluded view of `plate over pure background`.

Prior "decompose / un-darken" work (see memory `asset-clip-segmentation`) was left
unsatisfactory because it tried to recover the *background hidden under the plate*
by inpainting / polynomial floor-fitting — an under-determined inverse with ~10
interacting knobs. This effort steps back to first nail a **precise, trusted mask**
of the plate footprint, derived from data rather than thresholds.

## Why the difference method works (and is not under-determined)

The plate is a fixed-screen overlay, and on a clean vehicle the region behind it is
a static blurred background. For Rally Bike:

- **Idle frames:** plate region = `plate over background` → call the per-pixel
  median `P`.
- **Flourish frames:** the plate **drops** *and* the bike lifts away → plate region
  = `pure background` → call the per-pixel median `A`.

`D = P − A` therefore isolates **only the plate**, observed over a *known*
background — no inpainting, no floor-fitting. `|D|` is large under the plate
(strongest under the opaque text/badge), fractional at the serrated edge, ~0
outside. That magnitude, normalized, **is** the soft-alpha mask.

Rally Bike alone is sufficient (idle-vs-flourish fully determines the footprint);
additional clean vehicles only reduce noise.

## Coordinate space & the plate ROI

- **Master resolution: native 4K (3840×2160).** Source clips are
  `captures_sdr/en_uk/clips/*.mkv` at 3840×2160 @ 60fps. The mask is authored and
  stored at 4K so no fidelity is lost; the lightweight 720p `frames/` extracts were
  only ever for the dev viewers.
- **Plate ROI (user-set, native 4K):** `x=2360, y=1602, w=1378, h=226`
  (→ x2=3738, y2=1828). Generously padded; includes the 1-UP FUEL badge. The
  difference method only assigns alpha where the plate actually is, so empty margin
  is harmless. All precompute crops this strip from 4K frames (cheap even at 4K).
- **Exports generated *from* the 4K master** so any consumer resolution is served
  without re-deriving:
  - 720p full-frame mask (1280×720) for the existing matte viewers.
  - 540×590 combo-crop `(700,12,1240,602)@720p` for the matte / future
    un-darkening pipeline to drop in directly.

## Pipeline (Python precompute)

1. **Extract 4K plate-ROI frames** from a clip via
   `ffmpeg -vsync 0` (frame N in folder == decode-order frame N; `cv2.set` seek
   fails on these HEVC clips), cropping to the plate ROI to keep it light.
2. **Classify each frame** as plate-present vs plate-absent, reusing the validated
   **plate-presence gate** (bottom-strip median luma of matte-background pixels;
   dark ⇒ plate present, light ⇒ plate gone), with the window-5 majority smoothing
   that kills 1-frame blips.
3. **Median-reduce** the plate-present frames → `P`; the plate-absent frames → `A`
   (medians kill wheel-tick / coin / particle noise).
4. **Difference** `D = P − A`; convert `|D|` → soft alpha `[0..1]`
   (per-pixel max-channel magnitude, normalized; opaque text/badge ≈ 1, serrated
   edge fractional, clear = 0). No hard threshold is required for the footprint;
   any small floor is exposed as a tunable in the viewer.
5. **Aggregate (optional robustness):** median the per-vehicle alpha maps across any
   additional clean vehicles → canonical mask. With a single clean vehicle the step
   is a no-op.

### Clean-vehicle screening

A candidate vehicle is "clean" iff its **plate-absent** region contains no vehicle
pixels (i.e. the vehicle truly clears the plate during flourish) and its plate band
shows the plate during idle. The tool auto-screens nominated vehicles and reports
which qualify; **Rally Bike is the anchor**. If only Rally Bike qualifies from the
current Mario footage, that is sufficient.

## Viewer — `temp/asset_eyetest/nametag_mask_tool.html`

Served by the existing local server on `:8777` (canvas reads pixel data, which
`file://` blocks — same constraint as `band_picker.html`). One place to create /
verify / validate:

- **Overlay** the candidate mask on any frame/vehicle (Rally Bike clean, B-Dasher
  overlapping), tinted with alpha-modulated opacity so the **soft serrated edge is
  visible**; scrub frames, switch vehicles.
- **Validate:** highlight **over-coverage** (mask spilling onto the B-Dasher
  nose/wheels) and **under-coverage** (plate rim outside the mask) so mis-fit is
  obvious by eye.
- **Fallback methods** (per "don't rule the others out"):
  - **Standout detection** — show an alternate auto-result from
    dark/low-saturation/serrated-edge segmentation of a single clean idle frame.
  - **Manual** — brush add/remove alpha, whole-mask offset/scale, and a soft-floor
    slider on the auto map.
- **Save** the edited mask back to PNG via a POST to the local server (same pattern
  as `band_picker.html`).

The viewer edits the **4K-master** mask (loaded as the cropped high-res plate strip
for responsiveness, with a downscaled full frame for context); exports regenerate on
save.

## Outputs

- `temp/asset_eyetest/nametag_mask/nametag_mask_4k.png` — canonical soft alpha
  (grayscale), full-frame 4K coords (zero outside the ROI).
- `nametag_mask_720.png`, `nametag_mask_combo540.png` — derived exports.
- `nametag_mask_meta.json` — coordinate space, plate ROI, source clips/frames,
  derivation params (floor, smoothing), method used.

## Location

Prototype in `temp/asset_eyetest/` (gitignored, persists) alongside the existing
asset dev tools, with the Python precompute under
`temp/asset_eyetest/detection_scripts/` (or a sibling). Not productionized into
`tools/asset_matte/` yet — consistent with the current asset-work stage; promotion
happens when the downstream un-darkening is also rebuilt.

## Testing

- **Pure-logic Python test** on the difference + aggregation: feed synthetic
  `P`/`A` (known plate over known background) and assert the recovered alpha matches
  the planted footprint within tolerance, including a soft edge.
- **Plate-present/absent classifier** test against a few hand-labeled frame indices
  from Rally Bike + B-Dasher.
- **Visual correctness** is the user's eye-test in the viewer (over/under-coverage
  on B-Dasher), consistent with how every prior asset step was validated.

## Out of scope / deferred

- The un-darkening itself (transmission/tint recovery `t,C`, seam removal on the
  white body). The "soft alpha + measured darkening" output option was declined;
  only the soft alpha mask is produced now.
- Per-language text differences (the strip footprint is language-independent; only
  the inner text glyphs change, and the mask covers the whole strip regardless).
- Promotion into `tools/asset_matte/` and any pipeline wiring.

## Open questions

- Whether any Mario clip other than Rally Bike qualifies as clean (resolved by the
  screening step at build time; does not block — Rally Bike anchors).
- Whether the badge should later be split from the strip for the un-darkening step
  (kept together in the mask for now).

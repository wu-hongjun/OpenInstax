# Plan 036 — Adjustments UX redesign: live preview + sliders + granular control

## Context

Plan 035 shipped a full postprocessing pipeline (saturation / exposure /
sharpness / hue / vignette + datestamp + watermark) wired through a
preset system. The pipeline math is sound; the UX is not.

Three independent audits (designer + critic + code-reviewer) and direct
user feedback converged on the same diagnosis:

1. The 5-position discrete picker (`-100, -50, 0, +50, +100`) is a poor
   fit for continuous photographic adjustments. Real values often live
   between the discrete stops, and the user has no way to land there.
2. The "preset gates editing" model forces a meaningless interaction
   step. A first-run user hits "Edit by setting Preset → Custom" as
   their first toast — opaque jargon as the discoverability moment.
3. There is no feedback loop. The user picks a value and has no idea
   what it does until they burn a sheet of Instax film. This is
   structurally backwards.
4. Five locked rows show chevrons they can't honour. The affordance is
   a lie; users learn to distrust chevrons everywhere.

The replacement design:

- **Live preview** — a small example photo (≈96 × 96 px) renders at the
  top of the Adjustments page with the current adjustments applied. The
  user sees what each axis does immediately.
- **Sliders** — each axis becomes a continuous integer in `[-100, +100]`
  (or `[0, 100]` for vignette). Selecting a row enters a focused
  adjustment mode where Up/Down nudges by 5, Left/Right by 25, KEY1
  commits, KEY2 cancels. Preview repaints on every keypress.
- **Presets become starting points, not gates** — selecting a built-in
  preset stamps its values into the live config. The user can then tweak
  freely. No more "Custom" sentinel; every value is always editable.
- **More granular control** — 41 reachable values per axis instead of
  5; the slider visualises position so the user can see where they are
  in the range without doing the math.

## Phases

Each phase ends with: all gates green, deployed to Pi, `NRestarts=0`,
no Traceback in journal. Same operating rules as plans 034 / 035.

### Phase 1 — Continuous values

Smallest possible data-layer change. Replace the 5-position discrete
picker with a continuous integer in `[-100, +100]` (or `[0, 100]` for
vignette), keep the existing picker UI for now (so the UI rewrite is
isolable to later phases).

- `AdjustmentsConfig` validators relax from `{−100, −50, 0, 50, 100}`
  to `int` in `[-100, 100]` (or `[0, 100]` for vignette).
- `setting_options` for the four colour axes still returns the 5
  discrete options for now — the picker stays usable.
- `config_with_setting_value` already accepts any int via the picker
  path; the relaxed validator is the only change.
- TOML round-trip: existing configs continue to load without migration
  (their values are already in `[-100, +100]`).
- Tests: parametric over a few off-grid values (e.g. saturation = +7,
  exposure = -13) to confirm the pipeline accepts them.

Commit: `refactor(bridge/config): adjustment axes become continuous
ints, drop 5-position discrete cap`.

### Phase 2 — Slider rendering primitive

Add a `draw_slider(...)` primitive in `ui/render.py`. Used by the
Adjustments page to visualise each axis's position.

- Slider drawing: a rounded-rect track + a filled-rect thumb. ~80 px
  wide, 6 px tall track, 8 px tall thumb. Thumb position is computed
  from `(value - min) / (max - min)`. Track filled to the thumb
  position when `value > 0` (or always from the centre for symmetric
  ranges).
- Theme tokens: `theme.slider_track` (light grey), `theme.slider_fill`
  (accent blue), `theme.slider_thumb` (white with 1 px outline).
- Replace each Adjustments row with: label (left) + slider bar
  (middle) + numeric value (right). Chevron stays on rows that still
  open a picker (Preset, Save).
- Tests: pixel-test that a slider with value=50 fills the track to ~75
  % when the range is symmetric `[-100, +100]`.

Commit: `feat(bridge/ui): slider primitive + slider rendering on
Adjustments rows`.

### Phase 3 — Example-photo live preview

Ship a built-in test image and render it in the Adjustments page header
with the current `AdjustmentProfile` applied.

- New asset `bridge/src/instantlink_bridge/imaging/_example_photo.jpg`
  (or PNG). A 240 × 180 stock landscape with colour variety — sky,
  skin tones, foliage, shadow detail. Source: a CC0 image that
  exercises every axis meaningfully. (Embed the image bytes in
  Python? Or ship as a separate file alongside the wheel? Ship as a
  separate file; load with `importlib.resources`.)
- New helper `render_adjustments_preview(profile: AdjustmentProfile,
  *, size: tuple[int, int] = (96, 96)) -> Image.Image`. Loads the test
  image, resizes to `size`, runs `apply_adjustments` on the result.
- Page layout becomes a two-column split: 96 × 96 preview on the left,
  rows on the right.
- Performance: at 96 × 96 the entire stack (hue + saturation + exposure
  + sharpness + vignette) runs in `<10 ms` on the Pi Zero. Verify with
  a microbenchmark.
- Tests: preview is byte-identical for identity profile; preview differs
  measurably for each axis active in isolation.

Commit: `feat(bridge/ui): live example-photo preview on Adjustments`.

### Phase 4 — Focused-adjustment-mode editor

The interaction layer that finally makes the slider editable.

- New `UiMode.ADJUSTMENT_EDIT` mode (or a flag inside `UiMode.SETTINGS`).
  Controller transitions into it on KEY1 over a slider row.
- In edit mode the page renders the focused slider full-width + the
  live preview taking the rest of the page. Other rows are hidden.
- Key bindings:
  - Up: value -= 5
  - Down: value += 5
  - Left: value -= 25
  - Right: value += 25
  - KEY1: commit and return to the list
  - KEY2: cancel (revert to the value at entry) and return
- The preview re-renders on every keypress with the working value;
  commit only writes back to config.
- Tests: state-machine tests confirm enter → tweak → commit + enter →
  tweak → cancel both work; the on-disk config only updates on commit.

Commit: `feat(bridge/ui): focused adjustment-mode editor with live
preview`.

### Phase 5 — Drop Custom gate, add overwrite + delete + cleanups

Remove the read-only-when-not-Custom wall and surface preset management.

- `resolve_preset` simplified: every value is read from config now,
  presets are pure starting templates. Selecting a preset *stamps* its
  values into config (via a one-shot `apply_preset_template` action),
  not a permanent override.
- `"Custom"` sentinel preset name removed. Any user-saved preset is a
  named slot.
- Save flow becomes two-press confirm (matches plan 034 destructive-
  confirm pattern): first press shows `Press KEY1 again to save as
  CustomN`, second press writes.
- Long-press on a saved-preset picker option opens an overwrite /
  delete sub-menu. Picker option labels grow to `Custom1` … `Custom4`
  with a trailing `›` to indicate the sub-menu.
- Slot cap raised from 4 to 6 (eight is the natural ceiling on the
  visible picker without scrolling; six leaves headroom for future
  built-ins).
- The pre-existing P1 / P2 cleanups from the three audits roll in
  here:
  - `save_user_presets` adds `fp.flush()` + `os.fsync()` before
    `os.replace` (code-reviewer P1).
  - Vignette NumPy ops become tiled / in-place to halve peak memory
    (code-reviewer P1).
  - "B&W" preset name becomes "Black & white" (critic P2).
  - "Hue" help text becomes user-perceptual instead of degrees
    (critic P2).
  - System page gets an empty section divider between Refresh status
    and Appearance (critic P2).
  - The chevron-lie disappears naturally because no rows are
    read-only any more.

Commit: `refactor(bridge/ui): presets become templates; drop Custom
sentinel; add preset overwrite + delete + plan-035 cleanups`.

## Out of scope (defer to a later plan)

- Configurable watermark text (still hardcoded `"InstantLink"` until
  someone wires a config picker for it).
- Configurable datestamp format (still locale-driven; no custom
  pattern picker).
- LCD keyboard for naming presets (still auto-named `CustomN`).
- Multi-touch / gesture inputs (the bridge has 3 keys + 1 joystick).

## Performance budget

Phase 3 introduces a render-time `apply_adjustments` call on a 96 × 96
preview, repainted on every Up/Down/Left/Right keypress in the edit
mode. The target is `<50 ms` per keypress on the Pi Zero (smooth
interactive feel). The microbenchmark in phase 3 must measure this and
the commit message must include the number.

If the budget is missed:

- Cache the per-axis intermediate results when only one axis changes
  (hue is the expensive one — skip it if `hue` didn't change between
  renders).
- Drop the preview resolution to 80 × 80.
- Drop interactive repaint and only update preview on key release / a
  100 ms debounce.

## Cross-cutting constraints

- After each phase: `pytest -q --timeout=10 --timeout-method=thread`,
  `mypy src`, `ruff check src tests` must be clean.
- Deploy after each phase. Bridge must come back active with
  `NRestarts=0`.
- Conventional commits. Reference plan 036 + phase number in body.
- No `SKIP_HOOK=1` — memory file `skip-hook-not-needed` documents both
  lint gates are clean.
- Apple iOS 26 voice + sentence-case rules for any new copy. zh-Hans
  entries added at the same time as their EN source.

## Open design questions to resolve before phase 4

1. **Edit-mode key bindings**. Up/Down for ±5, Left/Right for ±25 is
   the working draft. Should KEY3 also do something (e.g. snap to
   zero)?
2. **Preview area aspect ratio**. 96 × 96 keeps it square, which fits
   the Square Link printer's aspect. Should the preview adopt the
   currently-configured `PrinterModel`'s aspect (e.g. 96 × 64 for Mini)?
3. **Test image source**. CC0 from Unsplash? Wikimedia? Ship a
   single-file asset with a documented attribution comment.

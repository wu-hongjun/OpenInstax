# Plan 008: macOS UI/UX Pass 2

**Status:** In Progress

## Goal

Improve the image editor and overlay workflow so it feels more direct, more structured, and less burdened by model/UI mismatch.

This pass is still product-focused. It is not the full visual redesign pass. The priority is reducing editing friction and removing complexity that the current UI does not justify.

## Scope

### In Scope

1. Make defaults terminology accurately match behavior
2. Add direct-manipulation affordances for overlay resizing
3. Reduce inspector density by grouping controls into clearer sections
4. Clean up the location overlay editor so it only shows relevant fields
5. Remove unused overlay-model surface area that is not represented in the UI

### Out of Scope

- Global liquid-glass visual redesign
- New animation system
- Full settings IA redesign
- Overlay rotation
- New overlay types

## Problems To Fix

### 1. Defaults wording is misleading

The defaults popover currently says `Use Current Photo Settings`, but the implementation only promotes timestamp defaults. The copy should match the actual behavior.

### 2. Overlay editing is too slider-heavy

Users can drag overlays, but resizing still relies on numeric width/height sliders. That is too indirect for a visual editor.

### 3. Inspector is too dense

Selection actions, position controls, and content-specific settings are stacked together in one narrow column without enough structure.

### 4. Location editor shows irrelevant controls

Location source, display style, and manual fields are shown together even when some of them do not apply to the selected source.

### 5. Model and UI have drifted apart

Some overlay fields exist in the data model without corresponding UI or meaningful current behavior. Because backward compatibility is not a constraint, these should be removed instead of preserved.

## Implementation Workstreams

### Workstream A: Editor wording and inspector structure

- Rename the timestamp-default action to match what it actually does
- Group inspector content into clearer sections such as `Content`, `Position`, and `Appearance`
- Keep advanced numeric controls, but stop making them the only editing affordance

### Workstream B: Direct-manipulation overlay resize

- Add resize handles to the selected overlay in the editor canvas
- Keep the existing drag-to-move behavior
- Ensure resize stays clamped to the print canvas

### Workstream C: Model simplification

- Remove unused overlay-model fields and enums that are not represented in current UX
- Update preview and print rendering to rely on the simplified model
- Keep the simplified model aligned with the UI we actually ship

### Workstream D: Location editor cleanup

- Only show fields relevant to the chosen source
- Avoid showing manual coordinate inputs for metadata-driven overlays
- Avoid showing display options that do not make sense for manual text mode

## Acceptance Criteria

- Defaults wording no longer overpromises what gets saved
- Selected overlays can be resized directly on canvas
- Inspector layout is easier to scan than the current monolithic stack
- Location editor fields are conditional and coherent
- Unused model surface area is removed instead of carried forward

## Exit Criteria

Pass 2 is complete when the editor interaction model feels coherent without relying on hidden future flexibility, and the overlay model matches shipped behavior closely enough that future work can build on a cleaner base.

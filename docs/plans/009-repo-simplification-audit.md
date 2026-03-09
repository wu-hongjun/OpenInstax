# Plan 009: Repository Simplification Audit

**Status:** Proposed

## Goal

Reduce structural complexity across the repository now, while backward compatibility is still cheap to break.

This is not a feature pass. It is a cleanup sequence aimed at removing stale paths, collapsing duplicate structures, and making the repository easier to evolve.

## Priority Findings

### P0: FFI header packaging can drift from the real ABI

`instantlink.h` is generated into Cargo `OUT_DIR` unless `INSTANTLINK_UPDATE_HEADER=1`, but release packaging copies the checked-in header directly.

Implication:
- the shipped FFI zip can contain a stale header even when the dylib/staticlib is current

Recommended fix:
1. generate the header deterministically in CI
2. package only the generated header
3. optionally fail CI if the checked-in header differs from generated output

## P1 Cleanup Sequence

### 1. Split the macOS app by feature

`macos/InstantLink/InstantLinkApp.swift` is still too monolithic. It currently mixes:

- app lifecycle
- printer/device orchestration
- print pipeline
- queue state
- camera flow
- overlay editor
- settings/about UI

Recommended target structure:

- `App/`
- `Features/Queue/`
- `Features/Camera/`
- `Features/OverlayEditor/`
- `Features/Settings/`
- `Services/Printing/`
- `Services/Profiles/`

### 2. Remove or formalize `InstantLinkCLI.swift`

If the app is no longer using CLI process execution as a runtime path, delete `InstantLinkCLI.swift` and stop compiling it. If it must remain as fallback, make that fallback explicit and tested.

### 3. Consolidate printer profile editors

There are parallel profile-editing paths in the macOS app. These should collapse into one shared editor component with shared validation and save logic.

## P2 Cleanup Sequence

### 4. Keep overlay model and editor schema aligned

The overlay model should only contain fields we actually expose or intentionally derive. Pass 2 already starts this cleanup by trimming unused fields. Continue until the inspector and model line up one-to-one.

### 5. Normalize location editing

Location overlay source modes should map to clearly separate UI states and validation rules rather than one mixed form.

### 6. Move completed plans out of the active plan set

`docs/plans` currently mixes active and historical material. Completed plans should move into an archive section with a short note linking to the implemented reality.

## Supporting Cleanup

### 7. Keep contributor metadata in sync automatically

`AGENTS.md`, docs, and build metadata should be checked for drift in CI where possible, especially Rust edition, release artifacts, and documented commands.

## Suggested Order

1. Fix FFI header packaging in CI/release flow
2. Remove dead macOS CLI fallback path if truly unused
3. Split `InstantLinkApp.swift` into feature files without changing behavior
4. Consolidate duplicate printer-profile editing views
5. Archive completed plans and tighten docs ownership

## Exit Criteria

This audit is complete when the repo has:

- one authoritative FFI header path per release
- no dead fallback layers
- a decomposed macOS app structure
- fewer duplicated UI/editor paths
- a clear boundary between active plans and historical records

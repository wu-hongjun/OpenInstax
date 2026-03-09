# Plan 009: Repository Simplification Audit

**Status:** In Progress

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

Completed in March 2026: the macOS app is no longer a single-file target. The old `InstantLinkApp.swift` monolith was split into:

- `App/` for lifecycle and relaunch helpers
- `Core/` for `ViewModel`, queue state, and print orchestration
- `Features/Camera/`, `Features/Main/`, `Features/Editor/`, and `Features/Settings/` for workflow-specific UI
- `Support/` for reusable preview, overlay, and panel components

Remaining follow-up:
- continue reducing `Core/ViewModel.swift` by extracting non-state services and helpers behind clearer boundaries
- consolidate printer profile editing flows so settings and post-pairing use the same editor surface

### 2. Remove or formalize `InstantLinkCLI.swift`

Completed in March 2026: `InstantLinkCLI.swift` was removed and `scripts/build-app.sh` no longer compiles a Swift CLI fallback layer. The macOS app now uses the FFI dylib as its only printer/device runtime path, while the bundled CLI binary remains only for metadata/version lookups.

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
2. Consolidate duplicate printer-profile editing views
3. Archive completed plans and tighten docs ownership
4. Continue decomposing `Core/ViewModel.swift` into smaller services once current UX work stabilizes

## Exit Criteria

This audit is complete when the repo has:

- one authoritative FFI header path per release
- no dead fallback layers
- a decomposed macOS app structure with slimmer core state ownership
- fewer duplicated UI/editor paths
- a clear boundary between active plans and historical records

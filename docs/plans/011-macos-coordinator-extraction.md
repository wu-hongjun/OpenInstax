# Plan 011: macOS Coordinator and Render Service Extraction

**Status:** Proposed

## Goal

Reduce `macos/InstantLink/Core/ViewModel.swift` further by extracting three non-UI responsibilities behind clear boundaries:

1. printer connection and pairing
2. queue and per-photo edit-state management
3. print preparation and render composition

This plan is about simplifying ownership and making future UI iteration safer. It is not intended to change user-facing behavior.

## Target Structure

Add these new files under `macos/InstantLink/Core/`:

- `PrinterConnectionCoordinator.swift`
- `QueueEditCoordinator.swift`
- `PrintRenderService.swift`

`ViewModel` should remain the observable bridge for SwiftUI, but delegate workflow logic into these types.

## Extraction 1: Printer Connection Coordinator

Own:
- pairing loop
- printer discovery and nearby scan
- selected printer switching
- connection refresh and status hydration
- profile bootstrap after first successful connection

Suggested surface:

```swift
@MainActor
final class PrinterConnectionCoordinator
```

Inputs:
- `InstantLinkFFI`
- access to persisted printer profiles
- callbacks for status/error publishing

Outputs:
- connection snapshot struct with `isConnected`, printer identity, battery, film, charging, print count
- profile update events

## Extraction 2: Queue/Edit-State Coordinator

Own:
- queue add/remove/reorder/select
- queue item edit-state persistence and restoration
- new-photo defaults seeding
- camera draft/file-mode edit-state transitions

Suggested surface:

```swift
@MainActor
final class QueueEditCoordinator
```

Outputs:
- authoritative queue array
- selected queue index
- active edit state for the selected item

This coordinator should become the only layer allowed to mutate `QueueItem` and `QueueItemEditState`.

## Extraction 3: Print Preparation/Render Service

Own:
- crop/flip/rotate pipeline
- final film canvas rendering
- overlay composition
- temporary print-file generation

Suggested surface:

```swift
enum PrintRenderService
```

Key rule:
- preview math and print math must share the same placement rules

Longer term, this service should be usable by both print execution and export/share flows.

## Migration Order

1. Extract `PrintRenderService` first. It is the most self-contained and easiest to verify against existing output.
2. Extract `QueueEditCoordinator` next. This removes the largest cluster of state mutation from `ViewModel`.
3. Extract `PrinterConnectionCoordinator` last. It has the most app-wide touch points and should move after the queue/render seams are stable.

## Verification

For each extraction step:

- `cargo test --workspace`
- `bash scripts/build-app.sh 0.1.2`
- manual macOS smoke test:
  - pair printer
  - import/reorder/remove queue items
  - edit two different images and switch between them
  - print current and batch print
  - camera capture, edit, and print

## Exit Criteria

This plan is complete when:

- `ViewModel` no longer directly owns pairing loop logic
- `ViewModel` no longer directly mutates queue/edit-state internals
- print preparation lives outside `ViewModel`
- printer-profile flow remains unified through one editor component
- no user-visible behavior regresses during pairing, editing, or printing

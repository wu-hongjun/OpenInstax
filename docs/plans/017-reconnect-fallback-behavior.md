# Plan 017: Reconnect Fallback and Recovery UX

## Goal

Make reconnect behavior predictable and user-comprehensible when a remembered printer is unavailable, renamed, slow to reappear, or fails its first targeted reconnect attempt.

The target UX is simple:

- if we know which printer the user wants, try that first
- if that fails, automatically fall back to broader recovery options
- never leave the user staring at a dead-end spinner with no next step

## Current Problems

- Reconnect to a remembered printer is currently a one-shot targeted attempt.
- If that attempt fails, the app can stop without a clean fallback into scan or switch-printer selection.
- The UI can transiently show misleading connected/disconnected states while refresh and reconnect race.
- Saved printers and nearby results are still conceptually split in ways that can obscure what the app is actually trying to do.

## UX Principles

- Reconnect should feel like recovery, not a separate product flow.
- We should show one truthful current action at a time.
- Known-printer reconnect failure should lead directly to the next best action, not a blank end state.
- The app should only show `Connected` after post-connect status fetch succeeds.

## Proposed State Machine

### Reconnect entry

When the user clicks `Reconnect` and a selected/saved printer exists:

1. show `No printer connected`
2. immediately transition into a pairing/reconnect sheet/state
3. show `Reconnecting to <printer name>...`
4. run one targeted reconnect attempt with real connection-stage progress

### If targeted reconnect succeeds

- fetch status
- only then transition to connected UI

### If targeted reconnect fails

Automatically move to recovery mode:

- keep the reconnect sheet/state visible
- update copy to `Couldn’t reconnect to <printer name>`
- start a bounded nearby scan
- surface both:
  - saved printers
  - nearby printers

The selected/remembered printer should stay highlighted if it appears again.

### If scan finds nothing

Show explicit recovery actions:

- `Try Again`
- `Switch Printer`
- `Open Bluetooth Settings` if the printer was recently forgotten or the app suspects system-level pairing issues

## Implementation Scope

Primary files:

- `macos/InstantLink/Core/PrinterConnectionCoordinator.swift`
- `macos/InstantLink/Core/ViewModel.swift`
- `macos/InstantLink/Features/Main/MainView.swift`

Supporting areas:

- saved-printer picker UI in `SettingsViews.swift`
- connection-stage text mapping
- localizable strings for recovery copy

## Coordinator Changes

- Introduce explicit reconnect attempt outcome states:
  - `targetedReconnect`
  - `scanFallback`
  - `manualSelection`
- Separate:
  - reconnecting to a known printer
  - scanning for any printer
- Ensure stale refresh results cannot overwrite reconnect state.
- Keep session tokens on reconnect flows so canceled/older attempts cannot repaint the UI.

## UI Changes

- Replace ambiguous end states with one recovery panel.
- When reconnect fallback begins, keep the same modal/screen visible instead of bouncing between disconnected and picker flows.
- Show `Switch Printer` as a first-class recovery action, not as an unrelated detour.

## Testing

### Manual

- remembered printer off -> on -> reconnect succeeds
- remembered printer remains off -> fallback scan -> no results
- remembered printer renamed / `(IOS)` suffix variant -> fallback recovers
- remembered printer unavailable -> user switches to another saved printer
- reconnect canceled mid-flight -> new reconnect attempt starts cleanly

### Automated

- coordinator tests for:
  - targeted reconnect success
  - targeted reconnect failure -> scan fallback
  - stale callback ignored after cancel
  - no connected-state transition before status fetch success

## Rollout Order

1. Refactor reconnect coordinator states.
2. Implement targeted reconnect -> scan fallback flow.
3. Update disconnected/pairing UI copy and actions.
4. Add coordinator tests.
5. Run app build and hardware reconnect smoke tests.

## Exit Criteria

- Reconnect never ends in a dead-end spinner.
- Failed targeted reconnect always yields a clear next action.
- The app no longer flashes misleading connected state during reconnect failure.
- Saved-printer recovery and switch-printer behavior feel like one coherent flow.

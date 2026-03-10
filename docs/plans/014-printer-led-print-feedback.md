# Plan 014: Printer LED Print Phase Feedback

## Goal

Use the printer's built-in LED as a simple phase indicator during print operations so the device feels more alive while avoiding any risk to transfer reliability.

## Constraints

- Do not send repeated LED commands during chunk upload.
- Do not try to mirror upload percentage on the printer LED.
- Do not block or delay the print pipeline if LED control fails.
- Keep the app UI as the primary detailed progress surface; LED feedback is secondary.

## Why

The app already shows phase-aware print progress:

- `Preparing image...`
- `Sending to printer...`
- `Starting print...`

The printer itself stays visually static. Since the protocol already supports LED control, we can add lightweight device feedback that matches those phases without inventing fake progress.

## Proposed UX

Use phase-based LED signaling only:

- Idle: LED off
- Preparing image: no LED change yet
- Sending to printer: blue `breathe`
- Starting print: amber `solid`
- Print success: green `blink` briefly, then off
- Print failure: red `blink` briefly, then off

This keeps the meaning obvious:

- blue = working
- amber = final handoff
- green = success
- red = failure

## Architecture

### 1. Add a Print LED coordinator on macOS

Create a small helper owned by the macOS app, not by the Rust print pipeline.

Responsibilities:

- map app-level `PrintProgressPhase` and final result to LED states
- send one-off LED commands through `InstantLinkFFI`
- debounce duplicate state transitions
- ignore failures so printing is never blocked by LED issues

Suggested file:

- `macos/InstantLink/Core/PrinterLedCoordinator.swift`

### 2. Keep LED control out of the chunk loop

Do not thread LED updates through `progress_cb(sent, total)`.

Only send LED changes on these state transitions:

- before upload starts
- when upload completes and `printPhase` becomes `.starting`
- after print returns success/failure

That avoids command interleaving with the ACK-based transfer flow.

### 3. Add one safe FFI surface if needed

Current Swift already has:

- `setLed(r:g:b:pattern:)`
- `ledOff()`

If current `blocking` semantics are sufficient, reuse them. If not, add a small fire-and-forget wrapper so LED cleanup does not stall UI flow.

## Implementation Steps

### Phase 1: Safe coordinator

- Add `PrinterLedCoordinator`
- Define LED presets:
  - sending: `(31, 111, 235, breathe)`
  - starting: `(248, 120, 67, solid)`
  - success: `(38, 222, 109, blink)`
  - failure: `(230, 57, 70, blink)`
- Add a short success/failure auto-off timer

### Phase 2: Wire into print lifecycle

- On single print:
  - set `sending` when `printPhase = .sending`
  - set `starting` when `printPhase = .starting`
  - set success/failure at completion
- On batch print:
  - keep LED in phase state across items
  - only show final success blink after the last item
  - failure on any item should switch to failure blink and then off

### Phase 3: Reconnect and cleanup behavior

- turn LED off when:
  - print is cancelled
  - printer disconnects mid-print
  - reconnect flow starts
  - app terminates while connected

## Risks

### Protocol interference

The main risk is that LED commands could contend with print commands on the same BLE session. That is why this plan only sends LED commands at coarse phase boundaries.

### Stale LED state

If the printer disconnects after a blue or amber state, the LED could remain on. The coordinator should always attempt `ledOff()` during known teardown paths, but failures must stay non-fatal.

## Verification

### Manual

- single print success
- single print failure
- batch print success
- batch print failure on item `n`
- disconnect during sending
- disconnect during starting

### Code checks

- `cargo test --workspace`
- `bash scripts/build-app.sh 0.1.3`

## Exit Criteria

- LED feedback is visible and phase-correct
- print reliability is unchanged
- no extra queue/FFI deadlocks are introduced
- LED failures never surface as print failures

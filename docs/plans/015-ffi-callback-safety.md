# Plan 015: FFI Callback Safety and Concurrency

## Goal

Remove the remaining callback-safety hazards in the macOS FFI bridge so connection-stage and print-progress callbacks cannot race, overwrite each other, or read freed memory.

This is a correctness plan, not a UX plan. The objective is to make the callback boundary mechanically safe under reconnect, print, cancellation, and future parallel operations.

## Current Problems

- `InstantLinkFFI.swift` stores connection and print callback boxes in global singleton pointers.
- Those pointers are released before they are cleared, which creates a use-after-free window if a C callback fires during teardown.
- The current design also assumes only one callback-bearing operation can exist at a time.
- The FFI queue is serialized today, but the callback API itself is still unsafe and too easy to break with future changes.

## Design Principles

- Eliminate global callback state from Swift.
- Tie callback lifetime to the specific FFI call that created it.
- Use explicit opaque context pointers in the C ABI.
- Make null-callback behavior match current non-progress entry points.
- Keep the bridge simple enough that non-Swift consumers can use it directly.

## Proposed FFI Surface

Add context-aware callback entry points alongside the existing exported functions:

```c
typedef void (*instantlink_connect_stage_cb_ctx)(int32_t stage,
                                                 const char *detail,
                                                 void *context);

typedef void (*instantlink_print_progress_cb_ctx)(uint32_t sent,
                                                  uint32_t total,
                                                  void *context);

int32_t instantlink_connect_named_with_progress_ctx(const char *name,
                                                    int32_t duration_secs,
                                                    instantlink_connect_stage_cb_ctx progress_cb,
                                                    void *context);

int32_t instantlink_print_with_progress_ctx(const char *path,
                                            uint8_t quality,
                                            uint8_t fit_mode,
                                            uint8_t print_option,
                                            instantlink_print_progress_cb_ctx progress_cb,
                                            void *context);
```

Legacy functions remain as thin wrappers for now:

- `instantlink_connect_named_with_progress(...)`
- `instantlink_print_with_progress(...)`

They can pass `NULL` context internally.

## Rust Implementation

### `crates/instantlink-ffi`

- Add the two new `_ctx` exports.
- Thread `context: *mut c_void` through the callback trampoline.
- Keep all callback invocation best-effort and panic-free.
- Ensure wrappers preserve current return-code behavior.

### Header / export hygiene

- Update `include/instantlink.h`.
- Add `cargo:rerun-if-env-changed=INSTANTLINK_UPDATE_HEADER` in `build.rs` if it still is not present, so header refresh remains reliable during release packaging.

## Swift Integration

### `macos/InstantLink/InstantLinkFFI.swift`

- Replace `ConnectionStageBox.current` and `ProgressBox.current` with per-call retained boxes.
- Pass the retained box pointer as `context`.
- In the C callback, recover the box from `context` instead of a global static.
- On teardown:
  - clear any Swift-side reference first
  - then release the retained box
- Keep all FFI calls on the existing serial queue.

### Cancellation model

- A canceled operation should drop its Swift completion path, but stale callbacks must still be safe.
- Callbacks from a finished/canceled operation should be ignored by session token checks in higher layers, not crash the bridge.

## Testing

### Rust

- Add unit tests for `_ctx` callback forwarding.
- Verify null callback + non-null context is harmless.
- Verify wrapper exports still behave like current functions.

### Swift / macOS

- Smoke test:
  - connect with progress
  - print with progress
  - cancel reconnect and start a new reconnect
  - reconnect followed immediately by print-prep refreshes
- Verify no stale callback can update the wrong operation after completion.

## Rollout Order

1. Add `_ctx` FFI exports and header updates.
2. Switch macOS Swift wrapper to the context-aware API.
3. Keep legacy exports in place temporarily.
4. Run workspace tests, clippy, and app build.
5. Manually stress reconnect + print + cancellation.

## Exit Criteria

- No global callback singleton pointers remain in Swift.
- Callback teardown order is safe.
- Progress callbacks are isolated per operation.
- Connection and print progress still work exactly as before from the user’s perspective.
- The app no longer has a callback-bridge use-after-free risk.

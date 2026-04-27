# Plan 022: FFI Device Guard Concurrency

## Goal

Stop taking the global device out of its `Option` slot during a print. Today, `print_with_progress_internal` calls `guard.take()`, which makes every concurrent FFI accessor (battery, status, is_connected, …) return "no device" for the multi-second duration of a print, and risks losing the device entirely if the calling thread panics between take and put-back.

## Current State

`crates/instantlink-ffi/src/lib.rs:160-162` (and the surrounding put-back at ~178-180)

```rust
let mut guard = DEVICE.lock().unwrap();
let device = guard.take().ok_or(NOT_CONNECTED)?; // <-- removes device
// ... long-running print ...
*guard = Some(device);                            // <-- restored at the end
```

Compare with the non-progress `instantlink_print` (~line 674) which uses `if let Some(ref device) = *guard` and never removes the device from the slot.

Symptoms today:

- The macOS app's status pollers (battery, connection state) flicker during print.
- A panic between `take()` and the restore would permanently lose the device handle for the lifetime of the process.

## Proposed Change

1. Switch `print_with_progress_internal` to borrow the device behind the lock rather than removing it:

   ```rust
   let guard = DEVICE.lock().unwrap();
   let device = guard.as_ref().ok_or(NOT_CONNECTED)?;
   device.print_with_progress(...).await
   ```

2. Validate that holding the mutex across an `.await` is acceptable. If it is not (because we currently use `std::sync::Mutex`), either:
   - migrate the global to `tokio::sync::Mutex`, or
   - introduce a smarter wrapper that holds an `Arc<Device>` so the lock can be released before the `.await`.
3. Add a regression test that calls `instantlink_battery` mid-print (using a fake device) and confirms it returns valid data instead of `NOT_CONNECTED`.

## Implementation Scope

Primary:

- `crates/instantlink-ffi/src/lib.rs`

Possible supporting changes:

- internal helper to centralize the "with device" pattern across all FFI handlers, so future drift is impossible

## Testing

- `tests::ffi_status_works_during_print` — start a print on a fake device, query battery/status concurrently, expect success.
- `tests::ffi_panic_during_print_does_not_leak_device` — simulate a panic inside the print closure and confirm the device is still reachable afterwards.
- Smoke test the macOS app: confirm that battery/status indicators in the UI remain stable during a print.

## Risks

- If the device API requires `&mut self`, we need to revisit how the FFI layer wraps it — likely via an `Arc<Device>` with internal `tokio::sync::Mutex` slots for mutable bits.
- Care needed not to deadlock: never hold `DEVICE.lock()` across an `.await` if the lock is `std::sync::Mutex`.

## Rollout Order

1. Audit every FFI handler for the same pattern.
2. Pick a single concurrency primitive (`Arc<Device>` + `tokio::sync::Mutex` is the recommended path).
3. Migrate `print_with_progress_internal` first, keep the rest unchanged temporarily.
4. Add tests.
5. Migrate the remaining handlers to the shared helper.

## Exit Criteria

- No FFI handler removes the device from the global slot for the duration of an operation.
- Concurrent introspection FFI calls succeed during a print.
- A panic inside any handler cannot leak the device.

# Plan 020: Notification Listener Lifecycle

## Goal

Make sure the `tokio::spawn`-ed BLE notification listener inside `BleTransport` actually shuts down when the transport disconnects, so reconnect cycles do not pile up orphaned tasks holding references to the peripheral and the unbounded mpsc sender.

## Current State

`crates/instantlink-core/src/transport.rs:244-258`

- A listener task is spawned to forward `notification.value` into an `mpsc::Sender`.
- No `JoinHandle` is stored.
- The task only ends when the underlying notification stream returns `None`, which can lag well behind a logical `disconnect()`.
- `BleTransport::disconnect()` (≈line 348) unsubscribes and disconnects the peripheral but never aborts or awaits the listener.
- During an aggressive reconnect-loop UX, multiple stale listener tasks can coexist, each pinning their own `Arc<Peripheral>` clone.

## Proposed Solution

1. Store the `JoinHandle<()>` (and ideally a `CancellationToken`) inside `BleTransport`.
2. In `disconnect()`:
   - signal the cancellation token
   - drop the peripheral notification subscription
   - `await` the join handle with a short timeout (e.g. 250 ms); if it does not finish, `abort()` it
3. In `Drop for BleTransport` (or an explicit shutdown path), abort the handle as a last-resort safety net.
4. Ensure the listener loop checks the cancellation token between `next()` calls so it cannot block forever on a stalled stream.

## Implementation Scope

Primary:

- `crates/instantlink-core/src/transport.rs` — add the handle/token, wire up cancellation
- new dev dependency on `tokio_util::sync::CancellationToken` (already part of `tokio-util`) if we choose tokens over a oneshot

Optional follow-up:

- Surface a `transport.listener_active()` introspection helper for tests.

## Testing

- Unit test with a fake notification stream that never ends; the test verifies that calling `disconnect()` causes the listener task to exit within a bounded time.
- Stress test that reconnects many times in a loop and asserts the spawned-task count does not grow unbounded.

## Risks

- Aborting a task that is mid-write to the channel can drop a notification — acceptable, since `disconnect()` already implies abandoning in-flight work.
- macOS CoreBluetooth occasionally takes its time before notifications stop emitting; the timeout-then-abort path handles that gracefully.

## Rollout Order

1. Wire the cancellation token + join handle.
2. Update `disconnect()` to use them.
3. Add unit + stress tests.
4. Manual hardware soak: 50 connect/disconnect cycles, watch for resource growth.

## Exit Criteria

- After `disconnect()` returns, the listener task has either exited or been aborted.
- No measurable task or memory growth after sustained reconnect loops.
- Unit tests cover both the graceful and the abort path.

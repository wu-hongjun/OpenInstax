# Plan 016: Transport Command Serialization and Protocol Hardening

## Goal

Serialize all request/response command traffic per connected printer so concurrent operations cannot interleave writes and consume each other’s responses.

This plan also tightens the surrounding timeout and ACK-validation behavior, because serialization alone is not enough if malformed or fragmented responses can still be treated as success.

## Current Problems

- `Transport::send_and_receive(...)` is logically “send one command, await one response,” but today that sequence is not protected by an operation lock.
- `BlePrinterDevice` methods take `&self`, so multiple async callers can overlap commands on the same device.
- `receive(timeout)` effectively resets the timeout budget on each fragment loop iteration.
- Some ACK/status decoders still default missing payload bytes to success.

## Design Principles

- One in-flight protocol command per printer connection.
- Timeouts should apply to the total operation, not each fragment.
- Malformed responses must fail closed.
- Tests should prove command ordering and timeout semantics directly.

## Proposed Architecture

## 1. Make `send_and_receive` atomic

Change the `Transport` trait so `send_and_receive(...)` is an explicit required operation instead of a default helper.

For `BleTransport`:

- add a `command_lock: tokio::sync::Mutex<()>`
- acquire it across:
  - write
  - response assembly
  - final packet decode

That makes the full command lifecycle atomic per device connection.

For mocks/tests:

- implement the same trait contract directly so tests can simulate serialized and malformed flows.

## 2. Convert `receive(timeout)` to deadline semantics

Instead of applying the full timeout on every `rx.recv()` wait:

- compute `deadline = Instant::now() + timeout`
- on each loop, wait only for the remaining duration
- fail once the total budget is exhausted

This prevents fragmented or noisy traffic from stretching a 10-second timeout into something much longer.

## 3. Fail malformed ACKs and status packets

Tighten packet decode in `commands.rs`:

- if `download_start`, `download_chunk`, `download_end`, or `print_image` ACK payloads are missing the expected status byte, return an error
- do not `unwrap_or(0)` into fake success

Use a dedicated protocol error where possible so higher layers can distinguish malformed transport from printer rejection.

## Scope

Primary files:

- `crates/instantlink-core/src/transport.rs`
- `crates/instantlink-core/src/device.rs`
- `crates/instantlink-core/src/commands.rs`

Supporting files:

- transport mocks/tests
- any FFI code that relies on specific error mapping if protocol validation becomes stricter

## Testing

### Unit tests

- concurrent command attempts on one device cannot interleave responses
- `send_and_receive` returns responses in the correct call order
- fragmented response respects a single total timeout budget
- malformed ACK payloads fail instead of succeeding

### Regression checks

- battery / film / print count queries still work
- print transfer still works with progress callback
- reconnect and status refresh are unchanged except for improved robustness

## Rollout Order

1. Refactor `Transport` trait and `BleTransport`.
2. Add total-timeout receive logic.
3. Tighten `commands.rs` ACK validation.
4. Add targeted concurrency and malformed-packet tests.
5. Run workspace tests, clippy, and app build.

## Exit Criteria

- No overlapping command on one printer connection can corrupt request/response pairing.
- Total timeout behavior is deterministic.
- Empty or malformed ACK payloads can no longer be treated as success.
- No regressions in printing, status fetch, reconnect, or LED control.

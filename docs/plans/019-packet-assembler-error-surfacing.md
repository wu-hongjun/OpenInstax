# Plan 019: Surface Protocol Errors from PacketAssembler

## Goal

Stop silently dropping corrupted bytes inside `PacketAssembler::feed`. When a header is invalid or a checksum fails, the transport layer should learn about it and have the option to log, reset, or surface a real error — instead of waiting for a downstream timeout.

## Current State

`crates/instantlink-core/src/protocol.rs:130-159`

`feed()` returns `Option<Packet>` and uses `None` to mean two very different things:

- not enough bytes yet — keep accumulating
- invalid header or bad checksum — bytes were silently discarded

`receive_packet_from_channel` (`transport.rs`) cannot distinguish these. In a noisy BLE environment or when interacting with a buggy printer firmware, the user observes only opaque timeouts and has no way to root-cause why.

## Proposed Change

1. Introduce `ProtocolError` (or extend `PrinterError::Protocol`) with the relevant variants:
   - `InvalidHeader { discarded: usize }`
   - `BadChecksum { opcode: u16 }`
   - `LengthMismatch { declared: u16, actual: usize }`
2. Change `PacketAssembler::feed` to return `Result<Option<Packet>, ProtocolError>`.
3. Update `receive_packet_from_channel` to:
   - log the protocol error at `warn` level
   - increment a transport-level metric/counter (or, if no metrics exist yet, at minimum a `tracing` event)
   - keep listening — corruption should not abort the receive loop unless it persists

## Implementation Scope

Primary:

- `crates/instantlink-core/src/protocol.rs`
- `crates/instantlink-core/src/transport.rs`
- `crates/instantlink-core/src/error.rs` (new variant if we keep one shared error type)

Tests:

- `protocol::tests::feed_signals_invalid_header`
- `protocol::tests::feed_signals_bad_checksum`
- `transport::tests::receive_packet_from_channel_logs_corruption_then_resumes`
- `transport::tests::receive_packet_from_channel_surfaces_persistent_corruption`

## Migration Concerns

- Any external caller of `PacketAssembler::feed` (currently only the in-tree transport) must be updated to the new return type.
- Existing tests that assume `feed` returns `Option` need to be ported.

## Testing

Unit tests cover:

- valid input still returns `Ok(Some(packet))`
- partial input still returns `Ok(None)`
- invalid header byte returns `Err(InvalidHeader)` with the discarded count
- corrupted payload (header valid, checksum wrong) returns `Err(BadChecksum)`

Transport tests confirm that one bad packet does not break the loop — the next valid packet still arrives.

## Rollout Order

1. Add `ProtocolError` variants and the new `feed` signature.
2. Update transport to log + continue.
3. Add unit tests.
4. Spot-check on hardware: print succeeds even when a deliberately mangled packet is injected via a mock fragment source.

## Exit Criteria

- A corrupted byte stream never disappears silently.
- Logs explicitly call out which kind of corruption occurred.
- The receive loop keeps making forward progress unless corruption is persistent.

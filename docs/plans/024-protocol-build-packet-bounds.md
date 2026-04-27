# Plan 024: Bounds-Check `protocol::build_packet`

## Goal

Make `build_packet` reject payloads it cannot represent on the wire instead of silently truncating the length field and emitting a malformed packet.

## Current State

`crates/instantlink-core/src/protocol.rs:41-47`

```rust
let total_size = HEADER_LEN + 2 + payload.len() + 1;
let mut out = Vec::with_capacity(total_size);
out.extend_from_slice(&HEADER);
out.extend_from_slice(&(total_size as u16).to_be_bytes()); // <-- silent truncation
```

If `payload.len() > u16::MAX as usize - 7 (≈ 65528)`, the `as u16` cast wraps. The resulting packet has a length field that disagrees with the actual byte count, the checksum will look correct to a sender that recomputed it on the truncated header, and the receiver will lose framing.

The function is `pub` and currently relied on for chunked image data, where chunks are well below this limit. But the safety contract is implicit, not enforced.

## Proposed Change

Pick one of:

### Option A — return `Result`

```rust
pub fn build_packet(opcode: u16, payload: &[u8]) -> Result<Vec<u8>, ProtocolError> {
    let total_size = HEADER_LEN + 2 + payload.len() + 1;
    if total_size > u16::MAX as usize {
        return Err(ProtocolError::PayloadTooLarge { len: payload.len() });
    }
    // ...
}
```

This is the "right" version but ripples through every caller.

### Option B — assert at call boundary

Keep the signature, add `debug_assert!(total_size <= u16::MAX as usize)` plus a `MAX_PACKET_PAYLOAD` constant and update callers (and any external integrators) to honor it.

### Option C — clamp to a hard runtime panic

`assert!(total_size <= u16::MAX as usize, "payload too large for protocol frame");` — preferable to silent truncation, even without a `Result`.

**Recommendation:** Option A. The function is the wire-format primitive; it should return errors instead of trusting callers. The caller count is small (transport + tests).

## Implementation Scope

Primary:

- `crates/instantlink-core/src/protocol.rs`
- `crates/instantlink-core/src/transport.rs` — propagate the new `Result`
- Tests in `protocol::tests` to lock in the new behavior

## Testing

- `protocol::tests::build_packet_rejects_oversize_payload`
- `protocol::tests::build_packet_accepts_max_payload`
- Existing tests keep passing with `?` propagation.

## Risks

- API break for anyone embedding the crate. Mitigated by the fact that `instantlink-core` is currently consumed only by our CLI and FFI crates.
- Slight ergonomic cost in tests that previously used `build_packet` directly — they need `.unwrap()`.

## Rollout Order

1. Introduce a `PayloadTooLarge` error variant.
2. Change the signature to `Result`.
3. Update all in-tree callers.
4. Add the new tests.
5. Update changelog/release notes for the next minor version.

## Exit Criteria

- Oversize payloads cannot produce malformed packets.
- The maximum allowed payload is documented.
- The change is covered by direct unit tests.

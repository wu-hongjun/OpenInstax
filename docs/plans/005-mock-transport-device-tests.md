# Plan 005: MockTransport + Device-Layer Tests

## Status: Implemented

## Problem

`device.rs` had zero test coverage because `BlePrinterDevice` requires a real BLE transport.

## Solution

Added a `#[cfg(test)] mod tests` block at the bottom of `device.rs` with:

1. **MockTransport** — implements `Transport` with a FIFO response queue and sent-bytes recording
2. **7 response helpers** — build `protocol::Packet` objects for each response type
3. **Device construction helper** — `make_device()` prepends model detection response
4. **22 test functions** covering all device behavior

### MockTransport Design

- `send()` records raw bytes to `state.sent`
- `receive()` pops next `Result<Packet>` from queue (supports error injection)
- Uses `std::sync::Mutex` (not tokio) since mock is synchronous
- Constructor returns `(Box<dyn Transport>, Arc<Mutex<MockState>>)` for test assertions

### Test Coverage (22 tests)

| Category | Tests | What's covered |
|----------|-------|----------------|
| Model Detection | 5 | Mini/Square/Wide detection, unknown dims, wrong response type |
| Status Queries | 5 | Battery, film remaining, print count, full status, unexpected response |
| LED Commands | 2 | set_led, led_off (verifies zeros sent) |
| Print Flow | 2 | Single-chunk and 3-chunk transfer with offset verification |
| Error Paths | 5 | DownloadStart/Data/End rejection, unexpected response, transport error |
| Other | 3 | Progress callback (i, total), disconnect delegation, name storage |

## Results

- Total tests: 66 (was 44)
- `cargo fmt`, `cargo clippy`, `cargo test` all pass clean
- No new files, no new dependencies, no changes to lib.rs or Cargo.toml

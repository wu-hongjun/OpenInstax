# OpenInstax — Project Instructions

## Plans

- Every plan created during plan mode must be saved as a numbered file in `/docs/plans/` (e.g., `001-scaffold.md`, `002-protocol.md`).
- Plans are the source of truth for implementation decisions and should be committed alongside the code they describe.

## Code Standards

- `cargo fmt --all` before every commit
- `cargo clippy --workspace -- -D warnings` — treat all warnings as errors
- `cargo test --workspace` — all tests must pass
- Conventional commits: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`

## Architecture

- **openinstax-core**: BLE protocol, image processing, device communication (async with tokio + btleplug)
- **openinstax-cli**: CLI binary (clap + indicatif), calls core directly
- **openinstax-ffi**: C FFI for Swift/macOS app (cbindgen), wraps core with global tokio runtime
- **No daemon**: Instax printing is one-shot (connect → print → disconnect)

## BLE Protocol

- Service UUID: `70954782-2d83-473d-9e5f-81e1d02d5273`
- Write char: `70954783-...`, Notify char: `70954784-...`
- Packet: `[0x41 0x62][len:2B][opcode:2B][payload][checksum:1B]`
- Checksum: `(255 - (sum & 255)) & 255`
- MTU fragmentation: 182-byte sub-packets
- Print flow: DOWNLOAD_START → DATA chunks (ACK per chunk) → DOWNLOAD_END → PRINT_IMAGE

## Multi-Model Support

| Model | Resolution | Chunk Size |
|-------|-----------|------------|
| Mini Link | 600×800 | 900 B |
| Square Link | 800×800 | 1808 B |
| Wide Link | 1260×840 | 900 B |

## Development Workflow

1. Understand requirements — read affected files
2. Implement changes
3. Verify: `cargo fmt --all && cargo clippy --workspace -- -D warnings && cargo test --workspace`

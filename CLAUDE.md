# InstantLink — Project Instructions

## Plans

- Every plan created during plan mode must be saved as a numbered file in `/docs/plans/` (e.g., `001-scaffold.md`, `002-protocol.md`).
- Plans are the source of truth for implementation decisions and should be committed alongside the code they describe.

## Code Standards

- `cargo fmt --all` before every commit
- `cargo clippy --workspace -- -D warnings` — treat all warnings as errors
- `cargo test --workspace` — all tests must pass
- Conventional commits: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`

## Architecture

- **instantlink-core**: BLE protocol, image processing, device communication (async with tokio + btleplug)
- **instantlink-cli**: CLI binary (clap + indicatif), calls core directly
- **instantlink-ffi**: C FFI for Swift/macOS app (cbindgen), wraps core with global tokio runtime
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

## macOS App (SwiftUI)

- Split architecture under `macos/InstantLink/`:
  - `App/` for app entry and relaunch helpers
  - `Core/` for `ViewModel` and print/device orchestration
  - `Features/` for Camera, Main, Editor, and Settings UI
  - `Support/` for shared preview and panel components
- Compiled with `swiftc` directly (no Xcode project)
- FFI loaded via `dlopen`/`dlsym` from `InstantLinkFFI.swift` (19 symbols)
- Features: image editor (crop/contain/stretch, rotation, overlays), camera capture with self-timer (2s/10s), film orientation toggle, film border preview (`FilmFrameView`), printer profile management, auto-updates
- Localization: 12 languages in `macos/Resources/{lang}.lproj/Localizable.strings`
- `L()` helper in `Localization.swift` wraps `NSLocalizedString`

## Versioning

- App version: passed to `build-app.sh` (e.g., `bash scripts/build-app.sh 0.1.3`), written to Info.plist
- CLI/crate versions: in each crate's `Cargo.toml` — **must be bumped in sync with app version**
- Three Cargo.toml files to bump: `instantlink-core`, `instantlink-ffi`, `instantlink-cli`
- The About section in Settings shows both App and Core versions to verify they match
- **Always bump version when rebuilding** to confirm the installed binary is up to date

## Building & Installing

- Build: `bash scripts/build-app.sh <version>`
- Install: `pkill -x InstantLink; sleep 1; rm -rf /Applications/InstantLink.app && cp -R target/release/InstantLink.app /Applications/InstantLink.app`
- **Must `rm -rf` the old .app before copying** — `cp -R` alone may not fully replace the bundle, causing stale binaries
- Launch: `open /Applications/InstantLink.app`

## Development Workflow

1. Understand requirements — read affected files
2. Implement changes
3. Verify: `cargo fmt --all && cargo clippy --workspace -- -D warnings && cargo test --workspace`
4. **Always rebuild and reinstall after every change**: `bash scripts/build-app.sh <version> && pkill -x InstantLink; sleep 1; rm -rf /Applications/InstantLink.app && cp -R target/release/InstantLink.app /Applications/InstantLink.app && open /Applications/InstantLink.app`

## Learnings / Gotchas

- Copyright year is 2026 (not 2025)
- When installing .app bundles on macOS, always `rm -rf` the destination first, then `cp -R`. Plain `cp -R` over an existing bundle can leave stale files
- SwiftUI `.frame(width:minHeight:)` does not exist as a two-parameter overload — use `.frame(minWidth:maxWidth:minHeight:)` instead

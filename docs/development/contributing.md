# Contributing

## Development Setup

```bash
git clone https://github.com/wu-hongjun/InstantLink.git
cd InstantLink
cargo build --workspace
cargo test --workspace
```

## Before Every Commit

```bash
cargo fmt --all
cargo clippy --workspace -- -D warnings
cargo test --workspace
```

Clippy warnings are treated as errors in CI.

## Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` new behavior or user-facing capability
- `fix:` bug fix
- `refactor:` structural cleanup without intended behavior change
- `docs:` documentation-only change
- `test:` test-only change
- `chore:` tooling, CI, or build-system change

## Build and Release Notes

- Use `bash scripts/build-app.sh <semver>` to build `target/release/InstantLink.app`
- The script requires a semver argument such as `0.1.2` or `v0.1.2`
- GitHub Releases are tag-driven via `.github/workflows/release.yml`
- Tagged releases publish the macOS DMG plus separate CLI and FFI zip archives

## Code Standards

- Rust uses snake_case for functions/variables, PascalCase for types, and `SCREAMING_SNAKE_CASE` for constants
- Prefer `thiserror`-based `PrinterError` in the core crate
- Use `anyhow` with `.context(...)` in the CLI for user-facing command failures
- Keep protocol and model knowledge in `instantlink-core`; avoid duplicating it in CLI or Swift

## Project Structure

```text
InstantLink/
├── crates/
│   ├── instantlink-core/   # BLE protocol, device logic, image processing
│   ├── instantlink-cli/    # CLI binary
│   └── instantlink-ffi/    # C ABI wrapper for native apps
├── macos/InstantLink/
│   ├── App/                # SwiftUI app entry, app delegate, relaunch helpers
│   ├── Core/               # ViewModel, queue state, print pipeline
│   ├── Features/           # Camera, main window, editor, settings UI
│   ├── Support/            # Shared preview, overlay, and panel components
│   ├── InstantLinkFFI.swift
│   ├── Localization.swift
│   └── OverlayModels.swift
├── docs/                   # MkDocs documentation
├── scripts/build-app.sh    # App bundle + DMG build script
└── .github/workflows/      # CI and release automation
```

## Testing

### Fast Tests

```bash
cargo test --workspace
```

This covers protocol encoding/decoding, image preparation, mock transport flows, and device-level behavior without real hardware.

### Hardware Verification

Prefer running the local CLI from the repo rather than relying on a separately installed binary:

```bash
cargo run -p instantlink-cli -- scan
cargo run -p instantlink-cli -- info
cargo run -p instantlink-cli -- status
cargo run -p instantlink-cli -- print test.jpg
```

Use a real printer for BLE discovery, status, print, and LED checks.

## Documentation

Docs are built with MkDocs Material:

```bash
pip install mkdocs-material
mkdocs serve
mkdocs build
```

Update docs alongside behavior changes. Reference docs should describe the live codebase, not historical implementation details.

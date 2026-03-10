# Repository Guidelines

## Project Structure & Module Organization
`crates/instantlink-core/src` contains the async BLE protocol, printer/device layers, image pipeline, and most inline unit tests. `crates/instantlink-cli/src` is the `clap` frontend. `crates/instantlink-ffi/src` and `crates/instantlink-ffi/include` expose the C interface used by the macOS app in `macos/InstantLink`, which is split into `App/` (entry/relaunch), `Core/` (view model and print logic), `Features/` (Camera, Main, Editor, Settings), and `Support/` (shared preview/panel components). Keep docs in `docs/`, automation in `scripts/`, and reverse-engineering references in `references/`. Treat `target/` as generated output.

## Build, Test, and Development Commands
Run workspace checks from the repo root:

```bash
cargo build --workspace
cargo test --workspace
cargo fmt --all
cargo clippy --workspace -- -D warnings
```

Build the release app bundle with `bash scripts/build-app.sh 0.1.3`. The macOS app baseline is 15.0 (`arm64-apple-macosx15.0` + `LSMinimumSystemVersion 15.0`). Install the CLI locally with `cargo install --path crates/instantlink-cli`. Preview docs with `mkdocs serve` after `pip install mkdocs-material`.

## Coding Style & Naming Conventions
This repository uses Rust 2024 and default `rustfmt` formatting with 4-space indentation. Follow the existing naming scheme: `snake_case` for functions/modules, `PascalCase` for types/enums, and `SCREAMING_SNAKE_CASE` for constants. Keep CLI-facing error messages contextual with `anyhow::Context`; keep reusable core errors in `PrinterError`. In Swift, follow the current SwiftUI style in `macos/InstantLink` and keep FFI loading isolated from UI logic.

## Testing Guidelines
Add Rust unit tests close to the code under `#[cfg(test)] mod tests`; current coverage lives mainly in `protocol.rs`, `commands.rs`, `image.rs`, and `device.rs`. Prefer mock-based tests for protocol or transport behavior so they run without hardware. For BLE or print-flow changes, also record a manual smoke test such as `cargo run -p instantlink-cli -- status` or `cargo run -p instantlink-cli -- print sample.jpg`, including the printer model used.

## Commit & Pull Request Guidelines
Use Conventional Commits, matching recent history: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`. Keep PRs focused and describe affected crates, user-visible behavior, and verification performed. Include screenshots for macOS UI changes, and for protocol or hardware work note the tested printer model, macOS version, and whether the change was validated on real hardware.

# Contributing

## Development Setup

```bash
git clone https://github.com/wu-hongjun/InstantLink.git
cd InstantLink
cargo build --workspace
cargo test --workspace
```

## Code Standards

### Before Every Commit

```bash
cargo fmt --all
cargo clippy --workspace -- -D warnings
cargo test --workspace
```

All three must pass cleanly. Clippy warnings are treated as errors.

### Commit Messages

Use [conventional commits](https://www.conventionalcommits.org/):

- `feat:` — New feature
- `fix:` — Bug fix
- `refactor:` — Code restructuring without behavior change
- `docs:` — Documentation only
- `test:` — Test additions/changes
- `chore:` — Build, CI, tooling changes

### Error Handling

- **Core crate**: Use `thiserror` with `PrinterError` enum and `Result<T>` alias
- **CLI crate**: Use `anyhow` with `.context()` for user-facing errors

### Naming

- Snake case for functions and variables
- Pascal case for types and enums
- `SCREAMING_SNAKE` for constants

## Project Structure

```
InstantLink/
├── Cargo.toml                    # Workspace root
├── CLAUDE.md                     # Dev instructions
├── mkdocs.yml                    # Documentation config
├── docs/                         # MkDocs documentation
├── crates/
│   ├── instantlink-core/          # BLE protocol, image processing, device comms
│   ├── instantlink-cli/           # CLI binary
│   └── instantlink-ffi/           # C FFI bindings
├── macos/
│   └── InstantLink/               # SwiftUI app
├── scripts/
│   └── build-app.sh              # App bundle build script
└── references/                   # Cloned reference repos (gitignored)
```

## Testing

### Unit Tests (No Hardware)

Protocol, command encoding/decoding, and image processing are fully tested without hardware:

```bash
cargo test --workspace
```

Currently 44 unit tests covering:

- Packet checksum, build, parse, fragmentation, reassembly (14 tests)
- Command encoding and response decoding (14 tests)
- Image resize, JPEG encoding, chunking (10 tests)
- Fit mode parsing, edge cases (6 tests)

### Hardware Tests

BLE transport and end-to-end printing require a real Instax Link printer:

```bash
instantlink scan          # Verify printer discovery
instantlink status        # Verify full communication
instantlink print test.jpg  # Verify print pipeline
```

## Documentation

Documentation is built with [MkDocs Material](https://squidfundraising.github.io/mkdocs-material-squidfundraising/):

```bash
pip install mkdocs-material
mkdocs serve    # Local preview at http://localhost:8000
mkdocs build    # Build static site to site/
```

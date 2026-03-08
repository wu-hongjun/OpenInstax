# Plan 002: CI/CD Setup

**Status:** Completed

## Goal

Set up GitHub Actions for continuous integration, documentation deployment, and tagged releases.

## Workflows

### CI (`ci.yml`)

Runs on every push to `main` and on pull requests.

**Steps:**

1. Format check (`cargo fmt --all --check`)
2. Clippy lint (`cargo clippy --workspace -- -D warnings`)
3. Unit tests (`cargo test --workspace`)
4. Build (`cargo build --workspace`)

**Runner:** `macos-latest` — required because btleplug depends on CoreBluetooth on macOS. Unlike StatusLight which uses `ubuntu-latest` (hidapi has Linux support), OpenInstax needs macOS for BLE compilation.

### Docs (`docs.yml`)

Deploys MkDocs Material documentation to GitHub Pages.

**Triggers:**

- Push to `main` when `docs/**` or `mkdocs.yml` changes
- Manual dispatch via `workflow_dispatch`

**Steps:**

1. Checkout with full history
2. Install Python + mkdocs-material
3. Deploy to `gh-pages` branch

**Setup required:** Enable GitHub Pages in repo settings, set source to `gh-pages` branch.

### Release (`release.yml`)

Creates GitHub Releases with pre-built binaries when a version tag is pushed.

**Trigger:** Push tags matching `v*` (e.g., `v0.1.0`)

**Artifacts:**

- `OpenInstax-CLI-vX.Y.Z.zip` — CLI binary
- `OpenInstax-FFI-vX.Y.Z.zip` — Static/dynamic library + C header

**Usage:**

```bash
git tag v0.1.0
git push origin v0.1.0
```

## Key Decisions

1. **macOS runner for CI**: btleplug requires CoreBluetooth, so we can't use ubuntu-latest
2. **No Linux CI**: The primary target is macOS; Linux BLE (BlueZ) support is secondary
3. **Separate CLI and FFI packages**: Different consumers need different artifacts

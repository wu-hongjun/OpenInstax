# Plan 003: GitHub Pages Documentation

**Status:** Completed

## Goal

Deploy project documentation to GitHub Pages using MkDocs Material.

## Documentation Structure

```
docs/
├── index.md                          # Project overview
├── getting-started/
│   ├── installation.md               # Build from source, macOS app
│   └── quickstart.md                 # Scan, print, LED, JSON output
├── reference/
│   ├── cli.md                        # Full CLI command reference
│   ├── protocol.md                   # BLE protocol specification
│   └── ffi.md                        # C FFI function reference
├── development/
│   ├── architecture.md               # Crate layers, design decisions
│   └── contributing.md               # Dev setup, code standards, testing
└── plans/
    ├── 001-full-stack-scaffold.md     # Initial implementation plan
    ├── 002-ci-cd-setup.md            # CI/CD workflow plan
    └── 003-github-pages.md           # This plan
```

## Setup

1. `mkdocs.yml` configures MkDocs Material with navigation, search, code highlighting
2. `docs.yml` workflow auto-deploys on docs changes
3. GitHub Pages must be enabled in repo settings (source: `gh-pages` branch)

## Local Preview

```bash
pip install mkdocs-material
mkdocs serve
```

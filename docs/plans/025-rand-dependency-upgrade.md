# Plan 025: Upgrade `rand` to a Sound Release

## Goal

Get off `rand 0.9.2` (RUSTSEC-2026-0097) which is pulled into our build via `image → rav1e → rand`. The unsoundness affects `rand::rng()` when used together with a custom logger; while we do not exercise that path today, leaving a known-unsound crate in the dependency tree is a poor signal and a latent risk if anyone enables logging hooks down the road.

## Current State

- `Cargo.lock` resolves to `rand 0.9.2`.
- We do not depend on `rand` directly; the path is `instantlink-core → image → ... → rand`.
- `cargo audit` flags this as RUSTSEC-2026-0097.

## Proposed Change

1. Bump the `image` crate to a release whose dependency closure pulls in a patched `rand` (≥ 0.9.3 once published, or whatever mainline ships next).
2. If the upstream `image` release is delayed, add an explicit `[patch.crates-io]` override to pin `rand` directly.
3. Add `cargo audit` to CI so the next unsound transitive does not slip through unnoticed.

## Implementation Scope

Primary:

- `Cargo.toml` (workspace dependency block)
- `crates/instantlink-core/Cargo.toml`
- `Cargo.lock`
- `.github/workflows/ci.yml` (new `Audit` step using `rustsec/audit-check@v1` or `cargo-deny`)

## Testing

- `cargo build --workspace` and `cargo test --workspace` after the bump.
- `cargo audit` passes with zero advisories.
- Image pipeline smoke test (Mini / Square / Wide) — image decoding has historically been the one place where bumping `image` shakes loose surprises.

## Risks

- `image` can break API surface across minor versions. Plan time for the bump even if it is a one-line change in `Cargo.toml`.
- A `[patch.crates-io]` for `rand` is a temporary measure; it should be removed once the upstream release lands.

## Rollout Order

1. Run `cargo update -p rand` and check whether the lockfile resolves to the patched version on its own.
2. If not, bump `image` to a release that does.
3. If still not, add the temporary `[patch.crates-io]`.
4. Wire `cargo audit` into CI.
5. Verify smoke tests on real hardware.

## Exit Criteria

- `cargo audit` reports zero advisories from the workspace.
- CI fails on any new advisory.
- Image decoding regressions are zero.

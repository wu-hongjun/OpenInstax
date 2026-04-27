# Plan 023: Restore unsafe Block Discipline in the FFI Crate

## Goal

Remove the crate-level `#![allow(unsafe_op_in_unsafe_fn)]` from `instantlink-ffi` and require every individual unsafe operation to live in an explicit `unsafe { ... }` block with a `// SAFETY:` justification. This brings us back in line with the Rust 2024 edition default and makes the FFI surface auditable.

## Current State

`crates/instantlink-ffi/src/lib.rs:17`

```rust
#![allow(unsafe_op_in_unsafe_fn)]
```

This blanket allow means that an `unsafe fn` body can dereference raw pointers, transmute values, or call FFI functions without any `unsafe {}` block. Today the code happens to validate inputs correctly (e.g. `cstr_to_str` at line 196, `write_str_to_buf` at line 210), but the lint is the only mechanism that would catch a future contributor who skips the validation step.

## Why It Matters

- Soundness regressions in FFI become silent — no warning at the call site.
- Reviewers cannot grep for `unsafe {` to find the actual unsafe operations; they would have to read every line of every `unsafe fn` carefully.
- It makes external audits (and our own Plan 018-style security reviews) more expensive.

## Proposed Change

1. Remove the crate-level `#![allow(unsafe_op_in_unsafe_fn)]`.
2. For each `unsafe fn` in the crate, wrap the unsafe operations inside a scoped `unsafe { ... }` block.
3. Add a `// SAFETY:` comment explaining the caller contract — at minimum:
   - what pointer must be valid
   - what lifetime must outlive the call
   - whether the function is reentrant-safe
4. Treat the resulting clippy warnings as work items; do not paper over them with `#[allow(...)]` unless we have an explicit justification.

## Implementation Scope

- `crates/instantlink-ffi/src/lib.rs` — every public `extern "C"` function and every `unsafe fn` helper.
- No FFI ABI changes; only internal shape changes.

## Testing

- The existing FFI test suite covers null-pointer, invalid-UTF-8, and disconnected-device branches — make sure all of those still pass.
- Add a soundness sanity check: run the test suite under `cargo +nightly miri` (best-effort; Miri does not fully support `extern "C"` + `catch_unwind`, but it surfaces the obvious UB).

## Rollout Order

1. Remove the blanket allow.
2. Fix every resulting compile error one function at a time.
3. Add `// SAFETY:` comments as we go; do not let the comments become rote — each must say something specific.
4. Re-run `cargo clippy --workspace -- -D warnings` to confirm the unsuppressed lint stays clean.

## Exit Criteria

- `unsafe_op_in_unsafe_fn` is no longer suppressed at any scope.
- Every unsafe operation has a localized `unsafe {}` block and a `// SAFETY:` comment.
- `cargo clippy --workspace -- -D warnings` passes without new allow attributes.

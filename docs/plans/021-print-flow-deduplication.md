# Plan 021: Deduplicate Print and Image-Prepare Code Paths

## Goal

Collapse the two print entry points (`print_file`, `print_bytes`) and the two image-prepare entry points (`prepare_image`, `prepare_image_from_bytes`) so the post-decode protocol flow only lives in one place. Right now every change to the BLE flow has to be made twice, and they have already drifted in subtle ways.

## Current State

`crates/instantlink-core/src/device.rs:384-445`

- `print_file` and `print_bytes` are nearly identical. The only divergence is the call to `image::prepare_image` vs `image::prepare_image_from_bytes`.
- Everything after image preparation — LED handoff, the pre-execute delay, `PrintImage`, response matching — is duplicated.

`crates/instantlink-core/src/image.rs:153-192`

- `prepare_image` and `prepare_image_from_bytes` mirror each other; the only meaningful difference is `image::open(path)?` vs `image::load_from_memory(bytes)?`.

## Proposed Refactor

### Image layer

```rust
fn prepare_image_inner(img: DynamicImage, model: PrinterModel, fit: Fit, quality: u8) -> Result<...>;

pub fn prepare_image(path: &Path, model: PrinterModel, fit: Fit, quality: u8) -> Result<...> {
    prepare_image_inner(image::open(path)?, model, fit, quality)
}

pub fn prepare_image_from_bytes(bytes: &[u8], model: PrinterModel, fit: Fit, quality: u8) -> Result<...> {
    prepare_image_inner(image::load_from_memory(bytes)?, model, fit, quality)
}
```

### Device layer

```rust
async fn print_prepared(&self, jpeg: Vec<u8>, chunks: Vec<Vec<u8>>, opts: PrintOption, progress: &Progress) -> Result<()>;

pub async fn print_file(&self, ...) -> Result<()> {
    let prepared = image::prepare_image(...)?;
    self.print_prepared(prepared.jpeg, prepared.chunks, opts, progress).await
}

pub async fn print_bytes(&self, ...) -> Result<()> {
    let prepared = image::prepare_image_from_bytes(...)?;
    self.print_prepared(prepared.jpeg, prepared.chunks, opts, progress).await
}
```

## Implementation Scope

Primary:

- `crates/instantlink-core/src/device.rs`
- `crates/instantlink-core/src/image.rs`

No FFI signature changes — both wrappers remain.

## Testing

- Existing unit tests should keep passing without modification.
- Add one test that exercises the shared `print_prepared` path to confirm the refactor did not regress LED handoff or response matching.

## Risks

- Moving the post-prepare flow into a private method changes the borrowing pattern; double-check that progress callbacks still receive ownership in the same way.
- Watch for any subtle ordering differences — the audit noted these were "nearly identical" but did not exhaustively diff every line. Capture a snapshot of `cargo expand` output (or a manual line-by-line review) before/after.

## Rollout Order

1. Refactor `image.rs` first — it's the cleaner of the two.
2. Refactor `device.rs` next.
3. Run the full workspace test suite.
4. Hardware smoke test on at least one Mini Link, one Square Link, and one Wide Link.

## Exit Criteria

- One copy of the post-decode print sequence.
- One copy of the image-prepare pipeline.
- All tests still pass.
- Hardware print succeeds on every supported model.

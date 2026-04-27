# Plan 026: FFI Documentation Truth-Up

## Goal

Make our FFI documentation match the FFI we actually ship. Three places contradict each other today, and the reference docs omit two functions the macOS app already calls.

## Current Discrepancies

| Source | Claim | Reality |
|---|---|---|
| `CLAUDE.md:47` | "FFI loaded via dlopen/dlsym from InstantLinkFFI.swift (19 symbols)" | 22 exported functions |
| `README.md:92` | "20 exported functions" | 22 exported functions |
| `docs/reference/ffi.md` | Documents the base `_with_progress` variants | Missing `_with_progress_ctx` variants |

The `_ctx` variants (`instantlink_connect_named_with_progress_ctx`, `instantlink_print_with_progress_ctx`) accept an opaque context pointer alongside the callback. They are the ones the macOS app actually uses (see `macos/InstantLink/Core/AppRuntimeServices.swift` and the symbol-resolution block in `InstantLinkFFI.swift`).

## Proposed Updates

### CLAUDE.md

- Replace `(19 symbols)` with `(22 functions: 20 base + 2 progress-context variants)`.
- Add a one-line pointer to `docs/reference/ffi.md` for the canonical list so the count never drifts again.

### README.md

- Update the FFI feature blurb to match (`22 exported functions`).
- If we are listing functions, regenerate from `crates/instantlink-ffi/include/instantlink.h`.

### docs/reference/ffi.md

- Add full signatures for `instantlink_connect_named_with_progress_ctx` and `instantlink_print_with_progress_ctx`.
- Document the `*mut c_void` context pointer contract: lifetime, thread safety, when the FFI invokes the callback.
- Cross-reference `InstantLinkFFI.swift` so Swift integrators see how to translate the context pointer.

### Optional but recommended

- Add a script `scripts/check-ffi-doc.sh` that diffs the public extern "C" function list against `docs/reference/ffi.md` and fails CI on drift. Even a one-line `grep` script is fine.

## Implementation Scope

- `CLAUDE.md`
- `README.md`
- `docs/reference/ffi.md`
- `scripts/check-ffi-doc.sh` (new, optional)
- `.github/workflows/ci.yml` (only if we add the drift check)

## Testing

- Manual: rebuild the mkdocs site and confirm the new entries render.
- Optional: run the drift script locally to confirm the docs match the header.

## Rollout Order

1. Regenerate the FFI function list from the header.
2. Update each doc.
3. (Optional) add the drift-check script and wire it into CI.

## Exit Criteria

- Every doc that mentions the FFI count says 22.
- The reference doc lists every function declared in `instantlink.h`.
- The next time we add or remove an FFI function, the docs cannot silently fall out of sync (drift check, or at minimum a CONTRIBUTING note pointing to the header).

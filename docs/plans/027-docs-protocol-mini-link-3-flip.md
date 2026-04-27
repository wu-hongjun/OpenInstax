# Plan 027: Document Mini Link 3 Vertical-Flip and Verify Response Header

## Goal

Bring `docs/reference/protocol.md` up to par with the actual implementation. Two specific gaps were flagged in the audit:

1. The Mini Link 3 vertical-flip behavior is mentioned but not explained.
2. The response header opcode (`0x61 0x42`) has not been independently verified against the implementation or test fixtures.

## Current State

`docs/reference/protocol.md`

- Line 21: declares `Response: 0x61 0x42` without a citation.
- Line 122: notes Mini Link 3 uses a "vertically flipped upload" without saying:
  - how the model is detected (DIS hint `FI033` in `device.rs`)
  - what exactly is flipped (the JPEG row order, not the wire framing)
  - whether the printer expects this for every chunk or only the final upload
- Lines 54-58: the `InfoType` response format is described in prose without a byte-level table.

## Proposed Updates

### Mini Link 3 vertical flip

Add a subsection explaining:

- detection: device DIS hint `FI033` → `PrinterModel::MiniLink3`
- behavior: the JPEG payload is flipped vertically before fragmentation
- code reference: `crates/instantlink-core/src/image.rs` (the `prepare_image_inner` flip branch once Plan 021 lands) and `crates/instantlink-core/src/device.rs` (model dispatch)
- printer-side expectation: the flip is applied once at upload time; chunking and acknowledgement protocol are otherwise unchanged

### Response header verification

- Search `crates/instantlink-core/src/protocol.rs` for the constant; if it is named (e.g. `RESPONSE_HEADER`), cite it directly.
- Add a unit test that asserts the documented bytes match the constant. That way the doc is verified by code, not by prose.

### Status payload byte layout

Replace the prose description in lines 54-58 with a byte-by-byte table:

| Offset | Field | Notes |
|---|---|---|
| 0 | InfoType | as documented |
| 1..N | payload | depends on InfoType |
| ... | ... | ... |

Bit-level fields (`Printer Function Info`, bits 0-3 / bit 7) get their own sub-table with explicit MSB/LSB ordering.

## Implementation Scope

- `docs/reference/protocol.md`
- `crates/instantlink-core/src/protocol.rs` — only if we add the doc-test that pins the response header bytes

## Testing

- mkdocs site builds (`mkdocs build --strict`).
- Optional doc-test asserting the response header constant.

## Rollout Order

1. Read the actual code paths and harvest the correct values.
2. Update the doc.
3. If the response header is not yet a named constant, introduce one, and add the doc-test.
4. Ship.

## Exit Criteria

- Anyone reading `protocol.md` can implement Mini Link 3 support without reading our source code.
- Every wire-format constant in the doc maps to a named constant in the implementation.
- The byte-level layout is explicit enough that a fresh reader does not need to guess at field ordering.

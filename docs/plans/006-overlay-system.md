# Plan 006: Overlay System for macOS Editor

**Status:** Proposed

## Goal

Introduce a first-class overlay system for the macOS app so users can place reusable visual elements on top of the printable Instax image area. The first supported overlay types will be:

1. Text
2. QR code
3. Timestamp
4. Imported image

This should replace the current one-off date stamp model with a general system that is consistent across preview, editing, queue persistence, and print rendering.

## Why This Needs a Real System

The current date stamp is implemented as a special case in `ViewModel`, previewed through `DateStampOverlayView`, and rasterized separately in `stampImage()`. That approach will not scale to multiple overlay types, multiple overlays per photo, selection, ordering, or future history persistence.

The new system should treat overlays as part of each queue item's edit state, just like crop, rotation, flip, and film orientation.

## Product Scope

### In Scope

- Multiple overlays per photo
- Per-photo overlay persistence in the in-memory queue
- Live preview in main view and editor
- Print output that matches preview
- Overlay defaults for new photos
- Basic selection, move, resize, hide/show, duplicate, delete

### Out of Scope for First Pass

- Remote sticker packs or downloadable assets
- Rich text editing
- Arbitrary rotation per overlay
- Blend modes beyond opacity
- Disk-backed history or reusable user asset libraries

## Terminology

Use `Overlay` as the product term. It accurately covers timestamp, text, QR, and imported images. `Sticker` can be a future preset category implemented on top of overlays.

## Data Model

Add overlays to `QueueItemEditState` and `NewPhotoDefaults`.

```swift
struct OverlayItem: Identifiable, Codable, Equatable {
    var id: UUID
    var kind: OverlayKind
    var placement: OverlayPlacement
    var opacity: Double = 1.0
    var zIndex: Int = 0
    var isHidden: Bool = false
    var isLocked: Bool = false
}

enum OverlayKind: Codable, Equatable {
    case text(TextOverlayData)
    case qr(QROverlayData)
    case timestamp(TimestampOverlayData)
    case image(ImageOverlayData)
}

struct OverlayPlacement: Codable, Equatable {
    var normalizedCenter: CGPoint
    var normalizedSize: CGSize
    var anchor: OverlayAnchor = .center
}
```

### Type Payloads

- `TextOverlayData`: plain text, font family, font size factor, foreground color, optional background pill, text alignment, shadow, emoji optimization flag
- `QROverlayData`: payload string, foreground color, background color, error correction level, quiet zone, corner style if added later
- `TimestampOverlayData`: preset key, date format, show time, glow, source date
- `ImageOverlayData`: asset ID, content mode, corner radius, optional white backing

### Placement Rules

- Store overlay coordinates relative to the printable image area, not view pixels and not full film frame size.
- Normalize to `0...1` so preview and print use the same geometry.
- Keep film border and decorative frame outside the overlay coordinate system.

### Asset Strategy

Imported image overlays should reference a lightweight asset record:

```swift
struct OverlayAsset: Identifiable, Codable, Equatable {
    var id: UUID
    var sourceURL: URL?
    var bookmarkData: Data?
    var imageData: Data?
}
```

For the first pass, storing image data in memory inside the queue is acceptable. The model should still be `Codable` so it can move to disk-backed history later without a redesign.

## View Model Refactor

The current `ViewModel` exposes many flat date stamp fields:

- `dateStampEnabled`
- `showTimeRow`
- `dateStampPosition`
- `dateStampStyle`
- `dateStampFormat`
- `lightBleedEnabled`

That pattern should not be repeated for overlays.

### Target Shape

- Keep image-level transforms as direct `@Published` state: fit, crop, rotation, flip, film orientation
- Move overlays into a single published collection for the selected queue item
- Track only transient editor state separately:
  - `selectedOverlayID`
  - `hoveredOverlayID`
  - `isDraggingOverlay`
  - `overlayDraftKind` for add flow if needed

### Compatibility Strategy

Do this in two steps:

1. Add `overlays: [OverlayItem]` to `QueueItemEditState` and `NewPhotoDefaults`
2. Convert the old date stamp fields into a generated `timestamp` overlay during state load/save

That allows the UI and renderer to move first, then the old date stamp fields can be deleted once the migration is stable.

## Rendering Architecture

Preview and print must share one composition model.

### Core Rule

Do not keep a separate `DateStampOverlayView` path and a separate `stampImage()` path long term. That will drift.

### Proposed Layers

1. `BaseImageRenderState`
   - crop
   - fit
   - flip
   - rotation
   - film orientation
2. `OverlayRenderState`
   - resolved overlay frames in normalized print-area coordinates
3. `CompositedPrintCanvas`
   - final canvas size based on printer model

### Implementation Approach

- Introduce a shared overlay rendering layer for SwiftUI preview
- Introduce a matching Core Graphics / Core Image compositor for print preparation
- Apply overlays after crop/flip/rotation and before final film-orientation rotation

That ordering keeps on-screen preview aligned with exported print pixels.

### Technology Choices

- Text and timestamp rendering: `NSAttributedString` / Core Text
- QR generation: `CIQRCodeGenerator`
- Image overlays: `CGImage` drawing in the same compositor
- Preview: SwiftUI overlay views backed by the same normalized placement rules

## Editor UX

The editor should become the only place where overlays are created and adjusted. The main window should preview the result but not expose advanced overlay controls.

### Sidebar Structure

Replace `Date Stamp` with `Overlays`.

Inside `Overlays`:

- `Add Overlay` button with menu:
  - `Text`
  - `QR Code`
  - `Timestamp`
  - `Image`
- Overlay list showing:
  - type icon
  - title
  - hide/show
  - lock
  - delete

### Canvas Behavior

- Clicking an overlay selects it
- Drag to move
- Corner handles resize
- Snap to edges and corners with subtle guides
- `Delete` removes selected overlay
- `Command-D` duplicates selected overlay

### Inspector Behavior

Show controls based on selected overlay type.

#### Text

- Text field
- Font size
- Color
- Background style
- Opacity
- Alignment

Emoji should not be a separate overlay type. It should be a good text-overlay experience. Use Apple Color Emoji fallback when the text contains emoji, and keep font choices narrow in the first pass so mixed glyph rendering stays predictable.

#### QR Code

- Content field
- Size
- Foreground / background
- Quiet zone toggle
- Error correction level
- Optional `Open Link` validation affordance for URLs

#### Timestamp

- Preset strip
- Format
- Time on/off
- Glow on/off
- Position shortcuts

This is the direct replacement for the current date stamp feature.

#### Image

- Replace image
- Scale
- Opacity
- Corner radius
- `Fit` vs `Fill`
- Optional white backing for logo-like assets

## Main Window UX

Keep main-window controls light.

- Show overlays in preview
- Keep `Edit Image` as the entry point for overlay editing
- Do not add separate overlay CRUD controls to the main window

This keeps the top-level print flow fast and prevents the main surface from turning into a full design tool.

## Defaults for New Photos

The new defaults popover should gain an `Overlays` area later, but only for reusable defaults:

- default timestamp overlay
- default text overlay templates if we add them later

Do not enable default QR or default image overlays in the first pass. Those are too specific and will surprise users if they appear automatically on every new photo.

## Implementation Phases

### Phase 1: Model and Migration

- Add `OverlayItem`, payload types, placement model, and asset model
- Extend `QueueItemEditState` and `NewPhotoDefaults`
- Add compatibility conversion from old date stamp fields to a `timestamp` overlay
- Keep old UI working temporarily

### Phase 2: Shared Rendering

- Add a shared overlay placement helper
- Build print compositor for text, timestamp, QR, and image overlays
- Replace `stampImage()` with general overlay composition
- Add preview overlay container in main and editor film views

### Phase 3: Editor Foundations

- Add overlay selection state
- Add overlay list UI
- Add canvas move and resize behavior
- Add add/delete/duplicate/hide/lock actions

### Phase 4: Overlay Type UIs

- Text overlay editor
- Timestamp overlay editor
- QR code overlay editor
- Imported image overlay editor

### Phase 5: Defaults and Polish

- Add timestamp overlay defaults to `Defaults For New Photos`
- Add snapping guides
- Add keyboard shortcuts
- Add accessibility labels and localization strings

## Testing Plan

### Manual QA

- Add multiple overlays to one photo and switch queue items
- Print preview matches print output
- Rotated film orientation preserves overlay layout
- Crop + overlay combinations remain stable
- Deleting queue items does not leak selected overlay state
- QR codes remain scannable after composition
- Emoji text prints legibly on mini, square, and wide

### Unit / Snapshot Targets

- Timestamp migration to `OverlayItem`
- Overlay placement normalization and denormalization
- QR generation output size
- Print compositor ordering
- Queue-item persistence of overlays

## Risks

1. Preview/print mismatch if SwiftUI and Core Graphics layout drift
2. Mixed emoji and text font rendering can look inconsistent
3. Image overlays can inflate memory if raw data is duplicated per queue item
4. Gesture conflicts between crop interaction and overlay interaction

## Recommended Implementation Order

Start with `timestamp` and `text` overlays first. They exercise most of the architecture with the lowest asset complexity. Add `QR` next because Core Image makes it cheap. Add imported image overlays last because asset handling and memory management are the hardest part.

## Success Criteria

- Users can add, edit, reorder, hide, duplicate, and delete overlays per photo
- Timestamp is fully implemented as an overlay, not a special case
- Preview and print output match
- Overlay state persists across queue selection changes
- The editor remains understandable for quick single-photo edits

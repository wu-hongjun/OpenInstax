# Plan 037 — Settings audit cleanup

## Context

Direct user audit of System and Adjustments surfaces flagged six issues.
All six are concrete, scoped, and independent — no architecture rethink
required, just cleanup of rows that no longer earn their slot.

The audit findings:

1. **System → Battery** is dead on the current X306 hardware.
   `power/x306.py` documents that the X306 18650 UPS shield exposes
   no host-readable telemetry by design — only LED indicators. So the
   row always reads "Battery — LED only" on this hardware. The row is
   parameter-driven (would show a real percentage on PiSugar), but on
   X306 / NONE backends it's pure noise.

2. **System → Idle** and **Idle poweroff** overlap confusingly.
   The "Idle" row mixes live state (`active`/`dim`/`off`) with the
   poweroff timeout, producing strings like `dim 600s` or
   `active no-off`. The "Idle poweroff" row is the actual On/Off
   toggle. Two rows that both say "idle", one read-only with mixed
   semantics, one editable — the user can't tell what each does.

3. **System → Personalisation header** registers as clickable and
   shows "Info only" when activated. It was intended as a visual
   section divider (plan 036 phase 5, critic P2 follow-up) but lives
   in `INFO_SETTING_KEYS`, so K1 falls through to the generic info
   toast. Headers should be visually present but unselectable.

4. **Network → Diagnostics header** has the same problem as #3.
   So does **Auto print → Advanced**.

5. **Datestamp** and **Watermark** in Adjustments open the standard
   On/Off picker rather than the focused-edit mode with the live
   192×108 preview tile. The user can't see what either overlay
   looks like before committing. Slider rows already preview live;
   the toggles should too.

6. **Watermark position** is currently right-top. User wants
   bottom-left. The overlay renderer hard-codes only two anchors
   (`"rs"` right-bottom, `"rt"` right-top) — needs a third anchor
   path.

## Decisions (confirmed with user 2026-05-30)

- **#1 Battery row**: hide entirely on X306 / NONE backends. Show on
  PiSugar. Filter in `_visible_keys_for_page`.
- **#2 Idle consolidation**: drop `SYSTEM_IDLE_INFO` entirely. Reword
  `SYSTEM_IDLE_POWEROFF` value to `Off` / `After 10 min`.
- **#5 Toggle preview**: KEY1 toggles, KEY1-on-current commits. KEY2
  cancels and reverts. Same shape as slider edit mode.

## Phases

Each phase: gates green, deploy to Pi, `NRestarts=0`, no Traceback in
journal. Standard rules from plans 034 / 035 / 036.

### Phase 1 — Settings audit batch (#1 + #2 + #3 + #4)

Combined into one batch because all four touch
`bridge/src/instantlink_bridge/ui/settings.py` and
`bridge/src/instantlink_bridge/ui/controller.py`, and the changes are
small individually.

- **#1 Battery hide**:
  - Extend `_visible_keys_for_page(SettingsPage.SYSTEM)` to filter
    out `SYSTEM_BATTERY_INFO` when
    `config.power.backend in (PowerBackend.X306, PowerBackend.NONE)`.
- **#2 Idle consolidation**:
  - Remove `SYSTEM_IDLE_INFO` from
    `SETTINGS_BY_PAGE[SettingsPage.SYSTEM]`.
  - Remove `SYSTEM_IDLE_INFO` from `INFO_SETTING_KEYS` (no longer
    surfaced anywhere). Keep the enum value for one release as a
    no-op so any in-flight TOML/state references don't crash; mark
    it deprecated in the docstring.
  - In `_settings_row_for_key(SYSTEM_IDLE_POWEROFF)`: return
    `SettingsRow("Idle poweroff", "Off")` when disabled, or
    `SettingsRow("Idle poweroff", "After 10 min")` when enabled.
  - Delete `_idle_power_value` helper and the `_apply_idle_stage`
    snapshot field path for the settings row (status bar continues
    to show live stage independently).
- **#3 + #4 Section headers non-selectable**:
  - Define
    ```python
    SECTION_HEADER_KEYS: frozenset[SettingKey] = frozenset({
        SettingKey.NETWORK_DIAGNOSTICS_HEADER,
        SettingKey.PRINT_ADVANCED_HEADER,
        SettingKey.SYSTEM_PERSONALISATION_HEADER,
    })
    ```
    in `ui/settings.py`.
  - Remove the three header keys from `INFO_SETTING_KEYS`.
  - Add `SECTION_HEADER_KEYS` to `HANDLED_SETTING_KEYS` so the
    "unhandled" guard doesn't fire.
  - In `_handle_settings_action` UP/DOWN branch: after advancing
    `selected_index`, skip-forward while
    `keys[selected_index] in SECTION_HEADER_KEYS`. Direction-aware
    skip. Wrap at the page boundary; bail out after one full lap
    if the page is somehow all-headers (defensive — never happens
    in practice).
  - In `_activate_setting`: early return when
    `key in SECTION_HEADER_KEYS`. No toast, no nav change.
  - When `_show_settings` first lands on a page, if the persisted
    selection points at a header, advance to the next non-header
    row.
- **i18n**: existing translations cover the labels. Add
  `"After 10 min"` to the zh-Hans table (`"10 分钟后"`).
- **Tests**:
  - `test_visible_keys_hides_battery_on_x306`
  - `test_visible_keys_keeps_battery_on_pisugar`
  - `test_idle_poweroff_row_value_off`
  - `test_idle_poweroff_row_value_after_10_min`
  - `test_nav_skips_section_header` (UP and DOWN both directions
    across each header).
  - `test_activate_section_header_is_noop`
  - `test_initial_selection_skips_header_when_persisted_on_header`

### Phase 2 — Watermark moves to bottom-left (#6)

Isolated to `bridge/src/instantlink_bridge/imaging/postprocess.py` and
the help-text/docstring strings.

- Extend `_render_overlay` to handle anchor `"ls"` (left-bottom):
  `x, y = margin, h - margin`.
- Change the watermark call at line 202 from `anchor="rt"` to
  `anchor="ls"`.
- Update the field docstring on
  `AdjustmentProfile.watermark` from
  "renders it top-right" to "renders it bottom-left".
- Update `ADJUST_WATERMARK` help text in `ui/settings.py:466` from
  "Stamp a short label in the top-right corner" to
  "Stamp a short label in the bottom-left corner".
- Update `ui/i18n.py` translation for that help string.
- Tests:
  - `test_watermark_anchor_is_bottom_left` — check rendered text
    pixels are concentrated in the bottom-left quadrant.
  - `test_render_overlay_supports_ls_anchor` — direct unit test
    on `_render_overlay` with a known-size canvas.

### Phase 3 — Live preview for toggle overlays (#5)

Routes `ADJUST_DATESTAMP` and `ADJUST_WATERMARK` through the focused
edit mode so the user sees the rendered overlay before committing.

- Extend `_activate_setting` so toggle keys
  (`ADJUST_DATESTAMP`, `ADJUST_WATERMARK`) call
  `_enter_adjustment_edit(key)` instead of `_show_setting_picker(key)`.
- `_enter_adjustment_edit` already records the original value; extend
  it to read the current bool for toggles into
  `adjustment_edit_value` (e.g. `1` for True, `0` for False — keeps
  the field typed as int).
- New `_update_adjustment_edit_toggle` path: KEY1 / UP / DOWN flip
  the working bool. KEY2 cancels-and-reverts as today. KEY1 *with no
  change* commits — same semantics as slider commit.
  - Actually with KEY1 = toggle, we need a second KEY1 to commit
    after a toggle. Implementation: track "toggle has been pressed
    this session" — if KEY1 fired and working value differs from
    snapshot's original, KEY1 commits if equal to current config
    (i.e. user toggled back). Simpler model: KEY1 always toggles;
    the user presses KEY2 (cancel) to back out without writing, or
    UI auto-commits when the user navigates back via KEY2-as-back
    after a toggle. Reconsider during impl.
  - **Chosen model**: KEY1 toggles + commits in one step. Each KEY1
    press writes the new value and exits edit mode. KEY2 backs out
    without writing. This is the simplest mental model and matches
    "press button → setting changes" expectations.
- `_adjustment_edit` renderer detects toggle keys and renders a
  different bottom-row: no slider; just `Off` / `On` labels with
  the current value highlighted, and the help strip becomes
  `KEY1 toggle · KEY2 cancel`.
- The 192×108 preview tile renders with the working toggle value
  applied — for watermark, paints "Sample" (or
  `config.adjustments.watermark_text`) at bottom-left; for
  datestamp, paints today's date at bottom-right.
- The preview helper `render_adjustments_preview` already takes a
  full `AdjustmentProfile`; we just need to pass one that has the
  working toggle value applied and a non-empty
  `datestamp_text` / `watermark_text` for visibility.
- Tests:
  - `test_toggle_activate_enters_edit_mode_not_picker`
  - `test_toggle_key1_commits_and_exits`
  - `test_toggle_key2_cancels_without_commit`
  - `test_adjustment_edit_renders_toggle_row` (snapshot-style)

### Phase 4 — Customizable watermark + datestamp format presets

User feedback after Phases 1-3 landed:

> "Users would want to be able to customize the watermark and
> datestamp, nobody want an InstantLink watermark on the photo, it
> doesn't make sense. We might just borrow instantlink's
> implementation."

The macOS app already ships 5 `DateStampPreset` entries
(Quartz Date / Olympus / Contax / Modern / Lab Print) with custom
DSEG7 / Matrix fonts, glow, and light-bleed effects. The Pi can't
ship those exotic fonts, but it CAN port the layout / separator
identity of each preset so the macOS app and bridge speak the
same vocabulary.

Scope:

- **Drop the "InstantLink" default watermark.** `watermark_text`
  defaults to empty. When empty AND watermark=True, the overlay
  guard `if profile.watermark and profile.watermark_text:` already
  no-ops. So enabling the toggle with no text simply renders
  nothing — no more hardcoded brand on the photo.
- **Show the current text in the Watermark row value** so users
  see what's set without entering edit mode. Empty → "(no text)".
- **Datestamp format picker** — 5 presets matching macOS names:
  - Quartz Date (`YY.MM.DD`)
  - Olympus (`YY M D`)
  - Contax (`'YY Mᴹ D`)
  - Modern (`YY.MM.DD` — visually = Quartz on Pi without DSEG7)
  - Lab Print (`YY-MM-DD`)
- Bridge keeps both names so macOS app + bridge speak the same
  vocabulary, even though Quartz / Modern collapse visually on Pi.

#### Config

- `AdjustmentsConfig.watermark_text` default → `""` (was
  `"InstantLink"`).
- New `AdjustmentsConfig.datestamp_format: DatestampFormat`
  enum field, default `DatestampFormat.QUARTZ_DATE`.
- TOML round-trip + parse with safe default for missing field.

#### Postprocess

- New `format_datestamp(date, fmt) -> str` in
  `imaging/postprocess.py`. Pure function, 5-branch switch.
- Caller wiring: `read_exif_datestamp_text` (or the controller path
  that formats it) accepts a `DatestampFormat` and dispatches.

#### Settings UI

- New `SettingKey.ADJUST_DATESTAMP_FORMAT`. Insert into the
  ADJUSTMENTS page row tuple immediately after `ADJUST_DATESTAMP`.
- Picker with the 5 format names. Default selected reflects
  current config.
- Row value shows the current format name.
- `_settings_row_for_key(ADJUST_WATERMARK)`: when watermark is On
  AND text is non-empty, show value as `On · "Hello"` (truncated
  to fit). When On AND empty, `On · (no text)`. When Off, `Off`.
- Datestamp live-preview (Phase 3): format the placeholder date
  using the configured format so the preview matches what'll print.

#### Tests

- `test_watermark_text_default_is_empty`
- `test_format_datestamp_quartz_date` (and 4 more for each preset)
- `test_datestamp_format_round_trip_toml`
- `test_datestamp_format_picker_options`
- `test_adjustments_page_includes_datestamp_format_row`
- `test_watermark_row_shows_current_text_when_set`
- `test_watermark_row_shows_no_text_hint_when_empty`

### Phase 5 — Post-audit polish (15 items)

After Phases 1-4 shipped, two parallel fresh-eyes audits (designer +
adversarial critic) produced ~42 raw findings. A tracer agent
verified the highest-impact P1 claims: 1 confirmed actionable, 1
confirmed dead-but-inert, 3 falsified. The actionable subset became
this batch.

**P1 visual fixes**
1. Section headers (`SettingsRow.is_header` flag) — render at small
   font with no row-grid hairline so they read as dividers rather
   than greyed-out selectable rows.
2. Adjustments scrollbar — 2 px indicator on the card's right edge
   when `start > 0` or `start + visible_count < len(rows)`. The
   page has 10 rows after Phase 4; only ~8 fit visibly.
3. Empty preset slot picker hint dropped (was "KEY1 empty" — read
   as "press to do nothing").
4. Watermark row `On · "Hello"` translates the `On` prefix in
   zh-Hans via a new `SettingsRow.i18n_value_prefix` field. The
   render layer translates the prefix only; the user text stays
   verbatim.

**P2 visual fixes**

5. Chevron `›` reserved for `kind == "open"` rows. Action rows
   (Pair, Forget, Save current, etc.) drop the chevron and use
   `theme.accent_blue` (or `theme.accent_destructive`) label tint
   instead. The chevron stops lying about "this opens a new view".
6. Preset modified marker `*` → ` · edited` (textual badge,
   translatable via `t("edited", lang)`).
7. Edit-preview tile failure path — instead of `except: pass`,
   draw a cross-hatch + log WARNING once per session. Users now
   see "preview is broken" rather than "preview looks the same as
   identity".
8. "Wi-Fi Mode" row → "Camera link". The setting controls FTP
   receive path, not the bridge's Wi-Fi station/AP mode.
9. Drop duplicate "Searching" body title from `_printer_searching`;
   promote the actionable hint to the primary line.
10. zh-Hans "Press KEY1 again to delete CustomN" added for all 6
    slots (i18n parity with the overwrite variant).
11. KEY3 help text auto-clears on subsequent UP/DOWN in edit mode
    (was sticky).

**Cleanups**

12. Delete `SYSTEM_IDLE_INFO` enum — tracer confirmed unreachable.
13. Delete `_settings_default_message` helper — both branches
    returned `None`.
14. Drop trailing period from Hue help text (en + zh-Hans) for
    consistency with sibling rows.
15. zh-Hans translations for "Quartz Date" / "Modern" / "Lab Print"
    (descriptive English). "Olympus" / "Contax" stay Latin (real
    product brands).

#### Design contract additions

- **`SettingsRow.is_header: bool`** — explicit flag, replaces the
  brittle `hint == "" and value == ""` heuristic for header
  detection.
- **`SettingsRow.i18n_value_prefix: str`** — render layer
  translates the prefix only; the suffix (user text) stays
  verbatim. Used by the watermark row's `On · "..."` format.
- **Row kind discipline** — chevron `›` ONLY for `"open"` rows;
  `"run"` rows get `accent_blue` label; destructive runs get
  `accent_destructive`. Codifies plan 036 phase 5's "no chevron
  lies on read-only rows" rule for the action class.

#### Tests added

+28 tests delta (787 → 815). Coverage spans section-header
rendering, scroll cue, camera-link rename, Hue help period guard,
zh-Hans delete-confirm parity, datestamp format zh-Hans, toggle-edit
message clearing, watermark prefix translation, chevron-on-open
guard, preview tile placeholder, preset modified marker.

### Phase 6 — Tool-pin cleanup

The `b0eefa2` "clear mypy debt" commit on 2026-05-29 removed three
`cast(...)` calls that the then-current mypy / Pillow stub revision
treated as redundant. After phases 1-5 shipped, `mypy --strict`
flagged them again — Pillow 10.4 inline stubs return `Image | Any`
from `.convert()` and mypy 1.11.2 does not narrow `object → str`
on set-membership tests.

- `imaging/postprocess.py:478` — restore `cast(Image.Image, ...)`
  around the example-photo resize+convert chain.
- `imaging/pipeline.py:498` — restore `cast(Image.Image, ...)`
  around the HIF thumbnail convert.
- `update/signing.py:473` — restore `cast(str, explicit_kind)`
  inside the membership-check branch.

Each cast carries a comment explaining when it was removed and
why it had to come back, so the next "redundant cast" cleanup pass
doesn't repeat the cycle.

The `SKIP_HOOK no longer needed` memory was true at `b0eefa2`
and stays true after this phase — both mypy strict and ruff are
clean again.

## Out of scope

- Custom datestamp fonts (DSEG7 / MatrixSans) — macOS-only for now.
- Datestamp colour / glow / light-bleed effects — macOS-only.
- A bridge-side text editor for watermark text. Power users edit
  `/etc/InstantLinkBridge/config.toml` or push the value via the
  macOS management API (plan 029 / 030).
- Status bar battery indicator (already exists, separately driven).
- PiSugar telemetry rendering on the row when present (already works).
- Headless SKU (plan 033 phase 5).

## Verification

Per phase:
- `cargo fmt && cargo clippy --workspace -- -D warnings && cargo test --workspace`
  (no Rust changes expected but keep gates green).
- `/tmp/bridge-verify/bin/python -m pytest bridge/tests/ -q --timeout=10 --timeout-method=thread`
- mypy strict + ruff over `bridge/src` and `bridge/tests`.
- Deploy to Pi via `bridge/scripts/deploy-to-pi.sh --restart`,
  confirm `systemctl is-active` and zero NRestarts after 30s,
  no `Traceback` in `journalctl -u instantlink-bridge.service`.

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

## Out of scope

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

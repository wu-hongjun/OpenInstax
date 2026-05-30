# Plan 034 — Bridge UX polish pass (multi-agent audit follow-up)

## Context

Two specialist agents (designer + critic) audited the 240×240 LCD UI in the
state delivered by plans 025-033. They returned 19 prioritised findings
spanning visual polish, IA, copy, dark-theme legibility, first-boot
onboarding, and destructive-confirm affordance. This plan catalogues all
findings, groups them into five executable batches, and assigns each batch
to a single executor agent.

Constraints carried into every batch:

- `cargo`-side gates do not apply — this plan is bridge-Python only.
- After each batch: `python -m pytest -q --timeout=10 --timeout-method=thread`,
  `python -m mypy src`, `python -m ruff check src tests` must all be green.
- No `SKIP_HOOK=1` on commits — both gates are clean today (see memory
  `skip-hook-not-needed`) and must stay clean.
- Conventional commits (`feat:`, `fix:`, `refactor:`, `style:`, `docs:`).
- Preview-render verification for any visual change; deploy to Pi
  (192.168.7.1, user `hongjunwu`) and confirm `NRestarts=0` after each
  batch.

## Findings catalogue (19 items)

### P1 — visible at arm's length, low cost

| # | Title | Source files | Source agent |
|---|---|---|---|
| 1a | SETTINGS pill is yellow; should be blue (yellow ≡ warning) | `ui/render.py:draw_status_bar`, `ui/status_indicator.py`, `ui/theme.py` | designer |
| 1b | Status pill always says "Settings" — sub-page name (Print, Network, …) never reaches it | `ui/render.py:draw_status_bar`, controller wiring of `settings_title` | critic |
| 2  | NEEDS_PAIRING hint says `"Hold KEY3"` but short-press is a silent no-op and Hold-target is unstated | `ui/controller.py:_mode_hints`-equivalent, `ui/render.py:_needs_pairing` | critic |
| 3  | `Refresh status` row value renders the literal word `"run"` (every other action row is empty) | `ui/controller.py:_settings_row_for_key` | critic |
| 4  | zh-Hans untranslated fragments: `"Find printer"`, `"Replace film pack"`, `"Wait for printer status"`, `"Choose FTP Wi-Fi"`, `"No-film test is in Settings"` | `ui/i18n.py` | critic |
| 5a | Dark `pill_bg_yellow = #D4B20D` reads muddy/brown — promote toward `#E6B800` (still ≤82 % saturation but reads as yellow) | `ui/theme.py:DARK_THEME` | designer |
| 5b | Dark `separator = #38383A` on `surface = #1C1C1E` is invisible at ST7789 contrast — bump to `#48484A` (Apple's dark separator token) | `ui/theme.py:DARK_THEME` | designer |
| 6  | Status pill optical centring — shift `pill_y` by +1 px so the pill floats with 8 px above / 6 px below instead of 7 px both sides | `ui/render.py:draw_status_bar` | designer |

### P2 — structural, worth doing soon

| # | Title | Source files | Source agent |
|---|---|---|---|
| 7  | READY card duplicates printer identity: `Type: INSTAX-…` + `Printer: …`. Bare-serial Printer row is redundant; replace with FTP host / SSID the user actually needs during camera setup. Also fixes the BLE-name leak (`INSTAX-XXXXXXXX`) when model is None. | `ui/render.py:_ready`, helper at `_status_bar_printer_name` | critic |
| 8  | Print-complete temporal vocabulary contradicts itself: pill `Done`, title `Sent`, body `Film should feed now`. Unify around a single tense. | `ui/render.py:_print_complete`, `ui/status_indicator.py`, `ui/i18n.py` | critic |
| 9  | Network page interleaves camera-setup credentials with diagnostic rows. Block all five FTP credentials at top, push diagnostics below. | `ui/settings.py:SETTINGS_BY_PAGE`, `ui/controller.py` | critic |
| 10 | Destructive confirm toast renders identical to help text — no visual affordance that KEY1 is now armed | `ui/render.py:_settings` (toast color), `ui/controller.py` (toast plumbing) | critic |
| 11 | Hint bar key labels (`KEY1`/`KEY2`/`KEY3`) and action labels (`Setting`/`Back`/`Help`) share the same `hint_fg` tint — no hierarchy. Promote key label to `label_primary`. | `ui/render.py:draw_hint_bar` | designer |
| 12 | READY split-row label/value contrast collapses at small font; the `Film:`/`8/10` colour gap reads as one muted line | `ui/render.py:_ready` (split-row drawing) | designer |
| 13 | zh-Hans pill is ~21 % narrower than English ("设置" hits 60 px floor; "Settings" gets 76 px) — looks cramped. Raise CJK floor to `max(76, word_w + 32)`. | `ui/render.py:draw_status_bar` | designer |

### P3 — polish nice-to-have

| # | Title | Source files | Source agent |
|---|---|---|---|
| 14 | Print-complete and Image-received screens have an empty 40 px bottom band — render an `"Auto"` placeholder so geometry doesn't read as broken | `ui/render.py:_print_complete`, `_image_received` | designer |
| 15 | "Setup needed" screen has a 37 px gap between title and `Next action` label — tighten by 6 px so the label binds to its cause list | `ui/render.py:_validation` | designer |
| 16 | Settings row value column can crash into the chevron at LARGE font scale; bump `value_right = marker_x - 6` and assert min 40 px value field | `ui/render.py:draw_settings_row` | designer |
| 17 | Help-text casing drifts between sentence case and Title Case (`"Quick BLE reconnect, no re-pair"` vs `"Bridge health and updates"`) — one voice pass | `ui/settings.py:SETTING_HELP_TEXT`, `ui/controller.py` dynamic help branches | critic |
| 18 | `Keepalive` / `Search rate` are developer jargon — rename to user-facing equivalents OR bury under an `Advanced` separator | `ui/controller.py:_settings_row_for_key`, `ui/i18n.py` | critic |
| 19 | zh-Hans Appearance help text uses ASCII colon + half-width comma — drifts from the rest of the Chinese corpus that uses full-width punctuation | `ui/i18n.py` | critic |

## Execution plan — five batches

Batches are ordered so file conflicts are minimised and earlier work
underpins later work (e.g. dark-theme tokens land before the print-complete
rewrite that references them).

### Batch 1 — Status semantics + dark-theme tokens (items 1a, 1b, 5a, 5b, 6, 11, 13, 16)

Foundational: every later batch reads these tokens / pill rules.

- Files: `ui/theme.py`, `ui/render.py` (status bar + hint bar + settings row),
  `ui/status_indicator.py` (route SETTINGS to blue), `ui/controller.py`
  (forward `settings_title` into the snapshot so the pill can read it).
- Outcome: SETTINGS pill is blue everywhere; on sub-pages the pill text
  reads the sub-page name (Print / Network / System / About /
  Accessibility); pill optically centred; CJK pill floor raised; key label
  in hint bar is primary-tinted; dark theme separator + yellow bumped to
  iOS-tokenised values; settings row chevron gap widened with a minimum
  value field width.

### Batch 2 — First-boot + READY polish (items 2, 3, 7, 12)

User-facing copy and the first-boot path. Depends on batch 1's pill
plumbing for the sub-page name change.

- Files: `ui/controller.py` (`Refresh status` row value, KEY3 hint
  rewrite, READY card row composition), `ui/render.py:_ready` and
  `_needs_pairing`, `ui/i18n.py` (any new strings).
- Outcome: NEEDS_PAIRING hint reads "Hold K3 → Pair" (or short-press KEY3
  also pairs, your choice — pick whichever lands first); `Refresh status`
  value is empty; READY card drops the bare-serial Printer row and adds
  an FTP / SSID row that's actually useful during camera setup; never
  leaks `INSTAX-…`; split-row contrast hierarchy lifted (value in body
  font).

### Batch 3 — Print-complete vocabulary unification (items 8, 14)

Self-contained renderer fix; can run after batch 1's pill tokens land.

- Files: `ui/render.py:_print_complete` + `_image_received`,
  `ui/status_indicator.py` (status state mapping for PRINT_COMPLETE),
  `ui/i18n.py`.
- Outcome: Pill / title / body share one tense; "Sent" / "Done" /
  "should feed now" contradictions removed; print-complete and
  image-received bottom band populated.

### Batch 4 — Network page restructure + destructive-confirm differentiation + help-text voice (items 9, 10, 17, 18, 19, 15)

Bundle the IA + voice work; touches `ui/settings.py` and `ui/controller.py`
heavily, plus i18n.

- Files: `ui/settings.py` (SETTINGS_BY_PAGE order, advanced separator if
  needed), `ui/controller.py` (toast plumbing for destructive-confirm tint,
  any label renames), `ui/render.py:_settings` (red-toast colour token,
  validation screen vertical rhythm), `ui/i18n.py` (Chinese punctuation
  audit + new strings).
- Outcome: camera-setup credentials block at top of Network; Reset
  credentials destructive-confirm toast renders in red (not yellow); help
  text passes a sentence-case audit; "Keepalive"/"Search rate" renamed
  OR moved below an "Advanced" separator; zh-Hans Appearance help fixed
  to use full-width punctuation; validation-screen title↔label gap
  tightened.

### Batch 5 — zh-Hans translation sweep (item 4)

Pure-i18n batch; runs cleanly in parallel with any of the others if needed,
but trivial to land sequentially.

- Files: `ui/i18n.py`, possibly a `tests/test_i18n.py` coverage assertion.
- Outcome: every string sent through `t()` from the unpaired / validation /
  no-film / pairing-failed paths has a zh-Hans entry; no English fragments
  embedded in zh-Hans screens.

## Acceptance criteria (every batch)

1. `python -m pytest bridge/tests/ -q --timeout=10 --timeout-method=thread` → all green
2. `python -m mypy src` → `Success: no issues found`
3. `python -m ruff check src tests` → `All checks passed!`
4. Preview-render verification for visual changes (PNG diff or eye-ball)
5. Deploy to Pi succeeds; `NRestarts=0`; no `Traceback` / `ERROR` in journal post-deploy
6. Conventional commit on `main` with a message that links back to this plan
   number and the finding IDs the commit closes.

## Mid-execution redirection (2026-05-29)

After Batch 1 (`d0b034a`) and Batch 2 (`5c84232`) landed, the user
re-scoped the status-bar treatment:

> "Probably a better way to do this indicator is: Top Left aligned
> 'Settings' text, no pill. Then, top center, a circular filled circle
> indicate the status, but do not have any text."

This separates two concerns the pill had been conflating:

- **Where you are** → top-left title text (no chip), driven by
  `status_bar_word(snapshot)` / `snapshot.settings_title`.
- **Whether things are OK** → top-center filled circle (12 px diameter),
  no text. The circle uses the same green/yellow/red/blue palette and
  breath modulation the pill carried.

Items rendered partially obsolete by the redesign (the underlying intent
is preserved, but the surface changed):

- **1a** ("SETTINGS pill is yellow") — still solved: SETTINGS routes to
  the neutral-blue `StatusSignal.NEUTRAL` introduced in batch 1, which
  now drives the *dot* colour instead of the pill.
- **1b** ("sub-page name in pill") — still solved: `settings_title` now
  drives the top-left *title text* instead of the pill word.
- **6** ("pill optical centring") — title + dot pick up the same +1
  optical-centring shift; the original pill code is gone.
- **13** ("CJK pill width") — no longer applicable; there is no pill
  whose width depends on glyph width. The title text just wraps to its
  natural width and the dot is glyph-independent.

The redesign lands as a separate commit (`refactor(bridge/ui): replace
status pill with title-text + status-dot`) after Batch 2 and before
Batch 3. Batches 3-5 still apply unchanged.

## Out of scope

- Headless SKU (Plan 033 Phase 5) — separate batch
- Mac app virtual UI (Plan 033 Phase 3) — separate batch
- Configurable Auto-appearance schedule — separate batch
- ja / ko i18n expansion — separate batch
- Bench harness — separate batch

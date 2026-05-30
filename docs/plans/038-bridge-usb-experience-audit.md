# Plan 038 — Bridge USB-plug-in experience: audit + Mac control surface

## Audit (2026-05-30)

The single largest UX gap in the project: **plan 029 Phase 1 shipped
the bridge management contract and the Mac transport layer, but
Phases 2-5 (the entire macOS Bridge Control UI) never landed.**

### What actually happens today

| Step | Reality |
|---|---|
| Plug Pi into Mac via USB-C | USB gadget enumerates → `usb0` UP → `192.168.7.1` reachable |
| Open InstantLink.app | App opens to Print main view. Zero mention of Bridge. |
| Find Bridge in app | Not possible. No menu item, no preference pane, no discovery. |
| Configure Wi-Fi / FTP / printer model | Not possible via app. Requires SSH + TOML edit + service restart. |
| Update firmware | Not possible via app. Requires `deploy-to-pi.sh` from a repo checkout with password auth. |
| Diagnostics | Not possible via app. SSH + `journalctl` only. |

### What is built (the iceberg)

**Bridge side — fully shipped:**

- `instantlink-bridge-manager.service` running as its own systemd
  unit, independent from the print bridge.
- Listening on `192.168.7.1:8742` (USB) AND `192.168.8.1:8742`
  (Bridge Wi-Fi). Verified live: 14h uptime, `NRestarts=0`.
- Discovery contract: `GET /v1/hello` returns rich device identity
  (device_id, software_version, network labels, pairing state,
  management public-key fingerprint) — anonymous-readable per
  design. Live verification:
  ```
  $ curl http://192.168.7.1:8742/v1/hello
  {"device":{"device_id":"IB-7B4233E7","software_version":"0.1.16",
   "is_paired":false,"pairing_open":false,"network_labels":[
   "Bridge Wi-Fi","USB IP","Same-Wi-Fi"]},...}
  ```
- Pairing contract: `GET /v1/pairing/status`, `POST /v1/pairing/complete`.
- Admin route surface (signed-request auth required):
  `/v1/status`, `/v1/config` (GET+PUT), `/v1/network/mode`,
  `/v1/printer/scan`, `/v1/printer/select`, `/v1/printer/forget`,
  `/v1/backup/create`, `/v1/backup/restore`, full update flow
  (preflight / upload / start / status / events / mark-good /
  rollback).
- `bridge/src/instantlink_bridge/manager/` ships 12 Python files:
  `api.py`, `auth.py`, `contract.py`, `status.py`, `backup.py`,
  `installer.py`, `update_flow.py`, `release_slots.py`, `health.py`,
  `cli.py`, `signing.py`, `__init__.py`.

**Mac side — transport built, UI absent:**

| File | Lines | Status |
|---|---|---|
| `Core/BridgeAuth.swift` | 243 | Ed25519 signer ready |
| `Core/BridgeHTTPTransport.swift` | 327 | HTTP client matching the contract |
| `Core/BridgeTransport.swift` | 440 | Protocol + in-memory mock |
| `Core/BridgeModels.swift` | 854 | Complete Codable type tree |
| `Core/BridgeFirmwareBundle.swift` | 89 | Local firmware bundle reader |
| `Tests/BridgeHTTPTransportTests.swift` | — | Round-trip tests against contract |
| `Features/Bridge/` | — | **DOES NOT EXIST** |
| Bridge Control menu item | — | **NOT PRESENT** in `InstantLinkApplication.swift` |
| Discovery probe on launch | — | **NOT WIRED** |
| Pairing UI | — | **NOT BUILT** |
| Settings editor | — | **NOT BUILT** |
| Update preflight + install UI | — | **NOT BUILT** |
| Backup / restore UI | — | **NOT BUILT** |
| Diagnostics + log viewer | — | **NOT BUILT** |
| Recovery assistant | — | **NOT BUILT** |

Grep across `macos/InstantLink/Features/`: every reference to
`BridgeTransport` / `BridgeHTTPTransport` / `BridgeAuth` /
`BridgeModels` is in `Core/Bridge*.swift` itself or in
`macos/Tests/`. Zero feature-layer callers.

### Findings

**P0 — Feature paid for but not shipped.** The plan 029 architecture
(specs + contract + transport + auth + models + tests) is all here.
The product surface is missing. Single largest UX gap.

**P1 — Power-user-only configuration.** Today's only configuration
path is SSH + TOML edit. `docs/plans/029-bridge-control-panel.md`
non-negotiable: *"Do not require users to type SSH usernames, SSH
passwords, or shell commands."* — current state violates its own
non-negotiable.

**P1 — Discovery is trivial and not wired.** A ~20-line
`URLSession` probe of `http://192.168.7.1:8742/v1/hello` on app
launch would tell the user "InstantLink Bridge IB-7B4233E7
connected" and close the largest gap.

**P1 — Pairing security model unused.** Bridge enforces signed
requests; Mac has the Ed25519 signer; bridge enforces an
LCD-confirmed pairing window. None of this is reachable.

**P2 — Update flow shipped on bridge, not exposed on Mac.**
Plan 030 (secure update) added preflight / upload / install /
mark-good / rollback with full A/B slot semantics. Transport maps
to it. No user surface.

**P2 — Network mode switching shipped, not exposed.**
`/v1/network/mode` accepts HOTSPOT/PEER/WIRED. Either know the
TOML key or fiddle with the LCD; Mac would be a far better surface.

---

## Scope (user-confirmed 2026-05-30)

Full plan 029 phases 2-5: discovery + pairing + settings editor +
updates + backup + diagnostics + recovery. The transport layer is
ready; this plan is the missing UI.

### Architecture

The macOS app uses:

- `@main InstantLinkApp` with `@StateObject private var viewModel = ViewModel()`
- Domain coordinators in `Core/*Coordinator.swift`
  (`@MainActor final class : ObservableObject`, callback struct +
  snapshot struct + status message struct, see
  `PrinterConnectionCoordinator.swift` for the canonical shape).
- Feature directories under `Features/{Camera,Editor,Main,Settings}/`.

Mirror that for the Bridge surface:

- `Core/BridgeControlCoordinator.swift` — owns discovery loop,
  pairing state, fetched `BridgeStatus`, signed-request lifecycle.
  Drives the visible UI via a published `BridgeControlSnapshot`.
- `Features/Bridge/` directory with views.
- `viewModel.bridgeCoordinator` published, so the main Print view
  can show a passive "Bridge connected" banner.
- Status-bar menu item `Bridge Control...` opens a separate
  `WindowGroup("BridgeControl")` window.

### Phase A — Foundation: discovery + pairing + status

Goal: when the user plugs the Pi into a Mac running InstantLink,
within 3 s the app shows "Bridge IB-7B4233E7 — v0.1.16 — connected
via USB" as a passive banner in the main view, and a new
`Bridge Control...` menu item opens a window that shows live
`BridgeStatus`. First-run flow walks the user through pairing
(LCD shows a 6-digit code; Mac enters it; identity saved to
Keychain).

**Files to create:**

- `macos/InstantLink/Core/BridgeControlCoordinator.swift` —
  the coordinator. Owns:
  - Discovery loop (probe `192.168.7.1:8742/v1/hello` and the
    Bridge Wi-Fi address on a timer; on hit, hold the device
    identity).
  - Pairing state machine (UNPAIRED → PAIRING_OPEN → AWAITING_CODE
    → COMPLETING → PAIRED; failures: WINDOW_CLOSED, BAD_CODE,
    TIMEOUT).
  - Live status polling once paired (5 s tick).
  - Public published `BridgeControlSnapshot { discovery: Discovery,
    pairing: PairingState, status: BridgeStatus? }`.
- `macos/InstantLink/Features/Bridge/BridgeControlWindow.swift` —
  window shell, hosts the three views.
- `macos/InstantLink/Features/Bridge/BridgeOverviewView.swift` —
  read-only status panel: device, network labels, paired printer,
  film count, recent uploads, software version, manager service
  health.
- `macos/InstantLink/Features/Bridge/BridgePairingView.swift` —
  pairing wizard.
- `macos/InstantLink/Features/Bridge/BridgeDiscoveryBanner.swift` —
  the in-Print-view passive banner.
- `macos/InstantLink/Features/Bridge/BridgeKeychain.swift` —
  thin wrapper around `Security.framework` for the per-Bridge
  signing identity. Keyed by `device_id`.

**Implementation outline:**

- `BridgeControlCoordinator.init(transport: BridgeTransport)` —
  inject so tests use the in-memory mock.
- Discovery probe: parallel `URLSession.dataTask` to all known
  fallback addresses (`192.168.7.1`, `192.168.8.1`, Bonjour-found).
  First success wins; cache for 30 s.
- Pairing wizard flow:
  1. Coordinator polls `/v1/pairing/status` once.
  2. If `pairing.open == false`, instruct user: "Open Bridge LCD
     → Settings → Network → Authorize Mac (hold KEY3)". The Pi
     opens a 60 s window and shows a 6-digit code.
  3. Once `open == true`, show a 6-digit input field on Mac.
  4. On submit: generate Ed25519 keypair, call
     `POST /v1/pairing/complete` with public key + code +
     proposed display name.
  5. On 200, store private key under
     `keychain://bridge/<device_id>/signing_key`; persist
     `paired_at`, `display_name`, `device_id`.
- Status polling: signed `GET /v1/status` every 5 s while window
  is foreground; pause when window hides.
- Main-view banner: `BridgeDiscoveryBanner` reads
  `viewModel.bridgeCoordinator.snapshot.discovery` — passive,
  no input, just a fact.
- Add menu item in `InstantLinkApplication.swift`:
  ```swift
  CommandMenu("Bridge") {
      Button("Open Bridge Control…") { … }
          .keyboardShortcut("B", modifiers: [.command, .shift])
  }
  ```
  Or add to the status-bar menu (preferred per plan 029).

**Acceptance criteria:**

- Plug Bridge into Mac, open InstantLink.app: within 3 s the
  Print view shows a small "Bridge IB-7B4233E7 — connected"
  banner with discovery details.
- `⌘⇧B` (or status-bar menu) opens the Bridge Control window.
- Window shows the Overview tab populated from `/v1/status`.
- First-run pairing wizard succeeds end-to-end: LCD code matches,
  signature stored, subsequent `GET /v1/status` succeeds.
- Unplugging Bridge: banner fades to "Bridge disconnected" within
  10 s; Overview switches to a disconnected state.
- Re-plugging: identity is recovered from Keychain; pairing not
  re-required.

**Tests:**

- `BridgeControlCoordinatorTests` — discovery, pairing state
  machine, status polling, using `InMemoryBridgeTransport`.
- `BridgePairingViewModelTests` — code validation, retry.
- `BridgeKeychainTests` — round-trip read/write/delete on a
  test keychain.

**Out of scope (this phase):** settings edit, updates, backup,
diagnostics, recovery.

### Phase B — Settings editor: typed Apply-to-Bridge

Goal: user sees the bridge's current sanitized config and edits
the settings the macOS app legitimately owns
(printer / network / FTP / power knobs) via typed controls, not
raw TOML. "Apply to Bridge" submits a diff via `PUT /v1/config`.

**Files to create:**

- `Features/Bridge/BridgeSettingsView.swift` — the settings tab.
- `Features/Bridge/BridgeSettingsSection.swift` — reusable section
  matching the existing `SettingsViews.swift` styling.
- `Core/BridgeSettingsDraft.swift` — observable draft model;
  diffs against last-fetched `BridgeStatus.config`.

**Implementation outline:**

- Coordinator fetches `GET /v1/config` on settings tab open.
- `BridgeSettingsDraft` holds editable copies; tracks `isDirty`.
- "Apply to Bridge" button enabled iff dirty; on click, builds
  the diff payload (`{section: {key: value}}`), signs, POSTs.
- Show a side-by-side "current / pending" preview before submit
  (Plan 029's "settings diff/review" requirement).
- On 200, refetch status to confirm; on 4xx with
  `ManagementValidationError`, surface the field-level errors
  inline.
- On 5xx or network error, show `Management service unavailable`
  toast (Plan 029 verbatim copy).

**Sections to cover (per `manager/contract.py` config surface):**

- Printer: model, fit mode, JPEG quality, auto-print delay,
  allow-print-without-film, keepalive interval, search interval.
- Network: FTP receive mode, Bridge Wi-Fi PIN (write-only,
  masked), Same-Wi-Fi credentials (write-only, masked).
- FTP credentials: username, password (write-only, masked).
- Power: idle poweroff enabled + timeout, battery thresholds.
- UI: appearance, font size, language.
- Adjustments: watermark text, datestamp format (already a real
  picker on the bridge; mirror).

**Acceptance criteria:**

- Settings tab populates from `GET /v1/config` within 1 s.
- Edit a field → "Apply" button activates.
- Apply submits diff; status refresh confirms; toast: "Bridge
  settings updated".
- Invalid input (e.g. negative keepalive) is blocked client-side
  with inline error.
- Server-side validation error surfaces with field highlight.

**Tests:**

- `BridgeSettingsDraftTests` — dirty tracking, diff generation,
  field validation.
- `BridgeSettingsApplyFlowTests` — happy path, validation error,
  network error.

**Out of scope:** updates, backup, diagnostics.

### Phase C — Updates: preflight + upload + install + rollback

Goal: when an InstantLinkBridge update is bundled into
InstantLink.app, the user sees "Update available" on the Bridge
Control overview, clicks through preflight, watches install
progress, and the bridge reconnects with the new version. If
verification fails, rollback is one click.

**Files to create:**

- `Features/Bridge/BridgeUpdateView.swift` — update tab.
- `Features/Bridge/BridgeUpdatePreflightView.swift` — preflight
  checks list.
- `Features/Bridge/BridgeUpdateProgressView.swift` — live
  progress feed.
- `Core/BridgeUpdateCoordinator.swift` — separate coordinator for
  the multi-stage flow; child of `BridgeControlCoordinator`.

**Implementation outline:**

- Read bundled firmware bundle via existing
  `BridgeFirmwareBundle.swift` (`Resources/BridgeFirmware/*.tar.zst`).
- Compare bundled `software_version` with
  `BridgeStatus.software_version`.
- If newer: show "Update available — v0.1.x → v0.1.y".
- Click "Preflight" → `POST /v1/update/preflight` → list checks
  (battery, disk space, network mode, BLE state, queue empty).
- Click "Install" → `POST /v1/update/upload` (multipart stream
  with progress), then `POST /v1/update/start` → subscribe to
  `GET /v1/update/events` (SSE).
- Display each event (`DOWNLOADING_ARCHIVE`, `VERIFYING_SIGNATURE`,
  `STAGING_NEW_SLOT`, `SWAPPING_ACTIVE_SLOT`, `RESTARTING_SERVICES`,
  `RECONNECTING`).
- On `RECONNECTING`: wait for `/v1/hello` to return with the new
  version; on success: "Verifying..." → `POST /v1/update/mark-good`
  → "Up to date".
- On health-check failure during reconnect: show "Update failed —
  bridge rolled back" with the journal excerpt.
- "Rollback to previous version" button on the overview when
  `BridgeStatus.update.previous_slot` is populated.

**Acceptance criteria:**

- Preflight surfaces all checks with pass/fail icons.
- Install progress shows percentage during upload, then named
  phases during install.
- Reconnect timeout (90 s) shows clear "Bridge didn't come back"
  state with diagnostics.
- Successful update: version flips on overview, "Up to date" badge.
- Rollback button: visible iff a previous slot exists; one click
  + confirm.

**Tests:**

- `BridgeUpdateCoordinatorTests` — happy path, preflight fail,
  upload fail, install fail, reconnect timeout, rollback.
- Script-driven `InMemoryBridgeTransport.scheduleUpdateScript([…])`
  for deterministic event sequences.

**Out of scope:** backup, diagnostics.

### Phase D — Backup & restore

Goal: user can create an encrypted backup of bridge identity +
credentials + paired printer to a local file and restore from
that file to the same or another bridge.

**Files to create:**

- `Features/Bridge/BridgeBackupView.swift`.
- `Core/BridgeBackupCoordinator.swift`.

**Implementation outline:**

- "Back up Bridge..." → `POST /v1/backup/create` → bridge returns
  a server-side bundle ID + download URL.
- Mac downloads + saves to user-chosen path; warns that the
  bundle includes secrets.
- Encryption: server-side encrypts with a user-provided
  passphrase passed in the create call.
- Restore: user picks a file + enters passphrase → upload via
  `POST /v1/backup/restore` → bridge restarts → reconnect.
- Restore-to-different-bridge prompts: "This will overwrite the
  current bridge identity. Continue?" — extra confirmation.

**Acceptance criteria:**

- Create: prompts passphrase, downloads `.bridgebackup` file,
  succeeds offline-replayable.
- Restore: re-creates the bridge state; pairing identity matches;
  paired printer survives.
- Cross-bridge restore: overwrites identity with confirmation.

**Tests:** standard happy-path + error-path coverage.

**Out of scope:** diagnostics, recovery.

### Phase E — Diagnostics + recovery

Goal: a "Bridge logs" tab shows the last 200 lines of the journal
in real time. A "Support bundle..." button packages a redacted
zip the user can mail to support. A "Recovery" panel handles the
boot-failure state when the bridge management service is
unreachable.

**Files to create:**

- `Features/Bridge/BridgeDiagnosticsView.swift`.
- `Features/Bridge/BridgeRecoveryView.swift`.
- `Core/BridgeDiagnosticsCoordinator.swift`.

**Implementation outline:**

- Live logs: subscribe to `GET /v1/logs/stream` (SSE) when on the
  Diagnostics tab; cap to last 200 entries; tail-follow.
- Support bundle: `POST /v1/support_bundle/create` → download +
  save. Redacted per the `support-bundle.md` policy
  (`docs/plans/029-bridge-control-panel.md` §"Optional encrypted
  backup").
- Recovery: when `/v1/hello` is unreachable but USB carrier is
  up, show "Bridge management service unavailable. Uploads may
  still work. Restart the Bridge service or reconnect with USB
  debug." (verbatim plan 029 copy).
- "Restart management service" attempts a recovery via the
  USB-debug socket if available; otherwise instructs the user
  to power-cycle the Bridge.

**Acceptance criteria:**

- Logs tab streams in real time; pause/resume works.
- Support bundle downloads; opened bundle contains the redacted
  set per policy.
- Recovery state triggers when management service is down; copy
  matches plan 029.

**Tests:** SSE stream parsing; support-bundle redaction;
recovery-state transitions.

## Verification (every phase)

- `swiftc` builds clean (existing `bash scripts/build-app.sh`).
- `swift test` (or whatever the macOS test runner is) passes.
- `cargo fmt && cargo clippy --workspace -- -D warnings && cargo test --workspace`
  (no Rust changes expected; keep gates green).
- `/tmp/bridge-verify/bin/python -m pytest bridge/tests/ -q --timeout=10 --timeout-method=thread`
  (no bridge changes expected; keep gates green).
- Manual: plug Pi into Mac, walk the phase's user flow end-to-end.

## Out of scope

- Bonjour discovery on Same-Wi-Fi (Phase A uses direct probe
  only; Bonjour is a separate optimization).
- Cross-platform (Windows/Linux InstantLink companion app).
- v1.5 multi-printer pairing.
- Cloud sync of Bridge config (intentionally out per CLAUDE.md
  non-goals).

## Open questions (deferred)

- Whether to expose raw TOML editing for power users (per plan
  029 "advanced editor" mention) — likely deferred until after
  Phase B ships and we see if users hit walls.
- Whether the Mac app should bundle a CLI tool (`instantlinkctl`)
  for scripting — would extend the audit/automation surface but
  is its own design.
- Whether status-bar menu vs full window vs both — Phase A
  proposes both; we may collapse to one based on testing.

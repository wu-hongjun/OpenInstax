# Plan 029: InstantLink Bridge Control Panel

## Goal

Build a first-class control panel in the InstantLink macOS app for discovering, configuring, updating, backing up, and diagnosing InstantLink Bridge devices. The Bridge should feel like a product accessory, not an SSH target.

## Product Principles

- The Mac owns complex workflows: setup, updates, settings diff/review, backup/restore, logs, and recovery.
- The Bridge LCD owns local truth: ready state, printer state, upload credentials, short recovery prompts, update safety, and device identity.
- USB is a maintenance path. Do not present USB as camera upload. Use `USB debug` for setup, updates, diagnostics, and recovery.
- Bridge Wi-Fi is the primary upload workflow. Same-Wi-Fi is advanced. Peer/AP+STA remains experimental unless hardware testing proves it reliable.
- Avoid implementation words in normal UI: `SSH`, `BlueZ`, `NetworkManager`, `systemd`, `config.toml`, `FFI`, `deploy`, `g_ether`, `wlan0`, `usb0`.

## Non-Negotiables For Product UX

- Do not require users to type SSH usernames, SSH passwords, or shell commands.
- Do not reuse camera FTP credentials or the Bridge Wi-Fi PIN for Bridge administration.
- Do not expose update controls until Bridge update packages have a signed trust chain and a health-gated recovery path.
- Do not show or upload credentials in support bundles unless the user explicitly chooses an encrypted backup.
- Do not leave management pairing open indefinitely. Pairing must be a short physical-approval window from the LCD.
- Do not present raw system details as the primary diagnosis. Normal UI says `Management service unavailable`; advanced details can include the underlying API/SSH/systemd error.

## Existing Surfaces

### macOS

- `macos/InstantLink/App/InstantLinkApplication.swift` owns the status-bar menu and app scene.
- `macos/InstantLink/Features/Main/MainView.swift` owns the main Print UI and settings sheet presentation.
- `macos/InstantLink/Features/Settings/SettingsViews.swift` is a narrow app settings sheet, not large enough for Bridge management.
- `macos/InstantLink/Core/ViewModel.swift` is already large and should not absorb Bridge update/config/log state.
- `macos/InstantLink/InstantLinkFFI.swift` is printer-only. Bridge administration should not be added to the printer FFI.

### Bridge

- `instantlink-bridge --version` and `instantlink-bridge --status` exist, but status is human-readable and thin.
- Runtime config is `/etc/InstantLinkBridge/config.toml`.
- Service is `instantlink-bridge.service`, installed under `/opt/InstantLinkBridge`, running as `ib`.
- Wi-Fi helper is `/usr/local/sbin/instantlink-bridge-wifi-mode`.
- Deploy/update is currently engineering-oriented SSH via `bridge/scripts/deploy-to-pi.sh`.
- In-process status, printer selection, FTP runtime updates, and network health exist in Python, but are not exposed as a stable management API.

## Proposed App Architecture

Add a new macOS feature area:

```text
macos/InstantLink/Features/Bridge/
  BridgeControlWindow.swift
  BridgeOverviewView.swift
  BridgeSetupView.swift
  BridgeUploadFTPView.swift
  BridgePrinterView.swift
  BridgeUpdatesView.swift
  BridgeBackupView.swift
  BridgeDiagnosticsView.swift

macos/InstantLink/Core/
  BridgeControlCoordinator.swift
  BridgeTransport.swift
  BridgeModels.swift
```

`BridgeControlCoordinator` should be independent of the print `ViewModel`. It owns device discovery, connection state, update progress, remote config snapshots, support bundle progress, and Bridge-specific errors.

`BridgeTransport` should be a protocol so early SSH/script-backed management can be replaced by a product API without rewriting the UI:

```swift
protocol BridgeTransport {
    func discover() async throws -> [BridgeDevice]
    func status(device: BridgeDevice) async throws -> BridgeStatus
    func fetchConfig(device: BridgeDevice) async throws -> BridgeConfigSnapshot
    func validateConfig(_ config: BridgeConfigSnapshot, device: BridgeDevice) async throws
    func applyConfig(_ config: BridgeConfigSnapshot, device: BridgeDevice) async throws
    func update(device: BridgeDevice, package: BridgeUpdatePackage) async throws -> AsyncThrowingStream<BridgeUpdateEvent, Error>
    func createBackup(device: BridgeDevice, options: BridgeBackupOptions) async throws -> URL
    func restoreBackup(_ backup: URL, device: BridgeDevice, scope: BridgeRestoreScope) async throws
    func tailLogs(device: BridgeDevice) async throws -> AsyncThrowingStream<BridgeLogLine, Error>
    func createSupportBundle(device: BridgeDevice) async throws -> URL
}
```

Initial implementation may use `SSHBridgeTransport` with `/usr/bin/ssh`, `/usr/bin/scp`, and remote helper commands. Product implementation should move to a Bridge management API over USB debug, Bridge Wi-Fi, or same-LAN.

## Navigation

Make `Bridge` a first-class area in the macOS app, not a hidden section inside Settings.

- `Print`: existing direct-Mac InstantLink flow.
- `Bridge`: one or more Bridge devices, setup, update, backup, diagnostics.
- `Settings`: app-level language, appearance, direct-printer profiles, app update.

Add status-bar menu item:

- `Bridge Control...`

The existing Settings sheet can include a compact `InstantLink Bridge` section with a single `Open Bridge Control...` button.

## Bridge Control IA

Persistent header:

```text
InstantLink Bridge IB-XXXXXXXX
Ready to print | Bridge Wi-Fi | Square 5/10 | vX.Y.Z

[Refresh] [Update] [Support Bundle]
```

Tabs or sidebar sections:

- `Overview`: readiness, active upload mode, printer, film, versions, last upload, last error.
- `Setup`: first setup checklist and camera/sender FTP card.
- `Upload FTP`: Bridge Wi-Fi, Same-Wi-Fi advanced, FTP host/user/pass, sender setup export.
- `Printer`: pair to Bridge, status, model, keepalive, no-film testing, test print.
- `Updates`: update availability, preflight, install progress, rollback/retry state.
- `Backup`: create backup, restore backup, reset for gifting.
- `Diagnostics`: logs, health checks, support bundle, advanced connection details.

## User Flows

### Connect Bridge

1. User opens `Bridge`.
2. App scans USB debug `192.168.7.1`, Bridge Wi-Fi `LinkBrdg-XXXXXXXX`, saved same-LAN addresses, and future Bonjour advertisements.
3. If found, show device tile: `InstantLink Bridge IB-XXXXXXXX`.
4. User clicks `Connect`.
5. App checks service, version, config, FTP path, printer status, disk, and recent errors.
6. End state is `Ready to print`, `Setup needed`, or `Needs attention`.

### First Setup

1. User clicks `Set Up New Bridge`.
2. Preferred path: `Connect the Bridge to this Mac with USB`.
3. Copy: `Use the Pi port labeled USB, not PWR IN. Keep the Bridge powered.`
4. App pairs/trusts the Mac to the Bridge using a short LCD confirmation code.
5. User names the Bridge.
6. User chooses upload mode. Default: `Bridge Wi-Fi`.
7. Show sender FTP card:

```text
Wi-Fi: LinkBrdg-XXXXXXXX
Wi-Fi PIN: 12345678
FTP host: 192.168.8.1
FTP user: ib
FTP pass: 12345678
```

8. Pair printer to Bridge.
9. Optional test print.
10. Finish screen: `Ready to print`.

### Update Bridge

1. Overview shows `Update available`.
2. User clicks `Update Bridge`.
3. Preflight verifies power, disk, service health, current version, connectivity, and that no print is active.
4. App creates an automatic backup first.
5. Progress steps:
   - `Preparing update`
   - `Uploading update`
   - `Installing update`
   - `Restarting Bridge`
   - `Reconnecting`
   - `Verifying print service`
6. LCD shows `Updating`, `Do not power off`, then `Restarting`, then `Ready`.
7. Success: `Bridge updated to vX.Y.Z`.
8. Failure: show recovery and keep the backup available.

### Settings Upload

1. User edits settings in Mac UI.
2. Changed rows show `Pending`.
3. Primary action is `Apply to Bridge`.
4. App validates values before sending.
5. App uploads config atomically, applies side effects such as Wi-Fi mode, and restarts service when needed.
6. App verifies live state after apply.
7. Failure preserves local pending changes and shows one recommended recovery action.

### Backup And Restore

Backup should support:

- Settings and credentials.
- Selected printer metadata.
- Deployment/runtime manifests.
- Diagnostics summary.

Logs are opt-in. Photos/uploads are excluded by default.

Export format: `.instantlinkbridgebackup`, with a manifest, redaction metadata, schema version, and checksums.

Restore flow:

1. User opens backup.
2. App shows contents and target Bridge.
3. User chooses scope:
   - `Network and FTP`
   - `Printer and print settings`
   - `All settings`
4. App validates, uploads, fixes permissions, restarts service if needed, and verifies readiness.
5. Printer bond restore is best effort. If invalid, end with `Pair printer again`.

### Logs And Support Bundle

Diagnostics starts with a plain health summary, not a log wall.

`Create Support Bundle` collects:

- Redacted config.
- `instantlink-bridge --status --json` output.
- `systemctl show/status` for bridge services.
- Recent `journalctl -u instantlink-bridge.service`.
- Kernel USB/power excerpts.
- `ip -j`, `nmcli`, USB gadget state.
- Deployment/runtime/artifact manifests.
- Recent upload/print failure summaries.

Exclude credentials and uploaded photos by default.

Final actions:

- `Copy Summary`
- `Reveal in Finder`

### Printer Pairing

1. `Bridge > Printer > Pair Printer`.
2. Copy: `Pair an Instax printer to the Bridge`.
3. User turns printer on and closes phone Instax app.
4. Bridge scans; Mac lists found `INSTAX-*` printers.
5. User selects one.
6. Stages:
   - `Scanning`
   - `Connecting`
   - `Reading printer info`
   - `Saved`
7. Show model, film, battery, connection.
8. Optional `Print Test Photo`.
9. If film is 0, require explicit `Enable no-film test` before sending.

## Failure States

Each failure state should show:

- What happened.
- What still works.
- One recommended next action.
- `Advanced Details` collapsed.

States:

- `Ready to print`
- `Setup needed`
- `Bridge unreachable`
- `Bridge Wi-Fi off`
- `Same Wi-Fi adv unavailable`
- `USB debug unavailable`
- `Management service unavailable`
- `Bridge access not authorized`
- `Printer not selected`
- `Looking for printer`
- `Printer seen; connecting`
- `No printer signal`
- `No film left`
- `Printer battery low`
- `Storage full`
- `Update failed`
- `Restore failed`
- `Support bundle failed`

Recovery examples:

- Hotspot missing: `Bridge Wi-Fi not found. Check the Bridge is powered, switch FTP mode to Bridge Wi-Fi on the LCD, or connect with USB debug.`
- USB missing: `USB debug link not found. Use the Pi USB port, not PWR IN, use a data cable, and keep the Bridge separately powered.`
- Management unavailable: `Management service unavailable. Uploads may still work. Restart the Bridge service or reconnect with USB debug.`
- Auth failed: `Bridge access not authorized. Confirm the code on the Bridge LCD or reset Bridge access from the LCD.`

## Wording

Use:

- `InstantLink Bridge`
- `IB-XXXXXXXX`
- `LinkBrdg-XXXXXXXX`
- `Bridge Wi-Fi`
- `Same Wi-Fi adv`
- `USB debug`
- `Wi-Fi PIN`
- `FTP host`
- `FTP user`
- `FTP pass`
- `Pair printer to Bridge`
- `Apply to Bridge`
- `Update Bridge`
- `Create Support Bundle`

Required copy:

```text
USB debug is for setup, updates, and diagnostics. Use Bridge Wi-Fi for camera uploads.
```

Use `sender` or `upload device` when the source can be a camera, computer, or phone. Use `camera` only in camera-specific instructions.

## Required Bridge Management API

Do not build the product UI on human-readable shell output. Add machine-readable commands or an authenticated local API first.

Minimum CLI/API:

```text
instantlink-bridge status --json
instantlink-bridge config get --json
instantlink-bridge config validate --file <path> --json
instantlink-bridge config apply --file <path> --json
instantlink-bridge wifi status --json
instantlink-bridge wifi scan --json
instantlink-bridge wifi hotspot --json
instantlink-bridge wifi join --stdin-json
instantlink-bridge printer scan --json
instantlink-bridge printer select --stdin-json
instantlink-bridge printer forget --json
instantlink-bridge printer status --json
instantlink-bridge printer test-print --json
instantlink-bridge logs export --json
instantlink-bridge support-bundle create --json
instantlink-bridge backup create --json
instantlink-bridge backup restore --file <path> --scope <scope> --json
instantlink-bridge update preflight --json
instantlink-bridge update install --package <path> --json
instantlink-bridge update status --json
instantlink-bridge service restart --json
```

Use JSON schemas with `schema_version`, stable error codes, redacted fields, and a `recommended_action` string.

## Authentication

The product should not require typing SSH passwords.

Recommended product path:

1. Bridge advertises a local management service over USB debug, Bridge Wi-Fi, and same-LAN.
2. First connection requires LCD confirmation with a short code.
3. Mac stores a per-Bridge management token in Keychain.
4. Bridge stores authorized clients under `/var/lib/InstantLinkBridge/management/`.
5. User can revoke/reset access from LCD System settings.

Initial developer bridge can use SSH, but UI copy should say `Bridge access`, not `SSH`.

The admin credential is separate from:

- Bridge Wi-Fi WPA PIN.
- FTP username/password.
- Printer Bluetooth bond.
- Mac direct-printer profiles.

## Update Strategy

v1 control panel can reuse the deploy artifact model, but must wrap it as product update:

- Signed update package containing bridge Python source/wheelhouse, arm64 InstantLink FFI/CLI artifacts, manifests, and scripts.
- Preflight checks before install.
- Automatic backup before install.
- Atomic unpack to a staging directory.
- Verify checksums and signature before switching.
- Restart service and verify `status --json`.
- Mark-good only after the service, FTP listener, LCD/UI, network mode, and printer-status loop pass health checks.
- Preserve a rollback pointer to the previous working install.

Future v2:

- A/B image update using Raspberry Pi image tooling.
- Rollback on watchdog failure or failed post-update health check.

## Security And Redaction Requirements

- Store Mac-side Bridge management secrets in Keychain.
- Store Pi-side trusted management clients under `/var/lib/InstantLinkBridge/management/` with root-owned permissions.
- Prefer client keypair/certificate auth over bearer tokens once the local management API exists.
- Redact FTP pass, Wi-Fi PIN, MAC addresses if requested, image filenames, home SSIDs, and remote IPs from support bundles by default.
- Keep uploaded images out of support bundles unless the user explicitly attaches one.
- Replace broad sudo/script access with narrow root-owned helpers before hardening the systemd service with `NoNewPrivileges`, capability bounding, and filesystem restrictions.
- Use stable JSON error codes so the macOS recovery assistant does not parse logs or shell prose.

## Backup Contents

Default backup:

- `/etc/InstantLinkBridge/config.toml`
- Hotspot SSID and PIN metadata.
- Selected printer metadata from `/var/lib/InstantLinkBridge/printer.json`
- Deployment/runtime/artifact manifests.
- Bridge device ID and schema version.

Optional encrypted backup:

- FTP password.
- Bridge Wi-Fi PIN.
- Management client trust records.

Never include by default:

- Uploaded photos.
- Full unredacted journals.
- SSH credentials.

## Implementation Plan

### Phase 1: Bridge management contract

- Add JSON status/config/printer/wifi/log commands to Bridge.
- Add support bundle and backup commands.
- Add tests for command JSON and redaction.
- Keep SSH as transport, but only call stable commands.

### Phase 2: macOS control panel shell

- Add `Features/Bridge`.
- Add `BridgeControlCoordinator`, `BridgeTransport`, and models.
- Add status-bar `Bridge Control...`.
- Implement discovery and overview using `status --json`.

### Phase 3: setup and settings

- Implement setup wizard.
- Implement typed settings editor and `Apply to Bridge`.
- Implement Upload FTP card with copy/export actions.

### Phase 4: updates and backup

- Build signed bridge update package. The unsigned package/bundling substrate now exists in
  `bridge/scripts/build-firmware-bundle.sh`, `.github/workflows/bridge-firmware.yml`, and the app
  resource staging path documented in `docs/development/bridge-firmware-release.md`.
- Implement preflight/install/reconnect/verify.
- Implement backup/restore.

### Phase 5: diagnostics and recovery

- Implement live logs and support bundle.
- Add recovery assistant states.
- Add LCD reset/access instructions.

## Open Questions

- Whether the management API should be HTTPS over Unix socket proxy, local HTTP with token auth, or CLI-only for v1.
- Whether the Bridge should advertise with Bonjour in hotspot mode, same-LAN mode, or both.
- Whether update packages should be hosted as InstantLink release assets or generated inside the macOS app bundle.
- Whether reset-for-gifting should preserve the selected printer bond or force a full erase.

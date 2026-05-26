# Plan 030: Bridge Secure Management And Updates

## Goal

Define the minimum product-grade foundation required before the InstantLink macOS app can update
Bridge firmware: a local management API, physical local authorization, signed firmware trust chain,
automatic backup, and health-gated rollback.

## Product Rule

Do not expose one-click Bridge updates in the macOS app until every section in this plan has an
implemented test path. The existing firmware bundle from Plan 029 is only a packaging substrate; it
is not yet an update product.

## Scope

In scope:

- Bridge discovery, status, config, backup, diagnostics, and update commands over a stable API.
- Local authorization with physical approval on the Bridge LCD.
- Signed firmware bundle verification on both Mac and Bridge.
- Pre-update backup and post-update rollback.
- App UI that can explain update state without SSH or shell terminology.

Out of scope for this plan:

- Full Raspberry Pi OS A/B image updates.
- Cloud accounts, remote access, or internet-mediated device management.
- Automatic updates while printing or receiving an upload.
- Reusing FTP, Bridge Wi-Fi, or SSH credentials for administration.

## Architecture

Split runtime and management privileges:

```text
macOS InstantLink app
  -> BridgeTransport
  -> HTTPS JSON management API
  -> instantlink-bridge-manager.service
      -> signed-package verifier
      -> backup/restore helper
      -> release-slot updater
      -> instantlink-bridge.service control

instantlink-bridge.service
  -> FTP receive, LCD UI, BLE printer session, image pipeline
```

`instantlink-bridge.service` keeps running as `ib` and owns the user-facing appliance runtime.
`instantlink-bridge-manager.service` owns admin operations. It should be small, heavily tested, and
separate from the print loop so update/recovery work can survive service restarts.

Preferred implementation:

- Python `aiohttp` management server using JSON only.
- End-state transport is HTTPS with a self-signed Bridge certificate pinned during pairing.
- A bootstrap phase may use signed local HTTP requests only if no admin endpoint accepts unsigned
  requests and unauthenticated responses contain no secrets.
- Bind by default on USB debug `192.168.7.1` and Bridge Wi-Fi `192.168.8.1`.
- Same-Wi-Fi management disabled by default; allow only after explicit LCD or app setting.
- No raw shell output in app-facing responses.
- All responses include `schema_version`, `request_id`, `ok`, and stable `error_code`.
- Long operations expose an operation id and progress stream rather than blocking HTTP requests.

## Management API

Unauthenticated endpoints are discovery-only:

```text
GET  /v1/hello
GET  /v1/pairing/status
```

`/v1/hello` may return:

- Device id, display name, software version, API version.
- Management public-key fingerprint.
- Whether pairing is open.
- Safe network labels such as `Bridge Wi-Fi`, `USB debug`, and `Same-Wi-Fi`.
- No FTP password, Wi-Fi PIN, logs, config, printer MAC, or update controls.

Authenticated endpoints:

```text
GET  /v1/status
GET  /v1/config
PUT  /v1/config
POST /v1/config/validate
GET  /v1/network/status
POST /v1/network/mode
GET  /v1/printer/status
POST /v1/printer/scan
POST /v1/printer/select
POST /v1/printer/forget
POST /v1/backup/create
POST /v1/backup/restore
POST /v1/support-bundle/create
POST /v1/update/preflight
POST /v1/update/upload
POST /v1/update/install
GET  /v1/update/status
POST /v1/update/mark-good
POST /v1/update/rollback
GET  /v1/events
```

CLI parity is required for testability:

```text
instantlink-bridge-manager status --json
instantlink-bridge-manager update preflight --package <path> --json
instantlink-bridge-manager update install --package <path> --json
instantlink-bridge-manager backup create --json
instantlink-bridge-manager rollback --json
```

The macOS app should call the API through `BridgeTransport`; tests should use both the direct Python
manager and the Swift transport abstraction.

## Local Authorization

Use a physical approval ceremony. Management access is separate from FTP credentials, Bridge Wi-Fi
PIN, printer bond, and SSH.

Pairing flow:

1. User opens `Bridge access` on the Bridge LCD or clicks `Pair this Mac` in the app.
2. Bridge shows a short confirmation code and opens a 90 second pairing window.
3. Mac generates a per-Bridge client keypair and stores it in Keychain.
4. Mac sends its client public key, device fingerprint expectation, and confirmation code.
5. Bridge stores the authorized client record under
   `/var/lib/InstantLinkBridge/management/clients/<client-id>.json`.
6. Subsequent requests are signed and include timestamp, nonce, request method, path, and body hash.

Authorization rules:

- Reject unsigned admin requests.
- Reject stale timestamps and replayed nonces.
- Rate-limit failed pairing and failed signatures.
- Allow LCD revocation of one Mac or all Macs.
- Show `Bridge access open` only during the short pairing window.
- Store client records `root:root 0600`.
- Store Mac private keys only in Keychain.

Implementation dependency: add a small crypto dependency on the Bridge side, preferably
`cryptography`, so Ed25519 request signatures and firmware signatures use the same primitive.

## Signed Package Trust Chain

The current bundle has checksums but no trust root. Add release signing before app-driven install.

Firmware release assets:

```text
InstantLinkBridgeFirmware-vX.Y.Z-linux-aarch64.tar.gz
InstantLinkBridgeFirmware-vX.Y.Z-linux-aarch64.tar.gz.sha256
InstantLinkBridgeFirmware-vX.Y.Z-linux-aarch64.manifest.json
InstantLinkBridgeFirmware-vX.Y.Z-linux-aarch64.manifest.sig
latest.json
latest.json.sig
```

Signing rules:

- Sign canonical JSON manifests, not arbitrary shell scripts.
- Manifest includes schema version, package kind, version, target, commit, tag, artifact names,
  SHA-256 digests, required Bridge API version, migration notes, and minimum rollback version.
- The private signing key lives outside the repo and is injected into release CI through a protected
  secret.
- The public verification key is embedded in the already-installed Bridge verifier and the macOS app.
- The app verifies before upload; the Bridge verifies again before install.
- The Bridge refuses unsigned packages, mismatched target architecture, unknown key id, invalid
  signature, digest mismatch, dirty release metadata, and app/Bridge API incompatibility.
- Downgrades require explicit recovery mode unless the target is the recorded previous good release.

Key rotation:

- Support `key_id` and multiple trusted public keys in the verifier.
- New releases may add a future public key before the old key is retired.
- Removing a key requires a signed release from a still-trusted key.

## Automatic Backup

Every update creates a local backup before touching the current release slot.

Backup location:

```text
/var/lib/InstantLinkBridge/backups/update-YYYYMMDD-HHMMSS-<version>.tar.gz
/var/lib/InstantLinkBridge/backups/update-YYYYMMDD-HHMMSS-<version>.manifest.json
```

Default backup contents:

- `/etc/InstantLinkBridge/config.toml`
- Hotspot and FTP credential metadata required to restore service behavior.
- Selected printer metadata.
- Authorized management clients.
- Deployment, runtime dependency, firmware bundle, and native artifact manifests.
- NetworkManager profiles owned by the Bridge.
- Systemd unit files and udev rules installed by the Bridge.
- BlueZ bond metadata for the selected printer only, if readable and safe to restore on the same
  adapter.

Excluded by default:

- Uploaded images.
- Full journals.
- SSH credentials.
- Home Wi-Fi passwords in support bundles. Local rollback backup may retain them with root-only
  permissions because it never leaves the Bridge unless the user exports an encrypted backup.

Backup checks:

- Verify the backup manifest and file hashes before starting install.
- Keep at least the latest three update backups.
- Refuse update if backup creation fails.
- App-exported backups must be encrypted to the Mac client key or explicitly redacted.

## Release Slots And Rollback

Move from in-place replacement to release slots:

```text
/opt/InstantLinkBridge/
  current -> releases/2026-05-26T153000Z-v0.2.0
  previous -> releases/2026-05-20T101500Z-v0.1.0
  releases/
    <release-id>/
      bridge/
      native/
      .venv/
      manifest.json
  shared/
    wheelhouse/
    uploads/
```

Install flow:

1. Preflight validates package signature, disk, power, no active print, no active upload, API
   compatibility, and backup ability.
2. Create automatic backup.
3. Extract package into a new release directory.
4. Build or update the release-local virtualenv from a bundled wheelhouse.
5. Verify native artifacts and Python entrypoints.
6. Stop `instantlink-bridge.service`.
7. Atomically switch `current` and `previous` symlinks.
8. Run `systemctl daemon-reload` and restart services.
9. Enter `pending_verification`.
10. Run health checks.
11. Mark good or rollback.

Rollback flow:

1. Stop Bridge runtime service.
2. Switch `current` back to `previous`.
3. Restore config from the pre-update backup only if the failed update migrated config.
4. Restart services.
5. Run reduced health checks.
6. Record rollback reason in `/var/lib/InstantLinkBridge/update-state.json`.
7. Surface `Update failed; restored previous version` in app and LCD.

## Health Gates

An update is not good merely because files copied successfully.

Required gates:

- `instantlink-bridge-manager.service` responds to `/v1/status`.
- `instantlink-bridge.service` is active for at least 30 seconds without restart.
- `instantlink-bridge --version` reports the installed firmware version.
- Config parses and all configured paths are writable by `ib`.
- LCD render loop heartbeat is fresh, or display is explicitly disabled.
- FTP listener is bound on the active upload mode address and port.
- Network status matches the selected mode: Bridge Wi-Fi, Same-Wi-Fi, or USB debug.
- Printer status loop is alive. Printer offline is not a failed update, but the status must be
  truthful and non-blocking.
- Disk free space remains above the configured floor.
- Journal contains no critical startup exception after the new service start.

Mark-good policy:

- The manager marks good automatically only after all gates pass.
- The app may show progress, but it does not override gates.
- If gates fail within the verification window, rollback automatically.
- If the Bridge loses power during `pending_verification`, boot should prefer the previous good
  release unless the new release was already marked good.

## macOS Control Panel UX

The app should show updates as a guided operation:

```text
Checking Bridge
Backing up settings
Verifying update
Uploading update
Installing update
Restarting Bridge
Verifying Bridge
Done
```

Failure copy should name the safe state:

- `Update was not installed`
- `Update failed; restored previous version`
- `Bridge needs recovery`

Do not show:

- Raw SSH commands.
- Package filenames as primary copy.
- `systemctl`, `BlueZ`, `NetworkManager`, or interface names outside advanced details.

LCD behavior:

- Show `Updating`, `Do not power off`.
- Show current step in one short line.
- Disable print confirmation while installing.
- After rollback, show `Update restored` and `Open app for details`.

## Implementation Phases

Current implementation status (May 2026): Phase 1 scaffolding is present for the manager CLI/API,
route catalog, signed-request verification primitives, read-only discovery/status, Swift models, and
an in-memory `BridgeTransport`. Phase 3 scaffolding is present for Ed25519 manifest signing and
signed bundle sidecars. Phase 4/5 planning helpers exist for backup manifests and release-slot
switch plans. Product update controls must remain hidden until physical pairing, real signed HTTP
transport, trusted key embedding, backup archive/restore, privileged release-slot install, durable
boot recovery, and health gates are implemented end to end.

### Phase 1: Management contract

- Add `instantlink-bridge-manager` package entrypoint.
- Add JSON models, schema tests, and read-only status/config/network/printer endpoints.
- Add Swift `BridgeTransport` models matching the JSON contract.
- Acceptance: app can discover and display status without SSH.

### Phase 2: Local authorization

- Add device identity, client records, pairing window, LCD confirmation, and signed requests.
- Add Keychain-backed Mac client key storage.
- Acceptance: unpaired Mac can only call discovery endpoints; paired Mac can call status/config.

### Phase 3: Signed firmware packages

- Add manifest signing script, public verification key, CI signing step, and verifier tests.
- Add `.sig` release assets and app-side verification.
- Acceptance: tampered package, tampered manifest, wrong target, and unsigned package all fail.

### Phase 4: Backup

- Add local backup creation, manifest/checksum verification, retention, and encrypted app export.
- Acceptance: update preflight refuses to continue when backup cannot be created or verified.

### Phase 5: Release-slot installer

- Add release directory layout, virtualenv/wheelhouse install, atomic symlink switch, update state,
  and rollback helper.
- Acceptance: a deliberately broken test release rolls back to the previous working release.

### Phase 6: App update flow

- Add Bridge Updates UI backed by `BridgeTransport`.
- Hide update action unless auth, signature verification, preflight, backup, and rollback are
  available.
- Acceptance: the app can install a signed local bundled package and report success or rollback.

### Phase 7: Hardening

- Add request audit log with redaction.
- Add systemd sandboxing and narrow sudo/root helper boundaries.
- Add fuzz/property tests for manifest parsing and path traversal rejection.
- Add CI job that builds, signs with a test key, verifies, extracts, and runs installer dry-run.

## Open Decisions

- Whether Phase 1 should ship HTTPS with a self-signed, pinned device certificate immediately, or
  use signed local HTTP requests until certificate lifecycle is implemented.
- Whether same-Wi-Fi management should be hidden entirely in v1 or allowed as an advanced opt-in.
- Whether update packages should bundle an arm64 wheelhouse for every Python dependency in v1, or
  keep using existing Pi virtualenvs until the first production run.
- Whether BlueZ bond backup should be included by default or treated as best-effort recovery data.
- Whether signed packages should use a custom Ed25519 manifest signature or an external tool such as
  minisign/cosign wrapped by CI.

## Exit Criteria

- No update control is visible for an unpaired Mac.
- A paired Mac can update without SSH, shell commands, or manual file copying.
- A network attacker cannot install a modified package.
- A failed install automatically returns to the previous working Bridge release.
- The user sees clear app and LCD status for backup, install, verification, rollback, and recovery.

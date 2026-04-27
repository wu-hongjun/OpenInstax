# Plan 018: Auto-Update Signature Verification

## Goal

Stop installing update payloads that have not been cryptographically verified. Today the macOS app downloads a DMG over HTTPS, mounts it, copies the inner `.app` over the running bundle, and relaunches — with no signature, checksum, or notarization gate. A DNS hijack, BGP hijack, compromised CDN cache, or MITM on a hostile network gets full code execution as the current user.

## Current State

`macos/InstantLink/Core/AppRuntimeServices.swift` (≈108-275)

- `AppUpdateService.checkForUpdates()` fetches release metadata from the GitHub API (~line 112).
- `installUpdate()` downloads `browser_download_url` (~line 168) into a temp directory.
- The DMG is `hdiutil attach`-ed; the first `.app` discovered is `cp -R`-copied over the running bundle (~lines 262-264).
- The replacement bundle is then relaunched.

Builds are **ad-hoc signed** (`scripts/build-app.sh:136`, `codesign --force --deep -s -`), so Gatekeeper assigns no provenance and offers no protection at launch time.

## Threat Model

- Network attacker with TLS interception or DNS control.
- Compromised release pipeline writing a malicious DMG to the same `browser_download_url`.
- Stale CDN cache serving an older, vulnerable build after a security release.

In all three cases the current code installs and runs the attacker's payload without warning.

## Proposed Solution

### Short-term (must ship before the next release)

1. Publish a `SHA-256` checksum for every release artifact in the GitHub release body (or as a sibling `.sha256` asset).
2. The updater downloads both the DMG and its checksum, verifies the digest, and aborts if the comparison fails.
3. Pin the GitHub API host's certificate (or at minimum require TLS 1.3 + certificate transparency) so a rogue CA cannot rewrite either fetch.

### Medium-term

1. Sign distribution builds with a Developer ID Application certificate and notarize the DMG.
2. After download, run `SecStaticCodeCheckValidity` against a designated requirement string before replacing the running bundle.
3. Reject any payload whose signing identity differs from the currently-running bundle's signing identity.

### Long-term

- Move to Sparkle 2 or a similar update framework so we get EdDSA signatures, delta updates, and a vetted install dance for free, instead of maintaining bespoke `hdiutil`/`cp -R` logic.

## Implementation Scope

Primary files:

- `macos/InstantLink/Core/AppRuntimeServices.swift`
- `scripts/build-app.sh` (signing identity, checksum emission)
- `.github/workflows/release.yml` (publish checksums + notarize)

Supporting:

- `macos/InstantLink/Resources/*.lproj/Localizable.strings` for new error messages
- `docs/development/release.md` documenting the new signing prerequisites

## Testing

- Unit-test the checksum verifier with a known-good and a tampered payload.
- Manual: attempt to install a DMG whose checksum does not match — install must abort and surface a clear error.
- Manual: attempt to install a payload signed with a different identity — install must abort.
- Manual: confirm a clean update path on a notarized release still succeeds end-to-end.

## Rollout Order

1. Add checksum publication to the release workflow.
2. Add checksum verification to `AppUpdateService.installUpdate`.
3. Switch the build to Developer ID + notarization.
4. Add `SecStaticCodeCheckValidity` gate.
5. Document the new release prerequisites.

## Implementation Status

### Completed (short-term)

- `.github/workflows/release.yml`: added a "Generate SHA-256 checksums" step that runs `shasum -a 256` for the DMG, CLI zip, and FFI zip, and uploads the resulting `.sha256` sibling files alongside the release artifacts.
- `macos/InstantLink/Core/AppRuntimeServices.swift`: `AppUpdateInfo` now carries `checksumURL`; `checkForUpdates` resolves the matching `.sha256` asset from the GitHub release; `installUpdate` is refactored into two stages — download then `verifyAndInstall` — which fetches the `.sha256` file, computes the SHA-256 digest of the local DMG via CryptoKit, and aborts with a localized error if the digest does not match. Any missing checksum also aborts (fail-closed).
- `macos/InstantLink/Core/ViewModel.swift`: stores `updateChecksumURL` and threads it through to `installUpdate`.
- All 12 `Localizable.strings` files: added `update_error_invalid_url`, `update_error_download_failed`, `update_error_checksum_missing`, `update_error_checksum_mismatch`.

### Blocked — follow-up required

- **Developer ID notarization**: requires an Apple Developer ID Application certificate and a notarytool API key in CI secrets. Blocked on infrastructure not available in this environment. Tracked as medium-term item.
- **`SecStaticCodeCheckValidity`**: requires notarized builds with a known designated requirement string. Blocked on the notarization step above. Tracked as medium-term item.
- **Sparkle 2 migration**: requires integrating the Sparkle framework (EdDSA signatures, delta updates, vetted install dance). Tracked as long-term item.
- **TLS certificate pinning**: pinning the GitHub API host's certificate or requiring certificate transparency was deferred; HTTPS + checksum verification is the current defense.

## Exit Criteria

- A tampered DMG cannot be installed even if the network is fully compromised.
- Releases ship with a published, verifiable digest.
- Distribution builds carry Apple-issued signatures, and the app refuses to install anything signed by a different identity.

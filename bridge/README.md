# InstantLink Bridge

InstantLink Bridge is the Raspberry Pi appliance runtime for camera FTP receive plus InstantLink
printing. It receives selected JPEG/HIF/RAW-capable stills over the bridge hotspot FTP path,
prepares them for the detected printer format, and prints through the parent repository's
`instantlink-ffi` backend with a small LCD/joystick interface.

This folder contains the Pi service code, tests, setup docs, system configuration, and deployment
helpers for the current vertical slice.

## Read First

- [CLAUDE.md](CLAUDE.md) is the persistent context anchor for future Claude Code sessions.
- [docs/current-context.md](docs/current-context.md) is the latest handoff for deployment,
  hardware status, and verified operating assumptions.
- [ARCHITECTURE.md](ARCHITECTURE.md) describes data flow, state, and asyncio task topology.
- [HARDWARE.md](HARDWARE.md) records BOM, pinout, wiring, assembly, and enclosure notes.
- [DECISIONS.md](DECISIONS.md) contains ADRs for the major architectural choices.
- [ROADMAP.md](ROADMAP.md) stages v1, v1.5, and v2 work.

## Repo Layout

```text
.
|-- docs/                 # Setup notes and operational reference
|-- src/instantlink_bridge/    # Python service code
|-- systemd/              # Service units
|-- udev/                 # Cable/event rules
|-- config/               # Config templates and Pi system snippets
|-- scripts/              # Provisioning/deploy helpers
`-- tests/                # Unit tests for the bridge runtime
```

## Current Status

- Last verified on hardware: 2026-05-25, deployed commit
  `c1f016a04234afb5a32104e3a11b2b76f7895772`.
- The Pi service starts the LCD UI, Bridge Wi-Fi FTP, advanced Same Wi-Fi FTP,
  printer discovery/status keepalive, and the FTP-received-image auto-print flow.
- The LCD Settings menu can pair/forget a printer, explicitly choose Wi-Fi mode options, and persist
  printer, image-prep, keepalive, and auto-print settings.
- The runtime supports model-specific Mini, Mini Link 3, Square, and Wide image preparation.
- Provisioning and deployment helpers are tracked in `scripts/`, `config/`, `systemd/`, and `udev/`.
- Legacy standalone InstantBridge installs are removed with
  `scripts/cleanup-legacy-instantbridge.sh /` after the InstantLink Bridge service is healthy.

## Hotspot-Only Pi Deployment

When a Pi is already in Bridge Wi-Fi mode it may have no outbound internet. Use the offline deps path
with a known-good seed virtualenv from an earlier install:

```bash
INSTANTLINK_BRIDGE_HOST=192.168.7.1 \
INSTANTLINK_BRIDGE_USER=hongjunwu \
INSTANTLINK_BRIDGE_OFFLINE_DEPS=1 \
scripts/deploy-to-pi.sh --system --instantlink-artifacts --deps --restart
```

Only set `INSTANTLINK_BRIDGE_SEED_VENV=/opt/InstantBridge/.venv` during one-time migration from an
old device that has no outbound internet and no `/opt/InstantLinkBridge/.venv` yet.

This still records deployment metadata, installed Python packages, apt package state, and native
InstantLink artifact hashes under `/opt/InstantLinkBridge/.deployment/`.

## Target Device

Raspberry Pi Zero 2 W running Raspberry Pi OS Lite 64-bit Trixie, advertising a bridge hotspot at
`192.168.8.1`, optionally joining an existing Wi-Fi network for advanced Same Wi-Fi FTP, receiving
camera uploads, and printing to a bonded Mini, Mini Link 3, Square, or Wide Link printer over BLE.
The USB gadget network at `192.168.7.1` remains available for admin, SSH, firmware update, and
diagnostics only; direct camera USB-LAN is unsupported for v1 after the Mac-proven cable/camera
retest.

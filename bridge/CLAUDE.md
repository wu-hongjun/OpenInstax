# Claude Code Context: InstantLink Bridge

## Mission

InstantLink Bridge is a giftable, portable bridge between a Sony a7C II and Fujifilm Instax Link printers. The core user gesture is selecting an image in camera playback and pressing C1, mapped to `FTP Trans. (This Img.)`; the bridge receives a camera still over FTP, decodes JPEG/HIF and best-effort Sony RAW, prepares a model-specific Instax-ready JPEG, and prints it over BLE with minimal setup and no SSH required for normal use.

Read [docs/current-context.md](docs/current-context.md) first for the latest hardware deployment
state, verified commit, cleanup status, and operational assumptions.

## Hardware Summary

- Compute: Raspberry Pi Zero 2 W, BCM2710A1, quad Cortex-A53 at 1 GHz, 512 MB RAM, BLE 4.2, Wi-Fi 4.
- Display/input: Waveshare 1.3" LCD HAT, ST7789VW, 240x240 SPI, joystick plus KEY1/KEY2/KEY3.
- Battery: SupTronics/Geekworm X306 18650 UPS shield. It provides charging, UPS
  power-path switching, battery LEDs, and a hardware power button, but no
  host-readable fuel gauge. PiSugar remains an optional legacy backend only.
- USB admin link: USB-C to micro-USB data/OTG cable into the Pi `USB` port for admin, SSH, and diagnostics. The Pi is independently powered, but the cable must preserve data, ground, and enough host attach/VBUS signaling for the Pi gadget controller to enumerate with a normal host. This is not a supported v1 camera wired mode.
- Printer: Fujifilm Instax Mini, Mini Link 3, Square, or Wide Link, BLE 4.2 class, `INSTAX-XXXXXXXX` name pattern, model-specific JPEG input.
- Camera: Sony a7C II / ILCE-7CM2 on firmware 2.00 or later.

See [HARDWARE.md](HARDWARE.md) for BOM, pinout, wiring, assembly, and enclosure notes.

## Architecture Summary

```text
Sony a7C II playback
  C1: FTP Trans. (This Img.)
        |
        v
Bridge hotspot Wi-Fi, or optional same-network peer Wi-Fi
        |
        v
Raspberry Pi Zero 2 W
  Bridge Wi-Fi FTP 192.168.8.1
  optional Same Wi-Fi adv same-network address
  usb0 g_ether 192.168.7.1 for admin/SSH/diagnostics only
        |
        v
  pyftpdlib on 0.0.0.0:21
        |
        v
  asyncio.Queue[ImageJob]
        |
        v
  Pillow + pillow-heif/rawpy pipeline: EXIF transpose -> model-aware auto fit -> Mini/Square/Wide JPEG
        |
        v
  InstantLink Rust FFI / btleplug / BlueZ 5.79
        |
        v
Fujifilm Instax Link BLE printer
```

## Tech Stack

- Python 3.11+; Raspberry Pi OS Lite 64-bit Trixie currently ships Python 3.13, so the project supports `>=3.11,<3.14`.
- `asyncio` service with systemd `Type=notify` and `WatchdogSec=30`.
- Bleak 1.1.1 as a diagnostic fallback.
- pyftpdlib 2.2+.
- Pillow 11.x, pillow-heif 1.3+, rawpy 0.27+.
- luma.lcd 2.13+.
- gpiozero 2.x with libgpiod backend.
- BlueZ 5.79.
- dnsmasq.
- NetworkManager.
- Raspberry Pi OS Lite 64-bit Trixie / Debian 13.

## Coding Conventions

- Use `ruff`, `ruff format`, and strict `mypy`.
- Use snake_case for modules, functions, variables, and event names.
- Type hints are required for every function and method.
- Prefer `pathlib.Path` over `os.path`.
- Prefer `asyncio.Queue`, `asyncio.Event`, and typed structured events over threading primitives.
- Keep IO boundaries explicit: camera FTP, image processing, BLE, UI, power, network, and watchdog should remain separable.
- Treat the parent InstantLink workspace as the authoritative printer implementation. The default v1
  runtime backend is its Rust FFI (`libinstantlink_ffi.so`); the Python/Bleak path is retained only
  as a diagnostic fallback via `INSTANTLINK_BRIDGE_PRINTER_BACKEND=bleak`.
- The service must register the BlueZ `NoInputNoOutput` agent before the first printer status
  refresh. Instax Link printers issue an SMP security request during GATT connection; without the
  agent BlueZ replies `Pairing Failed: Pairing not supported`, the printer disconnects, and
  InstantLink cannot see the write/notify characteristics.

## Planned Module Layout

```text
src/instantlink_bridge/
  app.py            # orchestrator + state machine entry
  ble/
    client.py       # Bleak wrapper
    instax.py       # Instax Link protocol (Python port guided by InstantLink core)
    agent.py        # BlueZ NoInputNoOutput pairing agent for headless bonding
    instantlink.py  # default Rust FFI backend wrapper around parent InstantLink crates
  camera/
    ftp.py          # pyftpdlib server, on_file_received handler
    gphoto.py       # fallback PTP path (deferred)
  imaging/
    pipeline.py     # prepare_for_instax()
  ui/
    display.py      # luma.lcd ST7789 init + render loop
    widgets.py      # status bar, preview, queue badge, error modals
    input.py        # gpiozero joystick + buttons
  power/
    x306.py         # X306 no-telemetry UPS backend
    pisugar.py      # optional legacy /tmp/pisugar-server.sock client
  net/
    gadget.py       # usb0 admin/diagnostics verification
    wizard.py       # captive portal app
  watchdog.py       # sd_notify heartbeat
  config.py         # /etc/InstantLinkBridge/config.toml schema
```

## State Machine

The v1 state machine has exactly 10 user-visible states:

1. `BOOTING`
2. `BT_SCANNING`
3. `BT_CONNECTING`
4. `BT_CONNECTED`
5. `IDLE`
6. `IMAGE_RECEIVED`
7. `AWAITING_CONFIRM`
8. `PRINTING`
9. `PRINT_COMPLETE`
10. `ERROR_BLE`

`LOW_BATTERY` is a global overlay/guard condition rather than a primary state: warning at 20%, safe shutdown at 10%.
USB gadget attach/loss is diagnostics/admin status only and must not become a camera readiness
state in v1.

## Default UX Policies

- `workflow.auto_print_delay_s` supports only `0`, `5`, or `"off"`: `0s` prints immediately,
  `5s` shows an editable timed preview, and `off` waits on the editable preview until K1/joystick
  press.
- The preview screen must show the photo, active edit tool, and cancel/print controls. KEY3 cycles
  `Zoom`, `Crop`, and `Rotate`; KEY2 cancels.
- The long print path must show stage/progress (`Connecting`, `Preparing image`, `Sending N%`,
  `Finishing`) rather than a generic spinner.
- `printer.fit` defaults to `auto`: Square center-crops, Mini rotates landscape sources, and
  Wide rotates portrait sources before center-crop. Explicit `crop` never rotates.
- KEY1 / joystick press opens Settings from normal status screens. Settings persists
  `/etc/InstantLinkBridge/config.toml` and covers printer pairing/forget, Wi-Fi mode, printer type,
  fit, JPEG quality, auto-print mode/delay, and keepalive.
- `workflow.allow_print_without_film` is a testing-only escape hatch exposed as `No-film test`.
  Default is `false`; when enabled, do not block preview/print transfer at `0/10` film.
- KEY2 cancels during `AWAITING_CONFIRM`.
- KEY2 cancels an active printer scan.
- Printer setup starts scanning immediately from `Find printer`, no-printer setup, or KEY3 on
  status screens. Do not add a second confirmation screen for printer selection.
- If a selected printer is visible but repeatedly disconnects during GATT/service discovery, show
  `Restart printer` or equivalent recovery copy instead of `re-pair`; re-pair is only for absent or
  stale selected printers.
- The LCD and Settings must treat camera FTP as hotspot-first for v1. Use `Bridge Wi-Fi` for the
  primary bridge AP workflow and `Same Wi-Fi adv` for the optional existing-network workflow. Do
  not present USB gadget networking as a supported camera wired mode; label it `USB debug`.
- Settings rows that change values must open explicit option lists. Do not rely on blind
  parent-row cycling for camera FTP path, printer type, image fit, JPEG quality, auto print,
  no-film test, keepalive, or idle poweroff.
- Avoid repeated status text on the 240x240 LCD. The top bar owns compact live status
  (`Bridge Wi-Fi | Sq 8/10`); body content should show the action or workflow, not repeat the
  same state as chips, subtitles, and headlines.
- Do not show `Ready to print` unless the printer is ready and at least one FTP receive path
  is visible on a supported camera path: Bridge Wi-Fi `192.168.8.1` or Same Wi-Fi.
- Keep the three FTP transport subnets distinct. Do not use `192.168.7.2` for Wi-Fi; the
  `192.168.7.0/24` subnet is reserved for the USB gadget admin/diagnostics link.
- If film remaining is `0` and `No-film test` is off, show `NO FILM` / `No Film Left`; do not
  show `READY`.
- If film is already known to be `0/10` when an FTP image arrives and `No-film test` is off, skip
  the BLE transfer and show `No Film Left`.
- Storage is ephemeral: incoming and processed images are cached only as needed for queueing, retry, and short-term diagnostics.
- Camera inputs may be JPEG/HIF/ARW and may be as large as 100 MP. Decode paths must downsample before creating the Instax JPEG: JPEG uses `Image.draft`, HIF uses `heif-thumbnailer -s 1600`, and RAW uses embedded previews before any half-size rawpy fallback.
- v1 is single-printer by default. Multi-printer pairing belongs in v1.5.
- Completed FTP uploads are queued by file path in `asyncio.Queue(maxsize=100)` and processed
  strictly one at a time.
- InstantLink Bridge owns printer wakefulness while running: when a selected printer is online, keep
  the BLE connection open and poll status every `printer.keepalive_interval_s` seconds, default
  10 s, using known-safe status commands rather than an undocumented sleep-disable opcode.

## Things That Will Trip You Up

- The current implementation lives in this InstantLink repository under `bridge/`. The old
  standalone InstantBridge app and `/opt/InstantBridge` device install are legacy migration sources,
  not the place for new feature work.
- The working full implementation lives in the parent InstantLink workspace, especially `crates/instantlink-core/src/protocol.rs`, `printer.rs`, `transport.rs`, `commands.rs`, `models.rs`, and `image.rs`.
- The default printer runtime uses `crates/instantlink-ffi`, built to
  `/opt/InstantLinkBridge/lib/libinstantlink_ffi.so`. Do not debug new printer connection failures in
  Bleak first unless `INSTANTLINK_BRIDGE_PRINTER_BACKEND=bleak` is explicitly set.
- The deployment script now stops and, if needed, force-clears stuck bridge processes before updating
  files. The systemd unit also has `TimeoutStopSec=12` because btleplug/BlueZ status calls can wedge
  during shutdown when a printer is offline.
- `libgphoto2` 2.5.31 has a `has_sony_mode_300` regression affecting ILCE-7C/7CM2; pin to 2.5.30 if the deferred PTP path is used.
- BlueZ bonded printers must be marked `Trusted=true` under `/var/lib/bluetooth/<adapter>/<device>/info` or reconnect after reboot can fail.
- No public Instax Link sleep-disable command is documented. Keep the printer awake through
  periodic BLE status activity; if that proves insufficient, implement a persistent connection
  manager before inventing unknown protocol commands.
- The USB cable must be validated as a data cable for admin/diagnostics. A fully VBUS-isolated
  cable can leave the Pi gadget in `not attached`; the Pi must be independently powered, but the
  gadget controller still needs host attach/VBUS signaling with a normal host.
- Direct Sony USB-LAN via the Pi Zero USB gadget is unsupported for v1. The 2026-05-22
  Mac-proven cable/camera retest showed the same cable and Pi gadget enumerate on macOS while the
  Sony a7C II still left the Pi at `UDC state=not attached` with no `usb0` carrier.
- Sony Save/Load FTP Settings are same-model only; cross-camera provisioning must use Transfer & Tagging Bluetooth push or manual entry.
- Pi Zero 2 W has no working S3 suspend; use X306 hardware poweroff plus fast cold boot.
- X306 battery level is LED-only. Do not display fake bridge battery percentage or trigger
  low-battery shutdown unless a telemetry-capable backend such as PiSugar is explicitly configured.
- X306 automatic idle poweroff defaults off. It may be enabled from Settings > System, but dim and
  screen-off are the normal idle behavior.

## BMAD Development Workflow

- Claude Code is the orchestrator: maintain specs, split work into epics/stories, and integrate bounded changes.
- Codex CLI handles bounded parallel coding tasks when a story can be isolated by module or file ownership.
- Gemini CLI is used for research/validation, especially hardware, OS, BLE, and camera behavior checks.
- OpenClaw performs autonomous QA passes against acceptance criteria once implementation exists.
- Work proceeds spec -> epic -> story -> acceptance criteria -> implementation -> verification. Do not jump from an epic directly into broad implementation.

## Non-Goals

- Do not build an "auto-print every shot" camera workflow for v1.
- Do not support non-Sony cameras in v1.
- Do not build cloud sync.
- Do not build photo editing beyond deterministic auto-rotate, resize/crop, and color-safe
  preparation for Instax.

# Decision Log

ADR format: context, decision, status, consequences.

## ADR-001: Use Pi Zero 2 W for v1

Status: Accepted

Context: Candidate platforms included ESP32-S3, Pi Pico 2 W, and Raspberry Pi Zero 2 W.

Decision: Use Raspberry Pi Zero 2 W for v1.

Consequences: Python code reuse, Pillow, Bleak, BlueZ, pyftpdlib, and systemd are available immediately. Boot-time and power work are harder than on microcontrollers, but the library ecosystem and Linux USB gadget support reduce project risk.

## ADR-002: Use Hotspot-First Sony FTP

Status: Superseded

Context: Sony cameras support multiple transfer paths, including PTP/MTP-like tooling, mobile app flows, and FTP transfer.

Original decision: Use `dwc2` + `g_ether` USB-LAN gadget mode and Sony FTP Transfer with `FTP Trans. (This Img.)`.

Updated decision: For v1, use Sony FTP Transfer with `FTP Trans. (This Img.)` over the bridge hotspot first. Same-Wi-Fi peer FTP is optional. Keep the USB gadget network for admin, SSH, and diagnostics only.

Consequences: The user gesture remains Sony-supported and clean in playback, and the bridge avoids Creators' App reverse engineering. Direct Sony USB-LAN via the Pi Zero gadget is unsupported for v1 because the 2026-05-22 Mac-proven cable/camera retest showed the same cable and Pi gadget enumerate on macOS while the Sony a7C II did not attach to any tested USB Ethernet gadget personality.

## ADR-003: Auto-Print After 1.5 s Preview

Status: Accepted

Context: Instax film costs enough per shot to make accidental prints undesirable, but confirmation prompts slow a social/gift workflow.

Decision: Default to auto-print after a 1.5 s preview; KEY2 cancels during the preview.

Consequences: The workflow feels immediate while preserving a short safety window. Story acceptance tests must verify KEY2 cancellation timing.

## ADR-004: Use BlueZ Through Bleak

Status: Superseded

Context: Direct BlueZ D-Bus control gives maximum control but increases implementation surface.

Decision: Keep the Python/Bleak path as a diagnostic fallback, with `dbus-fast` installed for the required BlueZ pairing agent.

Consequences: Bleak's abstraction was mature enough for the first Python transport, but hardware testing on 2026-05-24 showed a Square Link advertising reliably while Bleak disconnected during GATT service discovery. See ADR-009 for the runtime replacement.

## ADR-005: Use Raspberry Pi OS Lite 64-bit Trixie

Status: Accepted

Context: The Pi Zero 2 W supports 64-bit Linux and the project depends on Python packages with native wheels.

Decision: Use Raspberry Pi OS Lite 64-bit Trixie / Debian 13.

Consequences: arm64 Python wheel availability is better for Pillow, Bleak, and python-gphoto2 if the deferred fallback path is used. The image must be trimmed aggressively to hit the boot budget.

## ADR-006: Use Captive Portal Provisioning

Status: Accepted

Context: The device has a 240x240 display and a joystick, unsuitable for typing Wi-Fi or FTP credentials. A native app adds store and platform friction.

Decision: Use a NetworkManager AP plus captive portal wizard.

Consequences: A phone or laptop can provision the bridge without SSH. Provisioning introduces NetworkManager/dnsmasq interactions that must be isolated from the USB gadget diagnostics network.

## ADR-007: Use X306 Hardware Poweroff Instead of Suspend

Status: Accepted

Context: Pi Zero 2 W does not have a practical working S3 suspend path, including the class of issues tracked around raspberrypi/firmware #1635.

Decision: Use the SupTronics/Geekworm X306 hardware power path and optimize cold boot to the ready state. Keep PiSugar support only as an optional alternate backend.

Consequences: Idle power is managed by shutdown rather than suspend/resume. The boot target becomes a product-critical metric: ready in 7 s or less.

## ADR-008: Use InstantLink as the Working Protocol Reference

Status: Accepted

Context: The parent InstantLink workspace is the working full implementation for Instax Link printing behavior. It includes a core protocol library plus CLI/macOS integrations.

Decision: Use InstantLink's core crate as the authoritative implementation reference for the Python BLE port, especially `crates/instantlink-core/src/protocol.rs`, `printer.rs`, `transport.rs`, `commands.rs`, `models.rs`, and `image.rs`.

Consequences: Future Python implementation should port behavior deliberately from InstantLink rather than reverse-engineering again from InstaxBLE alone. Superseded in part by ADR-009: InstantLink is now the v1 runtime backend, not only a reference.

## ADR-009: Use InstantLink Rust Backend for Printer Runtime

Status: Accepted

Context: On-device testing with a Square Link showed that the Pi could see `INSTAX-52006924 (IOS)` at strong RSSI, but Bleak failed every isolated connection attempt during service discovery, even with the InstantLink Bridge service stopped, scanning disabled, and no service UUID filter. InstantLink already contains the working Rust `instantlink-core` transport using `btleplug`, plus C FFI and CLI crates.

Decision: Use InstantLink's Rust FFI as the default v1 printer backend. InstantLink Bridge remains responsible for FTP receive, HIF/RAW/JPEG preprocessing, the print queue, settings, and the LCD UI. Keep the Python/Bleak implementation behind `INSTANTLINK_BRIDGE_PRINTER_BACKEND=bleak` as a diagnostic fallback.

Consequences: The printer UX becomes "select printer" rather than BlueZ pair/trust. Runtime provisioning must build and install `crates/instantlink-ffi` to `/opt/InstantLinkBridge/lib/libinstantlink_ffi.so` and the optional CLI to `/opt/InstantLinkBridge/bin/instantlink`. The deploy path can copy prebuilt InstantLink artifacts when the Pi has no internet.

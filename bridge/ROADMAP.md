# Roadmap

## v1: Camera-to-InstantLink Bridge

v1 ships the core giftable appliance behavior.

- Hotspot-first camera FTP profile for selected JPEGs.
- Optional same-Wi-Fi peer FTP profile.
- USB gadget admin/SSH/diagnostics profile, not a supported v1 camera wired mode.
- Image pipeline to model-specific Mini, Mini Link 3, Square, or Wide JPEG.
- Printer scan, status, model detection, and print path through the InstantLink Rust FFI backend.
- LCD UI with preview, cancel, printing, ready, settings, and errors.
- Explicit state machine and bounded image queue.
- X306 UPS/no-telemetry power backend, idle display management, and idle shutdown.
- Captive portal provisioning.
- Error handling and recovery rules.
- OTA design documented but deferred.

## v1.5: Household Product Polish

- Transfer & Tagging integration path for easier camera profile loading.
- Multi-camera profiles.
- Multi-printer pairing.
- Reset-for-gifting workflow.
- Localization for setup and LCD text.
- Local print log with privacy-preserving retention controls.
- Better film count/status surfacing.

## v2: Managed Appliance

- A/B OTA using `rpi-image-gen` and `rpi-connect-ota`.
- Signed firmware/update manifests.
- Watchdog rollback.
- Wi-Fi receive from phone as an alternate source.
- Possible hardware rebuild on ESP32-S3 or Pimoroni Pico Plus 2 W if Python/Linux burden becomes the dominant issue.

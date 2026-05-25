# BLE Bonding Notes

Target stack:

- BlueZ 5.79+
- InstantLink Rust FFI (`btleplug` backend) as the default runtime path
- Bleak 1.1.1 only as a diagnostic fallback
- Fujifilm Instax Mini, Mini Link 3, Square, or Wide Link advertising as `INSTAX-XXXXXXXX`

## Current v1 Behavior: Select, Then Bond Headlessly

Hardware testing on the Pi showed Instax Link printers may advertise as both platform-specific
endpoints, for example:

- `INSTAX-1N034655(IOS)` on a random BLE address such as `FA:AB:BC:*`
- `INSTAX-1N034655(ANDROID)` on a public/classic-looking address such as `88:B4:36:*`

The LCD `Find printer` action is still a printer selection flow, not a manual OS pairing wizard:

1. scan through InstantLink for an `INSTAX-*` advertisement,
2. normalize the name by removing `(IOS)`/`(ANDROID)`,
3. persist the selected printer to `/var/lib/InstantLinkBridge/printer.json`,
4. show `Searching` on boot while reconnecting to the selected printer, then `READY` only after a
   successful status read.

The LCD starts this scan immediately from `Find printer`, KEY1 on the no-printer screen, or KEY3 on
status screens. There is no second confirmation screen for printer selection.

At connection time, however, Instax Link printers can request BLE bonding before exposing GATT
characteristics. InstantLink Bridge therefore registers a process-owned BlueZ `NoInputNoOutput` agent on
startup and sets the adapter `Pairable=true`. This allows Just-Works bonding without requiring SSH
or an interactive `bluetoothctl` session.

On 2026-05-24, a Square Link advertised as `INSTAX-52006924 (IOS)` at
`FA:AB:BC:C7:95:64`. Direct Bleak connection attempts with an address, a `BLEDevice`, with and
without a service UUID filter, all failed during service discovery with `device disconnected`.
`btmon` then showed the actual root cause for the Pi path: the printer sent an SMP Security Request
for bonding, BlueZ replied `Pairing Failed: Pairing not supported`, and the printer terminated the
connection before services resolved. After adding the in-process agent, the same printer bonded and
reported status successfully:

- `Paired: yes`
- `Bonded: yes`
- `Trusted: yes`
- model `square`
- film remaining `6`
- battery `100`

## Manual `bluetoothctl` Bonding Flow, If Needed

```text
bluetoothctl
power on
agent NoInputNoOutput
default-agent
scan on
# wait for INSTAX-XXXXXXXX
pair <MAC>
trust <MAC>
connect <MAC>
info <MAC>
scan off
quit
```

The critical step is:

```text
trust <MAC>
```

Without `Trusted=yes`, reconnect after reboot can fail even though pairing appeared successful.

## Bond Persistence

BlueZ stores adapter and device state under:

```text
/var/lib/bluetooth/<adapter-mac>/<device-mac>/info
```

Inspect the device file:

```bash
sudo sed -n '1,120p' /var/lib/bluetooth/<adapter>/<device>/info
```

Expected section:

```ini
[General]
Trusted=true
```

If a printer becomes stale or Bleak raises `BleakDeviceNotFoundError`, the recovery path may need:

```text
bluetoothctl remove <MAC>
```

Then pair/trust/connect again.

InstantLink Bridge keeps its own selected-printer record in
`/var/lib/InstantLinkBridge/printer.json`. The recovery path for a stale selected printer is:

1. forget the selected printer record,
2. remove BlueZ cache entries matching either the selected address or normalized Instax name,
3. scan again and save the first visible Instax candidate.

The pairing service exposes this as `BluetoothctlPrinterPairer.forget_selected()`. It removes both
the persisted selection and matching cached BlueZ addresses, including split `(IOS)` and
`(ANDROID)` advertisements for the same normalized printer name.

Status scans now keep structured diagnostics for support and stale-device debugging:

- visible Instax candidate count,
- each visible candidate name and address,
- whether the selected printer was visible by address or normalized name.

If both the Bleak scanner and BlueZ fallback run during a failed status lookup, the diagnostics keep
the union of visible candidates from both scanner passes.

## Implementation Notes

- Use a cached `BLEDevice` only while it remains valid.
- Use `disconnected_callback` to emit state-machine events.
- Reconnect with capped backoff: 1 s, 2 s, 5 s, 15 s.
- Short-circuit discovery with `services=[INSTAX_SERVICE_UUID]` when Bleak/BlueZ supports it.
- Subscribe to the status characteristic so remaining-film count can update the UI.
- Keep the selected printer awake with BLE activity while InstantLink Bridge is running. The v1 policy
  is to keep a cached BLE connection open after the first successful status read and poll status
  every `printer.keepalive_interval_s` seconds, default 10 s. That status path sends only
  known-safe commands already used by the protocol implementation:
  `battery_status`, `printer_function_info`, and `history_info`.
- Status polling and printing share a BLE session manager. Status acquires the session briefly and
  releases it back to the cache, so a ready printer can be handed to print without waiting for a
  fresh advertisement. Clean short-lived status disconnects keep the resolved endpoint cached;
  protocol or connect failures clear the stale cached session and fall back to scanner resolution.
  Service shutdown, selected-printer forget, or a hard recovery path should explicitly close the
  shared session manager.
- Do not add an undocumented "disable sleep" or "wake lock" opcode without captures from a known
  Fujifilm app. The current public references show status/connection activity, not a supported
  sleep-disable command.

## References

- Bleak issue #676: `https://github.com/hbldh/bleak/issues/676`
- Bleak issue #992: `https://github.com/hbldh/bleak/issues/992`
- `../macos/InstantLink/Core/ViewModel.swift`: connected status refresh timer
  runs every 10 seconds.
- `javl/InstaxBLE`: examples wait before disconnecting as a workaround for
  disconnect timing.

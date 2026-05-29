# Plan 033 — Bridge UX polish and headless variant

Status: **proposed** (2026-05-28)
Successor to: plans 031 (connection recovery) and 032 (boot speed).
Owner: bridge UX + Mac app.

## Why now

Two workstreams just landed:
- Plan 031 — ~4 s active reconnect on a power cycle.
- Plan 032 — ~15 s power-on → scanning.

With the device fast and reliable, the remaining gap to a giftable product is
**the UI**. The LCD's settings menu mixes status, configuration, and
diagnostics; users see "Network → USB debug" and "FTP receive mode" before
they see "Pair printer." The on-device menu also blocks the cheaper SKU we
want to build: a Pi Zero 2 W with no LCD, no joystick, and no keys — driven
entirely from the Mac app, with a single onboard LED for at-a-glance status.

The Mac↔bridge channel (`BridgeHTTPTransport`, ed25519-signed identities,
`manager/api.py` aiohttp server) already exists and already streams
update-progress events. Extending it to carry the live UI is a small additive
step on a proven channel — not a new transport.

## North-star UX (the test for every change)

There are **two operating models**, one per SKU. They share the same setup
flow but have different daily-use flows.

### Setup (both SKUs, one-time)

1. Plug the bridge in — USB to Mac, or wall power + label-printed Wi-Fi creds.
2. Open the InstantLink Mac app. The bridge appears in the sidebar.
3. Tap "Pair printer" once. The printer's LED handoff confirms.
4. Press C1 on the Sony. Photo prints.

### Daily use — LCD SKU
- Press the X306 power button → boot → reconnect → ready.
- Pair button on the LCD remains as a manual recovery affordance.
- LCD shows status; Mac is optional once paired.

### Daily use — headless SKU (the camera-powered design)
- **Power source: camera hot-shoe.** The bridge mounts in the cold shoe and
  is wired into the hot-shoe pins. Camera on → bridge boots from cold.
- Bridge reconnects to the remembered paired printer automatically.
- **LED green = ready to print.** That is the only signal the user needs.
- Press C1 on the Sony → FTP transfer → print.
- Camera off → bridge loses power → no idle logic required.

The implication that drives this plan: **on the headless SKU, every camera
power-on is a cold boot.** Plan 032's ~15 s power-on→scanning figure is the
load-bearing UX metric for this product, not a benchmark. See §6.2.

The on-device LCD (when present) is a **status surface and a recovery
affordance** — never a configuration tool. The Mac app is the **setup
surface**, used during pairing and configuration changes, but not required
for daily operation on either SKU. The status LED is the only mandatory
on-device feedback.

## Decisions (settled 2026-05-28)

| # | Decision | Choice |
|---|---|---|
| D1 | LCD SKU long-term role | **Both ship.** LCD-SKU and headless-SKU are parallel products; every UX decision optimises for headless, LCD inherits. |
| D2 | Mac virtual-display render | **Native SwiftUI mirror.** Stream the `UiSnapshot` as structured JSON; preview JPEGs ride a separate sub-resource. |
| D3 | First-run for screen-less SKU | **Both USB-tether and hotspot.** USB wins when present; hotspot is the cordless fallback. Race resolution: see §6.4. |
| D4 | Status LED hardware | ~~Pi onboard ACT LED~~ → **No on-device LED.** Revised 2026-05-28 after hardware verification: the Pi ACT LED is hardwired to SD-card activity and cannot be reliably commandeered for status patterns on Pi Zero 2 W. Adding a discrete LED was rejected to keep the headless BOM minimal. Instead, the camera-side FTP transfer success/failure message becomes the user-visible signal: "C1 succeeded" = bridge worked; "C1 failed" = retry or check Mac app. Phase 4 (LED service) is deleted from this plan. **Consequence:** the user is shooting blind from camera-on until the first FTP transfer either lands or errors. This makes §6.2 (boot speed) the most user-critical lever in the plan, not a nice-to-have. |
| D5 | Headless SKU power source | **Camera hot-shoe.** Bridge powered from the camera's accessory shoe; camera on/off === bridge on/off. No X306 on the headless BOM. Implies every camera power-on is a cold boot — boot speed becomes load-bearing (see §6.2). |

## What's broken today (settings audit, evidence-based)

From `bridge/src/instantlink_bridge/ui/settings.py`:

1. **Main menu is 5 pages**, only 1 is action-oriented:
   - `Printer` (the only one users want)
   - `Camera` (mis-labelled; it's FTP credentials)
   - `Network` (Wi-Fi / Bluetooth / USB debug — overlapping concepts)
   - `Print` (image fit, JPEG quality — once-set-and-forget knobs)
   - `System` (9 read-only diagnostics)
2. **Duplicate data**: `NETWORK_HOTSPOT_SSID_INFO` and `NETWORK_HOTSPOT_PASSWORD_INFO` appear under BOTH `Camera` and `Network`. Same fact, two locations, no source of truth.
3. **End-user exposure of admin concepts**: `USB debug`, `Same Wi-Fi adv`, `BlueZ version`, `Python version`, `Refresh status`. Users don't reason in these terms.
4. **`Refresh status`** is an action with an invisible effect.
5. **Top-level vocabulary mismatches user mental model**: `Camera` doesn't talk about the camera; `Network` is required reading before pairing.

The structural fix isn't "rename things" — it's **stop using the LCD as a
configuration surface at all.**

## Existing leverage (don't reinvent)

| Surface | Where | What it gives us |
|---|---|---|
| `BridgeTransport` (Swift protocol, 14 methods) | `macos/InstantLink/Core/BridgeTransport.swift` | Discovery, pairing, ed25519 identity, status, streaming update events |
| `manager/api.py` (aiohttp) | `bridge/src/instantlink_bridge/manager/api.py` | Routes for `/v1/hello`, `/v1/pairing/*`, signed handlers, async streaming pipe |
| `UiController` → `Display` sink | `bridge/src/instantlink_bridge/ui/controller.py` + `ui/display.py` | Already emits immutable `UiSnapshot`s; already accepts pluggable Display backends (`NullDisplay`, `LumaSt7789Display`, `FramebufferDisplay`) |
| `GpioUiInput` ↔ `NullInput` | `bridge/src/instantlink_bridge/ui/input.py` | Headless input is already a factory swap; the action queue is the same |
| `UiSnapshot` dataclass (frozen, slotted) | `bridge/src/instantlink_bridge/ui/models.py` | Serialisable; PIL `preview_image` is the only non-trivial field |
| Pi onboard ACT LED | `/sys/class/leds/ACT/{trigger,brightness}` | Writable from `User=ib` with `gpio` group; no extra hardware |

**The state machine, snapshot model, render output, and Mac auth channel are
all already decoupled enough that this plan is mostly *wiring*, not new code.**

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Bridge (Pi Zero 2 W, headless OR LCD)                          │
│                                                                  │
│   UiController ── emits ──> UiSnapshot ── consumed by ──┐       │
│        ▲                                                 │       │
│        │ UiAction queue                                  ▼       │
│        │                                          ┌─────────┐   │
│        └── consumes ──────────────────────────────│ Display │   │
│                                                   │  sinks: │   │
│                                                   │ LCD     │ ◀─ headless: omitted
│                                                   │ Network │ ──┐
│                                                   └─────────┘   │
│                                                                  │
│   manager/api.py (aiohttp)                                       │
│     /v1/hello   /v1/pairing/*   (existing)                       │
│     /v1/config  (Phase 2, new)                                   │
│     /v1/ui      WebSocket (Phase 3, new) ◀───────────────────────┘
│                                                                  │
└──────────────────────────────────────┬──────────────────────────┘
                                       │  ed25519-signed HTTP/WS
                                       │
┌──────────────────────────────────────▼──────────────────────────┐
│  Mac app (SwiftUI)                                              │
│                                                                  │
│   BridgeTransport  (existing) ── adds: getConfig / patchConfig  │
│                                  adds: openUIStream (WS)         │
│                                                                  │
│   "Bridge" sidebar                                               │
│     ├── Status (existing)                                        │
│     ├── Settings  (Phase 2, new — canonical config)              │
│     └── Remote    (Phase 3, new — virtual display + controls)    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

The `Network` Display sink is the load-bearing piece: it broadcasts the
*same* `UiSnapshot` the LCD renders, with no UI fork. The Mac app re-renders
that snapshot natively in SwiftUI (per D2) for Retina sharpness.

## §6.2 Headless boot speed is the load-bearing metric

For the headless SKU, the entire daily-use UX collapses to one number:

> **Camera-on → LED-green latency.**

This is the time from the camera switching on (closing the hot-shoe power
contact) to the bridge being paired-connected and ready to receive an FTP
upload. Every component on the path contributes:

| Phase | Time today | Reference |
|---|---:|---|
| Pi boot → bridge READY=1 | ~15.0 s | Plan 032 §10 |
| Bridge READY → BLE active reconnect to remembered printer | ~4 s | Plan 031 §14 |
| **Total camera-on → LED-green** | **~19 s** | Today |

19 s is a long time to stand there with a camera in your hand. The user has
to either wait (frustrating) or shoot first and accept that early shots
won't print until later (confusing — "did it work?").

**Target for v1 headless:** ≤ 12 s camera-on → LED-green.

This is achievable by composing pre-existing leverage:

- **Plan 032 next levers** (documented but unshipped): dd-splash (~−1 s), lazy FFI load (~−1 s), `__pycache__` pre-bake (~−0.5 s). All compatible.
- **Headless removes the splash unit and the LCD init entirely** (~−1 s further). The splash is cosmetic on the LCD-SKU; gone on headless.
- **Boot-time BLE warm-up:** start scanning *before* the FTP service is fully up. The bridge can reasonably bet that the saved printer is the target; if FTP isn't ready by the time the photo arrives, the camera retries (Sony FTP has built-in retry). This is a meaningful inversion of the M3 plan-032 ordering and might be worth ~1-2 s.
- **No userland warmup of luma/gpiozero/etc on headless** (~−0.5 s).

Composed estimate: ~12 s, leaving buffer for variance.

**This becomes Phase 0.** Boot-speed work for headless ships before the
Mac-facing Phases 2/3, because nothing in the Mac app matters if the device
can't be ready in time for a typical "shoot a portrait, print it" gesture.

(For the LCD SKU, boot speed is nice-to-have, not load-bearing — the user
explicitly powers it on and waits.)

## Phases

### Phase 1 — Strip the LCD menu to status + one action

Make the on-device LCD a status-and-recovery surface. The Mac app becomes the
config surface (Phase 2) — Phase 1 prepares that transition without breaking
the existing LCD-SKU experience.

**New `SettingsPage.MAIN` layout (3 items):**

| Row | Action |
|---|---|
| `Pair printer` | (current `PAIR_PRINTER`) |
| `Help` | Shows a QR code linking to the Mac app + this bridge's ID |
| `Diagnostics` | The current `System` page contents, with no value changes |

**Removed from user-facing navigation (still reachable via long-press for field support):**
- The entire `Camera` page (data moves to Mac Settings).
- The entire `Network` page.
- The entire `Print` page.
- `System > Refresh status` (no observable effect).

**Long-press diagnostics combo:** KEY1 + KEY3 held 2 s opens the full
current menu. Documented in `docs/development/bridge-diagnostics.md`.

**Acceptance:**
- Fresh-eyes user walked through the LCD reaches `Pair printer` within 2 button presses from the home screen.
- No data is lost — all settings still in `config.toml`; just relocated.
- `SettingsPage.MAIN` has ≤ 3 rows.
- Long-press combo restores full menu access; pytest covers the combo path.

**Files touched:** `ui/settings.py`, `ui/controller.py`, `ui/render.py`,
`docs/development/bridge-diagnostics.md` (new).

### Phase 2 — Mac-side settings as canonical config

Two new endpoints on the existing aiohttp surface:

```python
GET   /v1/config   -> { "schema_version": 1, "config": {…} }
PATCH /v1/config   <- { "patch": {…partial config…} }  -> 200 + updated config
```

Both are ed25519-signed via the existing middleware. The PATCH handler reuses
`config_with_setting_value` and the `BridgeConfig` `replace(...)` machinery
that the LCD menu already uses, so there is exactly one config-mutation code
path. Writes are atomic (write to temp, fsync, rename) — same as the
existing LCD path.

**Mac app additions:**
- `BridgeTransport.getConfig(device) async throws -> BridgeConfig`
- `BridgeTransport.patchConfig(device, patch) async throws -> BridgeConfig`
- New `BridgeSettingsView` in SwiftUI organised as Mac users expect:
  - **Photos** (FTP path, fit mode, JPEG quality, auto-print delay)
  - **Printer** (selected printer, search rate, keepalive, no-film test)
  - **Network** (hotspot SSID/password — view only here; Wi-Fi provisioning is a separate flow)
  - **Power** (idle poweroff toggle)
  - **About** (versions, device ID — what `System` is today)

Copy is rewritten for a desktop audience. "FTP receive mode" becomes
"How should photos reach the bridge?" with explanations of each option and a
visible recommended default.

**Acceptance:**
- Every setting currently reachable via the LCD is reachable from the Mac app.
- Changing a setting from the Mac is reflected on the LCD within one render tick (≤ 250 ms).
- Concurrent edits (Mac + LCD long-press) don't lose data; last-writer-wins is fine for v1.
- New `bridge/tests/manager/test_config_endpoints.py` covers schema validation and atomic write.

**Files touched:** `manager/api.py`, `config.py` (schema export), new Mac
files `BridgeSettings*.swift`, new test file.

### Phase 3 — Virtual display + virtual controls (WebSocket)

The Mac app needs to *see* the bridge as if looking at the LCD, and *act* on
it as if pressing the joystick. We add one endpoint:

```
WS /v1/ui   (ed25519-signed handshake)

server -> client:
  { "type": "snapshot", "snapshot": { …UiSnapshot serialized… } }
  { "type": "preview",  "snapshot_id": "…", "jpeg": "<base64>" }
  { "type": "led",      "pattern": "ready" | "scanning" | … }

client -> server:
  { "type": "action", "action": "up"|"down"|"left"|"right"|"select"|"back"|"help"|"pair" }
  { "type": "ping" }
```

**Why structured JSON + separate JPEG (D2 → "Hybrid: structured")**: the
`UiSnapshot` is small (~1 KB); only `preview_image` is large. Streaming
structured snapshots lets the Mac render natively in SwiftUI (Retina sharp,
keyboard-shortcut-able, accessible). The JPEG sub-resource handles the only
field SwiftUI can't trivially re-render.

**Server side (`bridge/src/instantlink_bridge/ui/network_display.py`, new):**

```python
class NetworkDisplay:
    """Display sink that broadcasts UiSnapshots to subscribed WS clients."""

    async def render(self, snapshot: UiSnapshot) -> None: ...
    def set_idle_stage(self, stage: str) -> None: ...
    async def subscribe(self, ws: WebSocketResponse) -> None: ...
```

`UiController` already calls `display.render(snapshot)` once per state
change; we install a `CompositeDisplay([LumaSt7789Display(), NetworkDisplay()])`
on LCD-SKU and `CompositeDisplay([NetworkDisplay()])` on headless. Identical
state machine, identical snapshot, two sinks.

Inbound `action` events feed the same `asyncio.Queue[UiAction]` that
`GpioUiInput` populates today. Auth and rate-limiting at the handler level.

**Mac side (`macos/InstantLink/Features/BridgeRemote/`, new):**
- `BridgeRemoteView.swift` — top-level SwiftUI view rendering each `UiMode` natively. One file per mode-cluster (status / settings / preview / printing).
- `BridgeUiSnapshotDecoder.swift` — JSON ↔ `BridgeUiSnapshot` struct mirroring the Python dataclass.
- Keyboard shortcuts: arrow keys → joystick, Return → SELECT, Backspace/Esc → BACK.
- Click affordances on virtual joystick + KEY1/KEY2/KEY3 for trackpad users.
- Reconnect-on-drop with exponential backoff; banner if disconnected > 5 s.

**Acceptance:**
- A `UiAction` from the Mac client produces the same state transition as the same action from the LCD joystick (proven by paired tests).
- Snapshot latency Mac→render < 200 ms p95 on USB-tether, < 500 ms p95 on hotspot.
- Mac app reconnects automatically after a bridge reboot.
- Two Mac clients can subscribe simultaneously (no exclusive lock).

**Files touched:** new `ui/network_display.py`, new `ui/composite_display.py`,
`manager/api.py` (WS route), several new Swift files, paired tests in both
languages.

### ~~Phase 4 — Status LED service~~ (deleted 2026-05-28)

Originally a `bridge/src/instantlink_bridge/ui/led.py` that would drive the
Pi onboard ACT LED with 5 blink patterns mapping the `UiMode` state space.
Deleted after hardware verification: the ACT LED on Pi Zero 2 W is
hardwired to SD activity (trigger override unreliable on stock kernel) and
adding a discrete LED was rejected to keep the headless BOM minimal.

Per D4 (revised), the camera-side FTP transfer message is the
user-visible signal on the headless SKU. Phase 0 (boot speed) compensates
for the loss of an "is it ready?" affordance by making the
camera-on → ready-to-print latency short enough that the user can shoot
within seconds of camera-on and trust that early errors mean "wait a
moment, try again."

### Phase 5 — Headless SKU (camera-hot-shoe powered)

Make the absence of LCD + buttons a first-class configuration, *and* design
around the camera hot-shoe as the power source. The product story is: mount
to the cold-shoe, wire to hot-shoe, never touch it again.

**Config schema additions:**

```toml
[ui]
surface = "lcd"        # "lcd" | "headless"
input   = "gpio"       # "gpio" | "none" (auto-implied by surface)

[power]
source  = "x306"       # "x306" | "hotshoe"  (per D5)
# hotshoe: no battery telemetry, no idle-poweroff,
# camera off === bridge off; boot is always cold.
```

**Bridge changes:**
- `app.py` gates `luma.lcd` and `gpiozero` imports on `config.ui.surface != "headless"`. Saves ~half the existing UI startup cost on headless. (Compounds with plan 032 levers — see §6.2.)
- `bridge/systemd/instantlink-bridge-boot-splash.service` is `ConditionPathExists`-guarded on a fb1 device that doesn't exist on headless; the unit skips itself cleanly with no errors.
- `power/x306.py` is not loaded on `power.source = "hotshoe"`. A new `power/hotshoe.py` is a stub backend that reports "external, no telemetry" and disables idle-poweroff handling entirely (the camera does it for us).
- New `bridge/systemd/instantlink-bridge-headless.preset` enables only what's needed.

**Pairing persistence (load-bearing on this SKU):**

Because every camera power-on is a cold boot, pairing memory must survive
power loss without any user action. Both pieces of state already do:
- `BridgeConfig.printer.address` lives in `/etc/InstantLinkBridge/config.toml` on the SD card.
- BlueZ bond data lives under `/var/lib/bluetooth/<adapter>/<device>/` with `Trusted=true` (per `bridge/CLAUDE.md`).

Acceptance test added: cold-cycle the bridge 10× in a row with the printer
on; every cycle must reach `READY` without any human input.

**Reconnect after cold boot:**

The plan-031 hybrid connect handles this well: ~4 s active reconnect once
the bridge starts scanning. With ~15 s boot + ~4 s reconnect, total camera-on
→ ready-to-print on the headless SKU is **~19 s**. That number is the
metric to drive down via the §6.2 levers.

**First-run path (D3 → both):**

The race resolution is *order-based, not state-based*:

1. **If USB-tether enumerates within 8 s of boot**, the bridge advertises mDNS only over `usb0`. Mac app sees it there; hotspot is suppressed until pairing completes.
2. **Otherwise**, the bridge brings up the Wi-Fi AP with a default SSID/password printed on the device label (e.g. `InstantLink-A1B2` / 8-digit password derived from `device_id`). mDNS advertises on `wlan0`.
3. After the first successful pairing + Wi-Fi provisioning, both transports remain available but the bridge prefers the credentialed home Wi-Fi.

The first-run flow runs on its own bench power (USB-C wall wart), not on
camera power. Once the bridge has remembered the printer and the home Wi-Fi
(for FTP path) or has been told "use Bridge Wi-Fi" mode, the user moves it
to the camera hot-shoe and forgets about setup.

**Headless BOM** (`bridge/HARDWARE-headless.md`, new):
- Drop: Waveshare 1.3" LCD HAT, joystick, KEY1/2/3 (~$14).
- Drop: X306 UPS shield (~$24-35) — replaced by a small buck regulator since the camera provides power.
- Add: Hot-shoe foot with electrical pickup (proprietary or universal Sony hot-shoe pinout), `~$8`.
- Add: 5 V buck regulator (e.g. Pololu D24V5F5), `~$5`.
- Add: Cold-shoe / 1/4-20 mount foot on the bridge body so it slots into a side bracket without occupying the hot-shoe alone (hot-shoe carries power, cold-shoe carries weight).
- Add: Optional bench-power USB-C input for setup (Pi USB-C, already present).
- Keep: Pi Zero 2 W, microSD (prefer high-endurance — many cold cycles).
- Expected unit BOM net change: **roughly flat or slightly cheaper** vs. LCD-SKU; the saving is in form factor + simplicity, not dollars.

The hot-shoe pinout details depend on the camera. Sony a7C II's accessory
shoe is the "Multi Interface Shoe" with documented power pins (~7.2 V when
camera on). We design around that one camera for v1; other Sony bodies
likely work but aren't a goal.

**Acceptance:**
- `config.ui.surface = "headless"` boots cleanly with no imports of `luma.lcd` or `gpiozero` (verified via `python -X importtime`).
- 10× consecutive cold boots (simulating camera on/off) all reach READY without intervention; ACT LED transitions through the expected patterns each time.
- LED service drives ACT LED through the expected pattern transitions during a full print cycle on hardware.
- Mac app pairs, configures Wi-Fi, and prints on a headless bridge with no buttons pressed on the bridge itself.
- First-run race resolution covered by integration test (mocked transports).
- Bench-tested on hot-shoe power: voltage stable through Wi-Fi/BLE radio bursts, no brown-out resets during print transfer.

**Files touched:** `config.py`, `app.py`, `power/hotshoe.py` (new),
`bridge/systemd/`, new `bridge/HARDWARE-headless.md`, doc updates.

### Phase 6 — FTP reply codes as the user-facing signal (added 2026-05-28)

With D4 settled as "no on-device LED", the question "how does the user know
the bridge is ready?" needs an answer. The camera is already in their hand.
FTP (RFC 959) gives us reply codes with arbitrary text. **Use the camera's
existing FTP error display as the bridge's user-facing signal.**

This is a much better answer than an LED could ever be:
- The user sees specific, contextual text ("No film. Load film.") instead of
  guessing what a colour means.
- It works through hardware the user already owns.
- Sony's FTP client may auto-retry transient (4xx) failures for a few seconds
  before showing the user an error — meaning the user might never *perceive*
  the bridge starting up if Phase 0 keeps boot under that retry window.
  (This last claim needs a real-camera test; see Acceptance.)

**Implementation surface:**

In `bridge/src/instantlink_bridge/camera/ftp.py`, override `pyftpdlib`'s
`FTPHandler.ftp_STOR` (the upload command handler) to do a pre-flight
check against the current bridge state *before* the data connection opens.
If the bridge can accept the photo, fall through to the default handler.
If not, emit a meaningful reply.

```python
class InstantLinkFTPHandler(FTPHandler):
    bridge_state: Callable[[], UiSnapshot]   # injected at server construction

    def ftp_STOR(self, file, mode="w"):
        snap = self.bridge_state()                # bare attribute read of frozen UiSnapshot
        # Check order matters: BOOTING comes before not-paired because during the first
        # seconds of boot the paired_printer lookup hasn't run, and "not paired" would
        # be misleading. (Architect note 2026-05-28.)
        if snap.mode is UiMode.BOOTING:
            self.respond("451 Bridge starting, try again in a moment.")
            return
        if snap.paired_printer is None:
            self.respond("501 Bridge not paired. Pair from the Mac app.")
            return
        if not _printer_reachable(snap):
            self.respond(f"451 {snap.paired_printer.name} is offline. Power on the printer.")
            return
        if (snap.film_remaining is not None
                and snap.film_remaining <= 0
                and not snap.allow_print_without_film):
            self.respond("552 No film. Load film and retry.")
            return
        if snap.mode is UiMode.PRINTING:
            self.respond("450 Printer busy, try again.")
            return
        return super().ftp_STOR(file, mode)
```

`BridgeStateSnapshotProvider` is a thin interface owned by the controller
that publishes the current `UiSnapshot`-derived facts (paired? online?
film count?) under a lock-free snapshot pattern (immutable dataclass swap).
Same data the LCD shows; just exposed to the FTP handler.

**Reply-code design:**

| Bridge state | Code | Text | Sony semantics |
|---|---|---|---|
| Bridge still booting / FFI not ready | `451` | `Bridge starting, try again in a moment.` | Transient — Sony may auto-retry for ~5-10 s |
| Printer paired but offline / out of range | `451` | `INSTAX-XXX is offline. Power on the printer.` | Transient |
| Not paired yet | `501` | `Bridge not paired. Pair from the Mac app.` | Permanent — user sees error, must act |
| Film count = 0 | `552` | `No film. Load film and retry.` | Permanent |
| Printer busy with previous photo | `450` | `Printer busy, try again.` | Transient |
| Success | `226` (default) | `Transfer complete.` | Normal success path |

**Hardware test plan (REQUIRED before this phase is "done"):**

1. **Sony text display**: on the a7C II, trigger each error condition and
   confirm what the camera shows. Test cases:
   - Power off the printer → C1 a photo → confirm camera shows offline text.
   - Forget the printer in config → C1 a photo → confirm "not paired" text.
   - Print until film = 0 → C1 a photo → confirm "no film" text.
   - Send two C1 in quick succession → confirm "busy" text on the second.
2. **Sony auto-retry behavior**: with the bridge service stopped (FTP
   server still up via a stub returning 451), C1 a photo. Time how long the
   camera retries before showing the user. This number determines whether
   Phase 0 boot speed remains user-critical or becomes a power-burn concern.
3. **Reply-text length**: confirm Sony shows the full text vs. truncating.
   Bound all texts to ≤ 50 ASCII chars to be safe.

**Acceptance:**

- All five error conditions produce the right reply code + text on the wire (`tcpdump` confirms).
- On the a7C II, each error condition surfaces user-visible text that makes the cause obvious — captured in screenshots.
- Sony retry window measured and documented. Plan 033 §6.2 boot-speed urgency reassessed against that number (insert a footnote here once known).
- `bridge/tests/camera/test_ftp_signal.py` covers the pre-flight logic with mocked state.
- No regression in the success path: a valid C1 photo still prints end-to-end.

**Files touched:**
- `bridge/src/instantlink_bridge/camera/ftp.py` — `InstantLinkFTPHandler` subclass.
- `bridge/src/instantlink_bridge/app.py` — wire the snapshot provider into the FTP server constructor.
- `bridge/src/instantlink_bridge/ui/controller.py` — expose the snapshot-provider interface (read-only handle).
- New `bridge/tests/camera/test_ftp_signal.py`.

**Open questions:**
- Should we wrap the snapshot in a debounce so a flapping "online/offline" doesn't yield contradictory messages on close-spaced C1 presses? Probably yes — debounce window ~2 s.
- Multi-print queueing: if the camera sends C1 while we're mid-print on a previous photo, do we accept and queue (today's behaviour) or reject with 450? Today's behaviour is correct for the "FTP Trans. (Multi)" workflow; only reject if the queue is full.

### Phase 7 — Unified status indicator (added 2026-05-29)

**Why:** The single-pixel status dot in the top bar is too small to register at arm's length, and the LCD-SKU and the future headless-SKU need the *same* health language: a coloured top bar on LCD = a coloured LED on headless. Both surfaces should consume the same `StatusState`. Encoding the signal explicitly (rather than implicitly via `UiMode → accent`) also lets Phase 5's GPIO LED reuse it without re-deriving anything from the snapshot.

**Signal vocabulary (6 states, 2 patterns):**

| State | Pattern | Color | Trigger |
|---|---|---|---|
| `READY` | solid | green | `UiMode.READY` (printer fresh + can accept), `PRINT_COMPLETE` |
| `PRINTING` | breathing | green | `IMAGE_RECEIVED`, `AWAITING_CONFIRM`, `PRINTING` |
| `NOT_READY` | solid | yellow | `PRINTER_OFFLINE`, `NEEDS_PAIRING`, `PAIR_FAILED` |
| `SEARCHING` | breathing | yellow | `BOOTING`, `PRINTER_SEARCHING`, `PAIRING`, `VALIDATION` |
| `ERROR` | solid | red | `UiMode.ERROR` |
| `WARNING` | breathing | red | `NO_FILM` |

`SETTINGS` inherits — derive from the same non-mode signals (paired_printer presence, printer_status_fresh, film_remaining, can_accept_images) so the bar still reflects bridge health while the user is configuring.

**Pattern definition:**
- Solid: full intensity, no modulation.
- Breathing: 2 s period (0.5 Hz), intensity scaled 60 % → 100 % via `0.6 + 0.4 × (1 + sin(2πt/2))/2`.

**Surface bindings:**
- **LCD**: `draw_status_bar` fills the full 30 px band with `state.tint_at(t)` and chooses text color by luma (white on green/red, black on yellow). The standalone dot is removed.
- **Headless (Phase 5)**: a `GpioStatusSink` consumes the same `StatusState` and drives an RGB LED via PWM. Cadence is identical so users learn one vocabulary across SKUs.

**Abstraction:**
- New `bridge/src/instantlink_bridge/ui/status_indicator.py` owns the enum, the derivation pure function, and the breath modulation math. Both `render.py` (LCD) and the future GPIO driver consume it; nothing else imports `UiMode → color` mapping.
- `StatusSink` protocol with `set(state: StatusState) -> None` is wired into the controller's `_render` path. Default sink is `NullStatusSink` (no-op). `GpioStatusSink` is a logging stub today — Phase 5 replaces the body with the actual `gpiozero.RGBLED` calls.

**Re-render cadence:**
- The existing snapshot-equality short-circuit in `_render` is bypassed when `state.pattern == BREATHING`, so the render tick (`RENDER_TICK_S = 0.35`) drives ~3 frames per second of the breath curve. Imperceptible on a static frame; smooth enough for the 2 s breath cycle.

**Files touched:**
- New `bridge/src/instantlink_bridge/ui/status_indicator.py` (~120 lines).
- `bridge/src/instantlink_bridge/ui/render.py` — replace `draw_status_bar` body, drop the dot, route chip colors through `state.foreground`.
- `bridge/src/instantlink_bridge/ui/controller.py` — instantiate sink, push state on each `_render`, bypass short-circuit on breathing.
- `bridge/src/instantlink_bridge/config.py` — add `ui.status_sink: "lcd" | "null" | "gpio"` config knob (default `lcd`).
- New `bridge/tests/test_status_indicator.py` — per-mode derivation + breath modulation + sink dispatch.

**Out of this phase:**
- Real GPIO wiring (Phase 5; depends on hardware BOM).
- Tinting any surface other than the LCD top bar (e.g., film row chips).

## Sequencing

Phases are mostly independent and can ship in this order. Phase 0 (boot
speed) jumps to the front of the line because it gates the headless SKU's
fundamental viability.

| Order | Phase | Why this order |
|---|---|---|
| 1 | Phase 6 (FTP-as-signal) | Small, independent, replaces the deleted LED. Ships in ~1 sprint. Its hardware-test results reframe the urgency of Phase 0 — if Sony silently auto-retries 4xx for 5-10 s, the user never perceives the boot path. Cheapest way to know whether Phase 0 still needs to push for ≤ 12 s or can settle at ≤ 18 s. |
| 2 | Phase 0 (boot speed → ≤12 s or ≤18 s) | Target adjusts based on Phase 6's retry-window measurement. Regardless, Phase 0 still saves power (idle CPU burn during boot) and improves the LCD-SKU's user-explicit-wait experience. Sprints 0–3 already shipped (~14.9 s today); Sprints 4 + 6 remain. |
| 3 | Phase 1 (LCD declutter) | Cheap user-visible win on the existing LCD-SKU; no Mac changes needed; sets up Phase 2's removals. |
| 4 | Phase 2 (Mac canonical config) | Unlocks the LCD declutter being permanent. Required for headless setup flow. |
| 5 | Phase 5 (headless SKU) | Composes Phases 0 + 2 + 6. Phase 3 is NOT a prerequisite. |
| 6 | Phase 3 (virtual display + controls) | Larger; nice-to-have for both SKUs as a debug + power-user feature; ships after the headless SKU is real. |

Phase 4 was originally a status LED service; deleted (see above) and
replaced by Phase 6's FTP-reply-code approach. The reordering reflects the
camera-power-source model: the daily UX is **hot-shoe → boot → camera-side
FTP feedback**, not Mac app and not an on-device LED. The Mac app is
setup-only on headless, so its UI fidelity (Phase 3) matters less than the
boot path working at all (Phase 0).

Each phase can be reviewed and shipped independently — no monolithic
"v2 release."

## Open questions / risks

- **First-run hotspot SSID/password derivation**: deriving from `device_id` is reproducible but means anyone with `device_id` can guess the password. Phase 5 task: confirm whether to label-print a random password instead, accepting that we lose reproducibility (and need to handle label loss). Likely answer: random password printed on label; bridge prints it on the LCD-SKU as a recovery affordance.
- **Concurrent Mac clients**: Two Macs editing settings at the same time is allowed (last-writer-wins). Should we surface a "Bridge in use by …" advisory? Defer to v1.5.
- **Long-press combo (KEY1+KEY3) overlap**: KEY3 already long-press = PAIR. KEY1 + KEY3 held 2 s is unambiguous in the input layer but warrants UX testing. Phase 1 acceptance.
- **Headless first-run without a label**: if the user loses the sticker and has no USB cable, recovery requires re-flashing. Acceptable trade-off; mitigated by Mac app showing the printed credentials during pairing so they can be screen-shotted.
- **ACT LED conflicts**: `/sys/class/leds/ACT` is also used by the kernel for SD-card activity. We need to seize it (`trigger=none`) early; document in `bridge/HARDWARE-headless.md` and the systemd preset.
- **Sony hot-shoe power pinout & current budget**: the Multi Interface Shoe nominally provides ~7.2 V on the rear contacts, but the documented current limit is conservative (~few hundred mA). Pi Zero 2 W idle + Wi-Fi + BLE can spike well above that during scan bursts. Bench-test required before committing to this BOM; if marginal, fall back to a small in-bridge capacitor reservoir or refuse to do BLE scanning until FTP is up.
- **Hot-shoe wear on the camera**: repeated power cycling through the camera's accessory shoe is mechanically fine but electrically novel. Document expected duty cycle and confirm with Sony's accessory spec.
- **Cold-boot reconnect against a printer that's auto-slept**: if the printer has gone to sleep faster than the bridge cold-boots (the BLE scan might find nothing), we need a clear "wake the printer" affordance. For v1 the LED can fast-blink-red to indicate "printer offline" and the user power-cycles the printer; this is mechanically acceptable.
- **Wi-Fi for FTP on the headless SKU**: if `ftp.mode = "hotspot"`, the Pi has to bring up the AP from cold every camera power-on; that's ~3 s of additional startup work. If `ftp.mode = "peer"` (home Wi-Fi), the join can be slow on bad networks. Both are bounded but the user needs a sane default — recommend hotspot since it's network-independent.

## Out of scope (for this plan)

- iOS app. The Mac↔bridge channel is reusable for iOS, but UI work is significant; track separately.
- Multi-printer pairing. Already on the v1.5 list.
- Cloud sync.
- Photo editing beyond auto-fit.

## Reference: existing channel surfaces

`BridgeTransport.swift` (existing methods, do not duplicate):
`discover`, `pairingStatus`, `completePairing`, `forgetLocalAuth`, `status`,
`preflightUpdate`, `uploadUpdate`, `startUpdate`, `updateStatus`,
`updateEvents`, `markUpdateGood`, `rollbackUpdate`, `createBackup`,
`restoreBackup`.

This plan adds three: `getConfig`, `patchConfig`, `openUIStream`.

`manager/api.py` (existing routes):
`GET /v1/hello`, `GET /v1/pairing/status`, `POST /v1/pairing/complete`,
plus auth-required routes registered via `auth_required_handler`.

This plan adds three: `GET /v1/config`, `PATCH /v1/config`, `WS /v1/ui`.

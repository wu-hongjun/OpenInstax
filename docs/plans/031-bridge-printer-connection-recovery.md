# 031 — Bridge Printer Connection Recovery State Machine

Status: **Draft for review** (no implementation yet)
Author: takeover session, 2026-05-27
Hardware: Raspberry Pi Zero 2 W (BlueZ 5.79) ↔ Fujifilm Instax Square Link (`INSTAX-52006924`, `FA:AB:BC:C7:95:64`)

## 1. Why this plan exists

A live hardware session chased a chain of related printer-connection failures. Three fixes
shipped (and are deployed on the Pi as bridge `0.1.6`), each verified in isolation, but the last
hardware run surfaced a deeper failure mode that the point-fixes do not cover. Rather than stack a
fourth live hot-patch, we are stepping back to design the connection-recovery behaviour
deliberately. This document is the source of truth for that design (per `CLAUDE.md`, plans live in
`docs/plans/` and are committed alongside the code they describe).

## 2. What is already deployed (keep unless this plan says otherwise)

| Fix | Bridge ver | Commit | Verified | Keep? |
|-----|-----------|--------|----------|-------|
| Freshness gate — readiness requires a printer status confirmed within `max(30s, 3×keepalive)`; `printer_status_fresh` on `UiSnapshot`; render tick downgrades a stale `READY`. | 0.1.4 | `e59ba62` | ✅ Hardware: printer off → never "Ready"; on → "Ready" returns. | **Keep.** Orthogonal safety net; correct. |
| Stale-bond signature lowered from `notification_subscribe` (stage 6) to `characteristic_lookup` (stage 5). | 0.1.5 | `39e273f` | ✅ Unit. Hardware run hit a real stage-7 write failure (not a stage-5 false positive). | **Keep**, but re-evaluate threshold once §6 lands (see Risk R1). |
| Silent-link recovery — on `PrinterNotFound`, if BlueZ holds a *connected* link for the bonded printer, `disconnect_bluez_link()` drops it so the printer re-advertises. Per-device cooldown; no-op when nothing is connected. | 0.1.6 | `623afea` | ⚠️ Unit only (6 tests). Did **not** trigger in the failing run because the bond had already been removed (no connected device to drop). | **Keep** — it addresses a real, distinct deadlock (BlueZ auto-reconnect holds a silent link). Just not the one that bit us last. |

## 3. The failure this plan must fix

Observed sequence on a printer power-cycle (journal, 2026-05-27 ~13:3x):

```
stage=ble_connecting → service_discovery → characteristic_lookup → notification_subscribe
  → model_detecting → stage=failed  "BLE error: write failed"     # genuine stale bond (stage 7)
auto_rebond action=remove_bond → done=remove_bond                 # correct: stale bond removed
stage=ble_connecting → service_discovery → ble_connecting → failed
  "connect failed after service discovery retry"                  # re-pair attempt failed
stage=failed → "printer not found" (repeats forever)              # printer no longer discoverable
```

Post-mortem facts gathered live:
- After the rebond, `bluetoothctl info FA:AB:BC:C7:95:64` returns **nothing** — the device is gone
  from the BlueZ object table (removed by the rebond and never re-added).
- A **sustained 10 s `bluetoothctl scan on` sees no `INSTAX` advertisement at all.** The printer is
  **not advertising**. It stayed non-advertising for >100 s — well past any normal BLE supervision
  timeout — so it is durably wedged, not mid-retry.
- The printer *was* advertising and connectable at power-on (the Pi connected far enough to reach
  stage 7 before the write failed), so the wedge is a *consequence of our recovery*, not the
  power-on state.

**Root-cause statement (high confidence):** removing the BlueZ bond mid-session, after a connect
that reached late GATT stages, leaves the **printer** holding a half-open / "ghost" link on its
side. The Instax stops advertising while it believes it has an active central, so the bridge can
never rediscover it. Only a printer power-cycle (or a much longer printer-side timeout) clears it.
The current `remove_bluez_bond` path (`bluetoothctl remove <addr>`) does not guarantee the printer
observes a clean link-layer disconnect before the device object is destroyed.

This is **distinct** from the two deadlocks already handled:
- Not the freshness/stale-display bug (0.1.4).
- Not the BlueZ-holds-a-silent-connected-link deadlock (0.1.6) — here the device is *removed*, so
  there is no connected object to drop.

## 4. Hypotheses for the wedge (validate before committing to a fix)

| ID | Hypothesis | How to confirm | Implication if true |
|----|-----------|-----------------|---------------------|
| H-A | `bluetoothctl remove` destroys the device object without a clean LL `Disconnect`, so the printer never sees the teardown and holds the link. | Instrument: before `remove`, log `Connected`; issue explicit `disconnect`, poll `Connected=no`, *then* `remove`; observe whether the printer re-advertises. | Fix = clean disconnect + settle before remove. |
| H-B | The btleplug-side `device.disconnect()` on the status-fetch error path completes locally but BlueZ/controller does not emit the LL disconnect (e.g. because the bond removal races it). | Add LL-level tracing (`btmon`) during the rebond window. | Fix = serialize disconnect → confirm → remove; add settle delay. |
| H-C | The Instax firmware genuinely wedges for a long, fixed interval after an interrupted pairing, regardless of clean disconnect. | After a *clean* disconnect (H-A fix) the printer still won't advertise for N seconds. | Bond removal is too costly; prefer reconnect-without-removal (see §6, Option 2). |
| H-D | Removing the bond is unnecessary: a plain reconnect (without `remove`) re-pairs via the `NoInputNoOutput` agent because the printer already cleared *its* key on power-cycle. | A/B: on the stale-bond signature, try reconnect-only first; only remove the bond if reconnect-only fails K times. | Auto-rebond should be a last resort, not first response. |

`btmon` capture during one power-cycle is the single highest-value diagnostic and should be step 1
of implementation.

## 5. Design goals / invariants

1. **Never wedge the printer.** Any recovery action must leave the printer either connected or
   freely advertising — never holding a ghost link.
2. **Always converge.** From any failed state, the bridge must return to "searching → connected"
   without user intervention (no "hold K3", no power-cycle) within a bounded time.
3. **Bond removal is a last resort**, gated and rate-limited, because it is the most disruptive
   action and the suspected wedge trigger.
4. **One owner of the BLE link.** The FFI/btleplug session and BlueZ auto-reconnect must not fight
   over the single Instax connection slot. Recovery decisions live in one place (the controller),
   driven by typed failure signals from the status provider.
5. **Observable.** Every recovery transition emits a single structured log line; the LCD shows an
   honest, specific state (not a generic spinner).

## 6. Proposed recovery state machine (to be refined after §4 validation)

Failure signals already available as typed fields on `PrinterStatusUnavailableError`:
`stale_bond_suspected` (connect reached ≥ stage 5 then BLE-failed) and `printer_not_found`
(advertisement scan saw nothing). We add the wedge dimension.

```
        ┌────────────┐  status ok
        │  CONNECTED  │◀───────────────────────────┐
        └─────┬───────┘                            │
   status fail │                                   │ status ok
        ▼      ▼                                    │
   ┌──────────────┐  not_found (& was advertising)  │
   │  SEARCHING   │─────────────┐                   │
   └─────┬────────┘             │                   │
 stale_bond_suspected           │ not_found persists│
        ▼                       ▼                   │
 ┌───────────────┐      ┌────────────────┐          │
 │ RECONNECT_ONLY│      │ SILENT_LINK_RX │──drop────┘
 │ (no remove,   │      │ (BlueZ holds   │  connected link
 │  K attempts)  │      │  connected)    │
 └─────┬─────────┘      └────────────────┘
   still failing after K
        ▼
 ┌──────────────────────────┐
 │ REBOND (last resort):     │
 │ 1. clean disconnect       │
 │ 2. confirm Connected=no   │
 │ 3. settle delay           │
 │ 4. bluetoothctl remove    │
 │ 5. confirm re-advertising │  ← if not re-advertising within T, surface "Restart printer"
 └──────────────────────────┘
```

Key changes vs. today:
- **RECONNECT_ONLY before REBOND** (tests H-D): on the stale-bond signature, first retry a plain
  reconnect K times (the printer cleared its own key on power-cycle, so the agent may re-pair
  without us removing anything). Only escalate to REBOND if reconnect-only keeps failing.
- **REBOND becomes a guarded sequence** (tests H-A/H-B): clean disconnect → confirm `Connected=no`
  → settle → `remove` → confirm the printer re-advertises. If it does not re-advertise within `T`,
  stop looping and show a specific, honest recovery prompt instead of silent "Finding Printer".
- **Wedge detection / honest UI:** if the printer is neither connected nor advertising for `> T`
  after a REBOND, the LCD must say something actionable (e.g. "Restart printer") rather than an
  endless "Finding Printer". This directly addresses the trust complaint.

## 7. Implementation phases

- **Phase 0 — Diagnose (no behaviour change).** Add `btmon`/structured tracing around the rebond
  window; run one power-cycle; confirm which of H-A…H-D holds. Land findings in this doc.
- **Phase 1 — Guarded REBOND.** Clean disconnect + confirm + settle + remove + re-advertise check.
  Unit tests for the sequence ordering; hardware test: power-cycle → must not wedge.
- **Phase 2 — RECONNECT_ONLY escalation.** Try reconnect K times before any bond removal. Tune K /
  thresholds. Re-evaluate the stage-5 stale-bond threshold (Risk R1) once reconnect-first exists.
- **Phase 3 — Wedge UI.** Bounded recovery → honest "Restart printer" copy; never loop silently.
- **Phase 4 — Durable core fix (carry-over from the 0.1.6 follow-up).** Make the core adopt an
  already-connected/known peripheral when the advertisement scan misses it (`adapter.peripherals()`
  rather than advertisement-only `transport::scan`). Removes the silent-link mitigation's reliance
  on a Python-side disconnect. Requires cross-compiled `.so` redeploy (cargo-zigbuild).

## 8. Validation (hardware, per phase)

For each phase, the acceptance test is a **printer power-cycle while the bridge is running**:
1. Printer off → LCD leaves "Ready", shows searching (freshness gate — already passing).
2. Printer on → bridge connects and shows status within a bounded time **without** the printer
   wedging (no sustained non-advertising state; `btmon` shows a clean disconnect on any teardown).
3. Repeat 5× consecutively with no manual intervention (no `bluetoothctl` by hand, no power-cycle to
   un-stick). This is the bar that the current build fails and that the freshness gate alone cannot
   satisfy.
Battery/film status must keep flowing on the 10 s keepalive throughout the connected state.

## 9. Risks

- **R1 — stage-5 stale-bond threshold may now over-trigger** once reconnect-first exists; a stage-5
  characteristic-lookup miss can be transient. Re-evaluate after Phase 2; consider requiring 2
  consecutive signatures or reserving REBOND for stage ≥ 6 again once RECONNECT_ONLY absorbs the
  transient cases.
- **R2 — single connection slot.** BlueZ auto-reconnect of the bonded device competes with the FFI
  session for the Instax's one slot. Phase 4 (adopt the existing connection) is the principled fix;
  until then the silent-link mitigation (0.1.6) papers over it.
- **R3 — `Trusted: no`.** The bonded device shows `Trusted: no`. `CLAUDE.md` warns bonded printers
  should be `Trusted=true` or reconnect-after-reboot can fail. Decide in Phase 1 whether to set
  trust on pair (and whether that *worsens* R2 by making BlueZ auto-reconnect more aggressively).
- **R4 — cross-compile cadence.** Phases 1–3 are Python-only (fast `--deps OFFLINE_DEPS` deploy).
  Phase 4 needs an `.so` rebuild; budget for slower hardware iteration.

## 10. Out of scope

- The freshness gate, stage-5 signature, and silent-link mitigation already shipped (§2) — kept.
- macOS app behaviour (CoreBluetooth manages bonding itself; the BlueZ bond dance is Pi-specific).
  We borrow its UX contract (always searching, robust reconnect) but not its bonding mechanism.

## 11. Immediate operator note

The printer is currently wedged (not advertising) from the failing run. **Power-cycle the printer**
to clear the ghost link and restore a clean advertising state before any further testing.

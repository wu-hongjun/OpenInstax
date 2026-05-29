# FTP Upload Modes

InstantLink Bridge v1 FTP receive is hotspot-first. Any FTP-capable sender can use the bridge:

- `Bridge Wi-Fi`: primary portable/giftable mode. The sender joins the bridge-created Wi-Fi AP.
- `Same Wi-Fi adv`: advanced mode for an existing same-Wi-Fi network.

The USB gadget network at `192.168.7.1` is retained for admin, SSH, and diagnostics only. Direct
Sony USB-LAN from the camera to the Pi gadget is unsupported for v1 after the Mac-proven
cable/camera retest: the cable and Pi gadget enumerated on macOS, but the Sony a7C II did not attach
to any tested Pi USB Ethernet gadget personality.

## Mode 1: Bridge Wi-Fi FTP

- Pi interface: `wlan0` in AP mode
- Pi SSID: `InstantLink-XXXX`, where `XXXXXXXX` is the bridge's device suffix
- Pi Wi-Fi password: 8-digit numeric PIN in `/etc/InstantLinkBridge/hotspot.psk`
- Pi FTP address: `192.168.8.1`
- Wi-Fi radio shape: 2.4 GHz channel 6, WPA2-PSK/RSN, CCMP/AES only
- Sender connection method: Wi-Fi to the bridge SSID

This is the giftable/portable wireless mode. The sender joins the bridge's own Wi-Fi network,
`InstantLink-XXXX` by default, and uploads to `192.168.8.1`. No existing Wi-Fi password is needed for
the bridge workflow.

Configure and activate on the Pi:

```bash
scripts/wifi-mode.sh hotspot
```

Or from the LCD:

```text
KEY1 -> Upload FTP -> FTP mode -> Bridge Wi-Fi -> KEY1
```

The upload setup values are visible together on `Settings -> Upload FTP`: `Bridge Wi-Fi`,
`Wi-Fi PIN`, `FTP host`, `FTP user`, and `FTP pass`. `Settings -> Network` remains a diagnostics
page for checking whether the hotspot is actually active.

If the LCD reports a Wi-Fi switch failure and the journal shows `sudo: unable to change to root
gid`, verify `/etc/systemd/system/instantlink-bridge.service` does not set `CapabilityBoundingSet=`.
That systemd sandbox strips the setgid capability that `sudo` needs before it can run the
root-owned NetworkManager helper.

The script generates `/etc/InstantLinkBridge/hotspot.psk` if an 8-digit numeric PIN is not already
present. The file is readable by the service group so the LCD can display it. To set a known SSID or
password:

```bash
printf '%s\n' 'InstantLink-1234' | sudo tee /etc/InstantLinkBridge/hotspot.ssid
INSTANTLINK_BRIDGE_HOTSPOT_PSK='12345678' \
scripts/wifi-mode.sh hotspot
```

The helper intentionally preserves the configured NetworkManager SSID instead of deriving a changing
name from the hostname.

## Advanced Mode: Same Wi-Fi FTP

- Pi interface: `wlan0` in station/client mode
- LCD label: `Same Wi-Fi adv <address>`
- Pi FTP address: the LCD `Same Wi-Fi adv` address, currently `192.168.5.149` on the live bridge
- FTP credentials: LCD `FTP user` and `FTP pass`; provisioning generates an 8-digit numeric
  password for fresh devices
- Sender connection method: Wi-Fi to the same home network

Use this only when you intentionally want the sender and bridge on an existing Wi-Fi network. It is
an advanced path because the Pi must already know the network Wi-Fi password, the sender must join
the same network, and router DHCP can change the FTP address. The sender's FTP host must be the
actual `Same Wi-Fi adv` address on the LCD. Reserve the Pi's Wi-Fi MAC in the router only after this
path works, then set `preferred_wifi_host` to that reserved address.

Configure and activate on the Pi:

```bash
scripts/wifi-mode.sh home 'Home SSID' 'home-wifi-password'
```

After an existing Wi-Fi profile exists, the LCD Settings menu can switch back to it with the saved
NetworkManager connection. The command-line equivalent is:

```bash
scripts/wifi-mode.sh home-saved
```

The LCD path is:

```text
Settings -> Upload FTP -> FTP mode -> Same Wi-Fi adv -> KEY1
```

The `FTP mode` row opens an explicit option list. It does not cycle hidden values:

- `Bridge Wi-Fi`: start the bridge-created Wi-Fi AP and accept only hotspot-subnet FTP clients. The
  bridge Wi-Fi name, PIN, FTP host, and FTP credentials are shown under `Settings -> Upload FTP`.
- `Same Wi-Fi adv`: reconnect to a saved same-network Wi-Fi profile and accept only non-USB,
  non-hotspot, non-link-local peer clients.

If legacy builds still show `Auto` or `Wired`, do not use them for v1 upload setup. `Wired` is a
diagnostic/admin USB gadget path, and `Auto` must not make USB gadget FTP count as a supported
upload path.

Optional, after the router reservation exists:

```toml
[ftp]
preferred_wifi_host = "192.168.5.7"
```

Do not point the sender at the preferred address until the LCD actually shows that address. The LCD
always shows the actual Same Wi-Fi adv address. If it differs from the preferred reservation, the
Same Wi-Fi adv line is highlighted.

## Advanced: Hotspot With Home Wi-Fi Backhaul

The Pi Zero 2 W can create a virtual AP interface while `wlan0` remains connected to a saved home
Wi-Fi network, but the current production helper does not use that path. See
[wifi-ap-sta-experiment.md](wifi-ap-sta-experiment.md) for the live-device probe.

For v1, `Bridge Wi-Fi` and `Same Wi-Fi adv` remain separate deterministic modes. A future
experimental mode can bind the bridge hotspot to `ib-ap0` and keep existing Wi-Fi on `wlan0`, but it
must validate same-channel operation before the UI presents it as reliable.

## FTP Source Gating

InstantLink Bridge binds FTP broadly so the same daemon can serve the bridge hotspot, optional peer
Wi-Fi, and USB gadget diagnostics. The selected receive mode is enforced by source IP:

| Path | Accepted FTP sources | Rejected examples |
| --- | --- | --- |
| Bridge Wi-Fi | hotspot subnet only, for example `192.168.8.0/24` | USB and existing Wi-Fi clients |
| Same Wi-Fi adv | non-USB, non-hotspot, non-link-local Wi-Fi clients | `169.254.0.0/16`, USB, and hotspot clients |
| USB gadget diagnostics | admin/diagnostic host on `192.168.7.0/24` | upload workflows |

Rejected uploads are refused when possible. If a file is already present by the time the policy is
checked, it is removed and is not queued for printing.

## Subnet Rules

Keep the transport subnets separate:

| Transport | Bridge address | Notes |
| --- | --- | --- |
| Bridge Wi-Fi | `192.168.8.1` | Owned by InstantLink Bridge AP mode |
| Same Wi-Fi adv | router-reserved, e.g. `192.168.5.7` | Owned by the shared Wi-Fi router |
| USB gadget diagnostics | `192.168.7.1` | Owned by the admin/SSH link, not a v1 upload path |

Do not use `192.168.7.2` for Wi-Fi. It belongs to the USB gadget subnet `192.168.7.0/24`, so using
it on Wi-Fi can create ambiguous routes while the admin/diagnostic link is active.

Also avoid broader Wi-Fi networks that overlap either reserved transport subnet. For example,
`192.168.5.149/22` is on `192.168.4.0/22`, which overlaps the USB gadget subnet
`192.168.7.0/24`; Same Wi-Fi adv should be treated as a subnet conflict until the Wi-Fi network or
bridge FTP subnet is moved.

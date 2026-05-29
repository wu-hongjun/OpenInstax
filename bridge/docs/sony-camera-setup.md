# Sony a7C II Camera Setup

Target camera: Sony a7C II / ILCE-7CM2, firmware 2.00 or later.

## Verify Firmware

1. Open `MENU`.
2. Go to `(Setup) -> Setup Option -> Version`.
3. Confirm camera firmware is `2.00` or later.

## Enable FTP Transfer

1. Open `MENU`.
2. Go to `(Network) -> FTP Transfer -> FTP Transfer Func.`.
3. Set `FTP Function: On`.
4. Select `Server Setting -> Server 1`.
5. Configure the bridge profile:

| Field | Value |
| --- | --- |
| Display name | `InstantLink Bridge` |
| Host name / IP address | `192.168.8.1` for Bridge Wi-Fi FTP |
| Port | `21` |
| Directory | `/` |
| Passive mode | `On` unless camera rejects it during validation |
| Secure Protocol / TLS | `Off` / no TLS |
| User name | LCD `Settings -> Camera FTP -> FTP user` |
| Password | LCD `Settings -> Camera FTP -> FTP pass` |

The bridge `Connection` setting must match the transport you want to use. For v1, use
`Bridge Wi-Fi` first. `Same Wi-Fi` is optional. Direct Sony USB-LAN to the Pi gadget is unsupported.

## Primary Bridge Wi-Fi FTP Profile

Use this for portable/giftable operation. Connect the camera to the bridge SSID
`InstantLink-XXXX`, where `XXXXXXXX` matches the bridge device suffix shown in System settings.
The WPA password is the 8-digit
`Wi-Fi PIN` shown on the LCD under `Settings -> Camera FTP`. Then create or update the Sony FTP
server profile:

| Field | Value |
| --- | --- |
| Host name / IP address | `192.168.8.1` |
| Port | `21` |
| Directory | `/` |
| Secure Protocol / TLS | `Off` / no TLS |
| User name | LCD `Settings -> Camera FTP -> FTP user` |
| Password | LCD `Settings -> Camera FTP -> FTP pass` |

Set `Cnct. Method` to Wi-Fi for this profile.
On the bridge, use `Settings -> Camera FTP -> FTP mode -> Bridge Wi-Fi -> KEY1`.

## Unsupported Direct USB-LAN

Sony documents a7C II wired LAN as requiring a commercially available USB-LAN
conversion adapter connected to the camera's USB-C port. Direct Pi USB Ethernet gadget mode is not
supported for v1. On 2026-05-22, a Mac-proven cable and Pi gadget setup enumerated successfully on
macOS, then the Sony a7C II still did not attach in `USB-LAN Connection`; every tested gadget
personality stayed at `UDC state=not attached` with no `usb0` carrier.

Do not configure the v1 camera profile for `Wired LAN (USB-LAN)` against the Pi gadget. The Pi
`192.168.7.1` USB network is for admin, SSH, and diagnostics only.

## Advanced Same Wi-Fi Profile

Use this when the camera and bridge intentionally join an existing Wi-Fi network. Create a second
Sony FTP server profile that points at the bridge's actual LCD `Same Wi-Fi adv` address. On the
current live bridge, that address is:

```text
192.168.5.149
```

Use port `21`, directory `/`, and TLS off. Enter the LCD `FTP user` and
`FTP pass` values from `Settings -> Camera FTP`. Do not use a router-reserved address such as
`192.168.5.7` until the LCD actually shows that address.
On the bridge, use `Settings -> Camera FTP -> FTP mode -> Same Wi-Fi adv -> KEY1`; Bridge
Wi-Fi mode rejects this upload source.

Do not use `192.168.7.2` for Wi-Fi while the USB diagnostic link remains `192.168.7.1/24`; that
address is inside the USB gadget subnet and can collide with the admin path.

See [wifi-ftp-modes.md](wifi-ftp-modes.md) for the full transport split.

## Map C1 for Playback Transfer

1. Open `MENU`.
2. Go to `(Setup) -> Operation Customize -> Custom Key Setting`.
3. Switch to the `Playback` tab.
4. Select `C1`.
5. Assign `FTP Trans. (This Img.)`.

Usage after setup:

1. Press playback on the camera.
2. Navigate to the desired image.
3. Press C1.
4. The selected still image uploads to the bridge and auto-prints after the preview delay unless canceled with KEY2. JPEG and HIF are supported; Sony RAW/ARW is best-effort and slower.

## Save/Load Settings Limitation

Sony `Save/Load FTP Settings` and broader setup files are same-model only. A file exported from one Sony model must not be treated as a universal provisioning payload for other camera models. For cross-camera setup, use the Transfer & Tagging Bluetooth-push path if available, or enter the FTP profile manually.

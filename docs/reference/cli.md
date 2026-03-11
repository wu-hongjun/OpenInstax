# CLI Reference

## Global Options

| Flag | Description |
|------|-------------|
| `--device <NAME>` | Target a specific printer by BLE name |
| `--json` | Structured JSON output where implemented (`scan`, `info`, `status`). The flag is global, so it also appears in `print` and `led` help even though those commands still use human-readable output. |
| `--help` | Show help |
| `--version` | Show version |

## Commands

### `instantlink scan`

Scan for nearby Instax Link printers over BLE.

```bash
instantlink scan
instantlink scan --duration 10
instantlink scan --json
```

| Option | Default | Description |
|--------|---------|-------------|
| `--duration <SECS>` | `5` | BLE scan duration in seconds |

JSON output is an array of printer names.

---

### `instantlink info`

Show battery, film, charging, model, and print-count data.

```bash
instantlink info
instantlink info --device "INSTAX-12345678"
instantlink info --duration 10
instantlink info --json
```

| Option | Default | Description |
|--------|---------|-------------|
| `--duration <SECS>` | `5` | BLE scan duration in seconds |

**JSON output:**

```json
{
  "name": "INSTAX-12345678",
  "model": "Instax Mini Link",
  "battery": 85,
  "is_charging": false,
  "film_remaining": 8,
  "print_count": 42
}
```

---

### `instantlink print <IMAGE>`

Print an image file to the connected printer.

```bash
instantlink print photo.jpg
instantlink print photo.png --quality 90 --fit contain
instantlink print photo.jpg --color-mode natural
```

| Option | Default | Description |
|--------|---------|-------------|
| `--quality <1-100>` | `97` | JPEG compression quality |
| `--fit <mode>` | `crop` | `crop`, `contain`, or `stretch` |
| `--color-mode <mode>` | `rich` | `rich` or `natural` |

**Fit modes:**

- `crop` resizes to fill, cropping edges when needed
- `contain` preserves the full image and adds white bars
- `stretch` warps to exact printer dimensions

The printer model determines the final pixel size and maximum JPEG size. Current limits are Mini `105KB`, Mini Link 3 `55KB`, Square `105KB`, and Wide `225KB`.

---

### `instantlink led set <COLOR>`

Set the printer LED color and pattern.

```bash
instantlink led set "#FF0000"
instantlink led set "#00FF00" --pattern breathe
```

| Option | Default | Description |
|--------|---------|-------------|
| `--pattern <type>` | `solid` | `solid`, `blink`, or `breathe` |

Color format: `#RRGGBB` or `RRGGBB`.

---

### `instantlink led off`

Turn off the printer LED.

```bash
instantlink led off
```

---

### `instantlink status`

Combined connectivity check and printer status.

```bash
instantlink status
instantlink status --json
```

**JSON output when connected:**

```json
{
  "connected": true,
  "name": "INSTAX-12345678",
  "model": "Instax Mini Link",
  "battery": 85,
  "is_charging": false,
  "film_remaining": 8,
  "print_count": 42
}
```

**JSON output when disconnected:**

```json
{
  "connected": false
}
```

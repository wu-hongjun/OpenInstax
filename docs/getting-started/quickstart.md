# Quick Start

## 1. Scan for Printers

Turn on your Instax Link printer and scan:

```bash
instantlink scan
```

Expected output:

```
Found 1 printer(s):
  INSTAX-12345678
```

## 2. Check Printer Status

```bash
instantlink status
```

```
Connected:  yes
Printer:    INSTAX-12345678
Model:      Instax Mini Link
Battery:    85%
Film:       8 remaining
Prints:     42
```

## 3. Print an Image

```bash
instantlink print photo.jpg
```

The image is automatically resized to fit your printer model. Use `--fit` to control how:

```bash
# Crop to fill (default) — may cut edges
instantlink print photo.jpg --fit crop

# Contain within bounds — adds white bars
instantlink print photo.jpg --fit contain

# Stretch to exact dimensions
instantlink print photo.jpg --fit stretch
```

### JPEG Quality

Control output quality (affects file size):

```bash
instantlink print photo.jpg --quality 90
```

Quality automatically reduces if the image exceeds 105KB.

## 4. LED Control

```bash
# Set LED to a color
instantlink led set "#FF6600" --pattern breathe

# Turn LED off
instantlink led off
```

## 5. JSON Output

All commands support `--json` for machine-readable output:

```bash
instantlink status --json
```

```json
{
  "connected": true,
  "name": "INSTAX-12345678",
  "model": "Instax Mini Link",
  "battery": 85,
  "film_remaining": 8,
  "print_count": 42
}
```

## Multiple Printers

Target a specific printer by name:

```bash
instantlink print photo.jpg --device "INSTAX-12345678"
```

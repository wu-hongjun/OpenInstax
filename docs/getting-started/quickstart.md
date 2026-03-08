# Quick Start

## 1. Scan for Printers

Turn on your Instax Link printer and scan:

```bash
openinstax scan
```

Expected output:

```
Found 1 printer(s):
  INSTAX-12345678
```

## 2. Check Printer Status

```bash
openinstax status
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
openinstax print photo.jpg
```

The image is automatically resized to fit your printer model. Use `--fit` to control how:

```bash
# Crop to fill (default) — may cut edges
openinstax print photo.jpg --fit crop

# Contain within bounds — adds white bars
openinstax print photo.jpg --fit contain

# Stretch to exact dimensions
openinstax print photo.jpg --fit stretch
```

### JPEG Quality

Control output quality (affects file size):

```bash
openinstax print photo.jpg --quality 90
```

Quality automatically reduces if the image exceeds 105KB.

## 4. LED Control

```bash
# Set LED to a color
openinstax led set "#FF6600" --pattern breathe

# Turn LED off
openinstax led off
```

## 5. JSON Output

All commands support `--json` for machine-readable output:

```bash
openinstax status --json
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
openinstax print photo.jpg --device "INSTAX-12345678"
```

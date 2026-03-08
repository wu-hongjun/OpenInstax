# BLE Protocol Reference

This documents the Instax Link BLE protocol as reverse-engineered by the open-source community.

## BLE Service

| Item | UUID |
|------|------|
| Service | `70954782-2d83-473d-9e5f-81e1d02d5273` |
| Write Characteristic | `70954783-2d83-473d-9e5f-81e1d02d5273` |
| Notify Characteristic | `70954784-2d83-473d-9e5f-81e1d02d5273` |

## Packet Format

All communication uses the same packet structure:

```
[0x41 0x62] [length:2B] [opcode:2B] [payload...] [checksum:1B]
```

| Field | Size | Description |
|-------|------|-------------|
| Header | 2 bytes | Always `0x41 0x62` |
| Length | 2 bytes | Big-endian, covers opcode + payload + checksum |
| Opcode | 2 bytes | Command identifier (big-endian) |
| Payload | variable | Command-specific data |
| Checksum | 1 byte | `(255 - (sum_of_preceding_bytes & 255)) & 255` |

## Checksum

The checksum is computed over all bytes preceding it (header + length + opcode + payload):

```rust
fn checksum(data: &[u8]) -> u8 {
    let sum: u32 = data.iter().map(|&b| b as u32).sum();
    ((255 - (sum & 255)) & 255) as u8
}
```

## MTU Fragmentation

BLE packets larger than 182 bytes are split into sub-packets for transmission. The receiver reassembles them using the length field.

## Opcodes

### Query Commands

| Opcode | Name | Payload | Response |
|--------|------|---------|----------|
| `0x1000` | Device Info | (none) | Device info blob |
| `0x1001` | Support Function Info | (none) | Firmware/capability blob |
| `0x1002` | Image Support Info | (none) | Width(2B), Height(2B), ... |
| `0x1003` | Battery Status | (none) | Level(1B, 0-100) |
| `0x1004` | History Info | (none) | Print count(4B, big-endian) |
| `0x1006` | Remaining Info | (none) | Film remaining(1B) |

### Image Transfer

| Opcode | Name | Payload | Response |
|--------|------|---------|----------|
| `0x2000` | Download Start | Image size(4B, big-endian) | ACK status(1B) |
| `0x2001` | Data | Offset(4B) + chunk data | ACK status(1B) |
| `0x2002` | Download End | (none) | ACK status(1B) |
| `0x2003` | Download Cancel | (none) | ACK status(1B) |
| `0x4000` | Print Image | (none) | Print status(1B) |

### LED & Settings

| Opcode | Name | Payload |
|--------|------|---------|
| `0x3001` | LED Pattern Settings | Pattern(1B), R(1B), G(1B), B(1B) |
| `0x3010` | Print Mode Settings | Mode(1B) |

### Events

| Opcode | Name | Description |
|--------|------|-------------|
| `0x1005` | Shutter Button | Fired when physical button pressed |
| `0x3004` | XYZ Axis Info | Accelerometer data |

## Print Flow

The complete print sequence:

1. **Connect** to the printer via BLE
2. **Discover services** and subscribe to notifications on the notify characteristic
3. **Query `IMAGE_SUPPORT_INFO`** (`0x1002`) to auto-detect the printer model from response dimensions
4. **Prepare the image**: resize to model dimensions, JPEG compress, split into chunks
5. **Send `DOWNLOAD_START`** (`0x2000`) with the JPEG data size; wait for ACK
6. **Send `DATA` chunks** (`0x2001`) with offset and chunk data; wait for ACK after each chunk
7. **Send `DOWNLOAD_END`** (`0x2002`); wait for ACK
8. **Send `PRINT_IMAGE`** (`0x4000`); wait for print status response
9. **Disconnect**

## Model-Specific Parameters

| Model | Width | Height | Chunk Size | Max Image Size |
|-------|-------|--------|------------|----------------|
| Mini Link | 600 | 800 | 900 B | ~105 KB |
| Square Link | 800 | 800 | 1808 B | ~105 KB |
| Wide Link | 1260 | 840 | 900 B | ~105 KB |

Model is auto-detected from the `IMAGE_SUPPORT_INFO` response dimensions.

## References

- [javl/InstaxBLE](https://github.com/javl/InstaxBLE) — Python implementation
- [linssenste/instax-link-web](https://github.com/linssenste/instax-link-web) — Web Bluetooth implementation

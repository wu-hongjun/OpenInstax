//! Instax BLE command opcodes and Command/Response enums.
//!
//! All opcodes are derived from reverse-engineering of the Instax Link protocol
//! by the open-source community (javl/InstaxBLE, linssenste/instax-link-web).

use crate::protocol;

// ── Opcode constants ────────────────────────────────────────────────────────

/// Device information query.
pub const OP_DEVICE_INFO_SERVICE: u16 = 0x1000;
/// Firmware version query.
pub const OP_SUPPORT_FUNCTION_INFO: u16 = 0x1001;
/// Image support info (used for model auto-detection).
pub const OP_IMAGE_SUPPORT_INFO: u16 = 0x1002;
/// Battery status query.
pub const OP_BATTERY_STATUS_INFO: u16 = 0x1003;
/// Print count query.
pub const OP_HISTORY_INFO: u16 = 0x1004;
/// Shutter button event.
pub const OP_SHUTTER_BUTTON: u16 = 0x1005;

/// Begin image download.
pub const OP_DOWNLOAD_START: u16 = 0x2000;
/// Image data chunk.
pub const OP_DATA: u16 = 0x2001;
/// End image download.
pub const OP_DOWNLOAD_END: u16 = 0x2002;
/// Cancel image download.
pub const OP_DOWNLOAD_CANCEL: u16 = 0x2003;

/// Trigger print.
pub const OP_PRINT_IMAGE: u16 = 0x4000;

/// Set LED color.
pub const OP_LED_PATTERN_SETTINGS: u16 = 0x3001;

/// Accelerometer data.
pub const OP_XYZ_AXIS_INFO: u16 = 0x3004;

/// Print mode settings (normal, high speed, etc.).
pub const OP_PRINT_MODE_SETTINGS: u16 = 0x3010;

/// Film remaining info.
pub const OP_REMAINING_INFO: u16 = 0x1006;

/// Commands that can be sent to the printer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Command {
    /// Query device info.
    DeviceInfo,
    /// Query firmware / function support.
    SupportFunctionInfo,
    /// Query image support info (for model detection).
    ImageSupportInfo,
    /// Query battery status.
    BatteryStatus,
    /// Query print history / count.
    HistoryInfo,
    /// Query remaining film.
    RemainingInfo,
    /// Start image download with image size.
    DownloadStart { image_size: u32 },
    /// Send an image data chunk.
    Data { offset: u32, data: Vec<u8> },
    /// Signal end of image download.
    DownloadEnd,
    /// Cancel an in-progress download.
    DownloadCancel,
    /// Trigger the printer to print the downloaded image.
    PrintImage,
    /// Set LED color and pattern.
    LedPatternSettings {
        red: u8,
        green: u8,
        blue: u8,
        pattern: u8,
    },
    /// Set print mode.
    PrintModeSettings { mode: u8 },
}

impl Command {
    /// Encode this command into a complete protocol packet.
    pub fn encode(&self) -> Vec<u8> {
        match self {
            Command::DeviceInfo => protocol::build_packet(OP_DEVICE_INFO_SERVICE, &[]),
            Command::SupportFunctionInfo => protocol::build_packet(OP_SUPPORT_FUNCTION_INFO, &[]),
            Command::ImageSupportInfo => protocol::build_packet(OP_IMAGE_SUPPORT_INFO, &[]),
            Command::BatteryStatus => protocol::build_packet(OP_BATTERY_STATUS_INFO, &[]),
            Command::HistoryInfo => protocol::build_packet(OP_HISTORY_INFO, &[]),
            Command::RemainingInfo => protocol::build_packet(OP_REMAINING_INFO, &[]),
            Command::DownloadStart { image_size } => {
                protocol::build_packet(OP_DOWNLOAD_START, &image_size.to_be_bytes())
            }
            Command::Data { offset, data } => {
                let mut payload = offset.to_be_bytes().to_vec();
                payload.extend_from_slice(data);
                protocol::build_packet(OP_DATA, &payload)
            }
            Command::DownloadEnd => protocol::build_packet(OP_DOWNLOAD_END, &[]),
            Command::DownloadCancel => protocol::build_packet(OP_DOWNLOAD_CANCEL, &[]),
            Command::PrintImage => protocol::build_packet(OP_PRINT_IMAGE, &[]),
            Command::LedPatternSettings {
                red,
                green,
                blue,
                pattern,
            } => protocol::build_packet(OP_LED_PATTERN_SETTINGS, &[*pattern, *red, *green, *blue]),
            Command::PrintModeSettings { mode } => {
                protocol::build_packet(OP_PRINT_MODE_SETTINGS, &[*mode])
            }
        }
    }
}

/// Responses received from the printer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Response {
    /// Device info response.
    DeviceInfo { payload: Vec<u8> },
    /// Function support info.
    SupportFunctionInfo { payload: Vec<u8> },
    /// Image support info (width, height, chunk size).
    ImageSupportInfo {
        width: u16,
        height: u16,
        payload: Vec<u8>,
    },
    /// Battery status.
    BatteryStatus { level: u8 },
    /// Print history / count.
    HistoryInfo { count: u32 },
    /// Remaining film count.
    RemainingInfo { remaining: u8 },
    /// Download ACK (ready for next chunk or operation).
    DownloadAck { status: u8 },
    /// Print started / completed notification.
    PrintStatus { status: u8 },
    /// LED settings acknowledged.
    LedAck,
    /// Shutter button pressed.
    ShutterButton,
    /// Unknown / unrecognized response.
    Unknown { opcode: u16, payload: Vec<u8> },
}

impl Response {
    /// Decode a response from a parsed protocol packet.
    pub fn decode(packet: &protocol::Packet) -> Self {
        match packet.opcode {
            OP_DEVICE_INFO_SERVICE => Response::DeviceInfo {
                payload: packet.payload.clone(),
            },
            OP_SUPPORT_FUNCTION_INFO => Response::SupportFunctionInfo {
                payload: packet.payload.clone(),
            },
            OP_IMAGE_SUPPORT_INFO => {
                let width = if packet.payload.len() >= 2 {
                    u16::from_be_bytes([packet.payload[0], packet.payload[1]])
                } else {
                    0
                };
                let height = if packet.payload.len() >= 4 {
                    u16::from_be_bytes([packet.payload[2], packet.payload[3]])
                } else {
                    0
                };
                Response::ImageSupportInfo {
                    width,
                    height,
                    payload: packet.payload.clone(),
                }
            }
            OP_BATTERY_STATUS_INFO => {
                let level = packet.payload.first().copied().unwrap_or(0);
                Response::BatteryStatus { level }
            }
            OP_HISTORY_INFO => {
                let count = if packet.payload.len() >= 4 {
                    u32::from_be_bytes([
                        packet.payload[0],
                        packet.payload[1],
                        packet.payload[2],
                        packet.payload[3],
                    ])
                } else {
                    0
                };
                Response::HistoryInfo { count }
            }
            OP_REMAINING_INFO => {
                let remaining = packet.payload.first().copied().unwrap_or(0);
                Response::RemainingInfo { remaining }
            }
            OP_DOWNLOAD_START | OP_DATA | OP_DOWNLOAD_END | OP_DOWNLOAD_CANCEL => {
                let status = packet.payload.first().copied().unwrap_or(0);
                Response::DownloadAck { status }
            }
            OP_PRINT_IMAGE => {
                let status = packet.payload.first().copied().unwrap_or(0);
                Response::PrintStatus { status }
            }
            OP_LED_PATTERN_SETTINGS => Response::LedAck,
            OP_SHUTTER_BUTTON => Response::ShutterButton,
            _ => Response::Unknown {
                opcode: packet.opcode,
                payload: packet.payload.clone(),
            },
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encode_device_info() {
        let pkt = Command::DeviceInfo.encode();
        let parsed = protocol::parse_packet(&pkt).unwrap();
        assert_eq!(parsed.opcode, OP_DEVICE_INFO_SERVICE);
        assert!(parsed.payload.is_empty());
    }

    #[test]
    fn encode_battery_status() {
        let pkt = Command::BatteryStatus.encode();
        let parsed = protocol::parse_packet(&pkt).unwrap();
        assert_eq!(parsed.opcode, OP_BATTERY_STATUS_INFO);
    }

    #[test]
    fn encode_download_start() {
        let cmd = Command::DownloadStart { image_size: 50000 };
        let pkt = cmd.encode();
        let parsed = protocol::parse_packet(&pkt).unwrap();
        assert_eq!(parsed.opcode, OP_DOWNLOAD_START);
        let size = u32::from_be_bytes([
            parsed.payload[0],
            parsed.payload[1],
            parsed.payload[2],
            parsed.payload[3],
        ]);
        assert_eq!(size, 50000);
    }

    #[test]
    fn encode_data_chunk() {
        let data = vec![0xAA, 0xBB, 0xCC];
        let cmd = Command::Data {
            offset: 100,
            data: data.clone(),
        };
        let pkt = cmd.encode();
        let parsed = protocol::parse_packet(&pkt).unwrap();
        assert_eq!(parsed.opcode, OP_DATA);
        let offset = u32::from_be_bytes([
            parsed.payload[0],
            parsed.payload[1],
            parsed.payload[2],
            parsed.payload[3],
        ]);
        assert_eq!(offset, 100);
        assert_eq!(&parsed.payload[4..], &[0xAA, 0xBB, 0xCC]);
    }

    #[test]
    fn encode_download_end() {
        let pkt = Command::DownloadEnd.encode();
        let parsed = protocol::parse_packet(&pkt).unwrap();
        assert_eq!(parsed.opcode, OP_DOWNLOAD_END);
    }

    #[test]
    fn encode_print_image() {
        let pkt = Command::PrintImage.encode();
        let parsed = protocol::parse_packet(&pkt).unwrap();
        assert_eq!(parsed.opcode, OP_PRINT_IMAGE);
    }

    #[test]
    fn encode_led_pattern() {
        let cmd = Command::LedPatternSettings {
            red: 255,
            green: 128,
            blue: 0,
            pattern: 1,
        };
        let pkt = cmd.encode();
        let parsed = protocol::parse_packet(&pkt).unwrap();
        assert_eq!(parsed.opcode, OP_LED_PATTERN_SETTINGS);
        assert_eq!(parsed.payload, vec![1, 255, 128, 0]);
    }

    #[test]
    fn decode_battery_status() {
        let packet = protocol::Packet {
            opcode: OP_BATTERY_STATUS_INFO,
            payload: vec![85],
        };
        match Response::decode(&packet) {
            Response::BatteryStatus { level } => assert_eq!(level, 85),
            other => panic!("expected BatteryStatus, got {other:?}"),
        }
    }

    #[test]
    fn decode_image_support_info() {
        let mut payload = Vec::new();
        payload.extend_from_slice(&600u16.to_be_bytes());
        payload.extend_from_slice(&800u16.to_be_bytes());
        let packet = protocol::Packet {
            opcode: OP_IMAGE_SUPPORT_INFO,
            payload: payload.clone(),
        };
        match Response::decode(&packet) {
            Response::ImageSupportInfo {
                width,
                height,
                payload: _,
            } => {
                assert_eq!(width, 600);
                assert_eq!(height, 800);
            }
            other => panic!("expected ImageSupportInfo, got {other:?}"),
        }
    }

    #[test]
    fn decode_history_info() {
        let packet = protocol::Packet {
            opcode: OP_HISTORY_INFO,
            payload: 42u32.to_be_bytes().to_vec(),
        };
        match Response::decode(&packet) {
            Response::HistoryInfo { count } => assert_eq!(count, 42),
            other => panic!("expected HistoryInfo, got {other:?}"),
        }
    }

    #[test]
    fn decode_download_ack() {
        let packet = protocol::Packet {
            opcode: OP_DOWNLOAD_START,
            payload: vec![0],
        };
        match Response::decode(&packet) {
            Response::DownloadAck { status } => assert_eq!(status, 0),
            other => panic!("expected DownloadAck, got {other:?}"),
        }
    }

    #[test]
    fn decode_remaining_info() {
        let packet = protocol::Packet {
            opcode: OP_REMAINING_INFO,
            payload: vec![8],
        };
        match Response::decode(&packet) {
            Response::RemainingInfo { remaining } => assert_eq!(remaining, 8),
            other => panic!("expected RemainingInfo, got {other:?}"),
        }
    }

    #[test]
    fn decode_unknown_opcode() {
        let packet = protocol::Packet {
            opcode: 0xFFFF,
            payload: vec![1, 2, 3],
        };
        match Response::decode(&packet) {
            Response::Unknown { opcode, payload } => {
                assert_eq!(opcode, 0xFFFF);
                assert_eq!(payload, vec![1, 2, 3]);
            }
            other => panic!("expected Unknown, got {other:?}"),
        }
    }

    #[test]
    fn encode_decode_roundtrip() {
        // Encode a command, parse the packet, decode the response
        let cmd = Command::BatteryStatus;
        let raw = cmd.encode();
        let packet = protocol::parse_packet(&raw).unwrap();

        // Simulate a response with the same opcode
        let response_packet = protocol::Packet {
            opcode: packet.opcode,
            payload: vec![75],
        };
        match Response::decode(&response_packet) {
            Response::BatteryStatus { level } => assert_eq!(level, 75),
            other => panic!("expected BatteryStatus, got {other:?}"),
        }
    }
}

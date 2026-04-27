//! Instax BLE command opcodes and Command/Response enums.
//!
//! Opcodes derived from javl/InstaxBLE reference implementation.

use crate::error::Result;
use crate::protocol;

// ── Opcode constants (EventType values from reference) ────────────────────────

/// Device information query.
pub const OP_DEVICE_INFO_SERVICE: u16 = 0x0001;
/// Query printer info (image support, battery, film, history) — InfoType in payload.
pub const OP_SUPPORT_FUNCTION_INFO: u16 = 0x0002;

/// Begin image download.
pub const OP_DOWNLOAD_START: u16 = 0x1000;
/// Image data chunk.
pub const OP_DATA: u16 = 0x1001;
/// End image download.
pub const OP_DOWNLOAD_END: u16 = 0x1002;
/// Cancel image download.
pub const OP_DOWNLOAD_CANCEL: u16 = 0x1003;

/// Trigger print.
pub const OP_PRINT_IMAGE: u16 = 0x1080;

/// Accelerometer data.
pub const OP_XYZ_AXIS_INFO: u16 = 0x3000;
/// Set LED color.
pub const OP_LED_PATTERN_SETTINGS: u16 = 0x3001;

/// Additional printer info / print mode settings.
pub const OP_ADDITIONAL_PRINTER_INFO: u16 = 0x3010;

/// Power off the printer.
pub const OP_SHUT_DOWN: u16 = 0x0100;
/// Reset the printer.
pub const OP_RESET: u16 = 0x0101;

// ── InfoType constants (payload byte for SUPPORT_FUNCTION_INFO) ───────────────

/// Image support info (for model detection): width, height, chunk size.
pub const INFO_IMAGE_SUPPORT: u8 = 0;
/// Battery status.
pub const INFO_BATTERY: u8 = 1;
/// Printer function info (film remaining, charging state).
pub const INFO_PRINTER_FUNCTION: u8 = 2;
/// Print history / count.
pub const INFO_PRINT_HISTORY: u8 = 3;

/// Commands that can be sent to the printer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Command {
    /// Query device info.
    DeviceInfo,
    /// Query image support info (for model detection).
    ImageSupportInfo,
    /// Query battery status.
    BatteryStatus,
    /// Query printer function info (film remaining).
    PrinterFunctionInfo,
    /// Query print history / count.
    HistoryInfo,
    /// Start image download with image size.
    /// `print_option` controls Rich (0x00) vs Natural (0x01) mode (unconfirmed).
    DownloadStart { image_size: u32, print_option: u8 },
    /// Send an image data chunk (index = sequential chunk number 0, 1, 2…).
    Data { index: u32, data: Vec<u8> },
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
    /// Power off the printer.
    Shutdown,
    /// Reset the printer.
    Reset,
}

impl Command {
    /// Encode this command into a complete protocol packet.
    ///
    /// # Errors
    ///
    /// Returns an error if the payload exceeds the protocol maximum (see
    /// [`protocol::MAX_PACKET_PAYLOAD`]). In practice all built-in commands
    /// produce small payloads, so this only fires for [`Command::Data`] with
    /// a chunk larger than the limit.
    pub fn encode(&self) -> Result<Vec<u8>> {
        match self {
            Command::DeviceInfo => protocol::build_packet(OP_DEVICE_INFO_SERVICE, &[]),
            // Info queries use SUPPORT_FUNCTION_INFO opcode with InfoType as payload
            Command::ImageSupportInfo => {
                protocol::build_packet(OP_SUPPORT_FUNCTION_INFO, &[INFO_IMAGE_SUPPORT])
            }
            Command::BatteryStatus => {
                protocol::build_packet(OP_SUPPORT_FUNCTION_INFO, &[INFO_BATTERY])
            }
            Command::PrinterFunctionInfo => {
                protocol::build_packet(OP_SUPPORT_FUNCTION_INFO, &[INFO_PRINTER_FUNCTION])
            }
            Command::HistoryInfo => {
                protocol::build_packet(OP_SUPPORT_FUNCTION_INFO, &[INFO_PRINT_HISTORY])
            }
            Command::DownloadStart {
                image_size,
                print_option,
            } => {
                // Prefix: pictureType=0x02, printOption, printOption2=0x00, zero=0x00
                let mut payload = vec![0x02, *print_option, 0x00, 0x00];
                payload.extend_from_slice(&image_size.to_be_bytes());
                protocol::build_packet(OP_DOWNLOAD_START, &payload)
            }
            Command::Data { index, data } => {
                let mut payload = index.to_be_bytes().to_vec();
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
            } => {
                // Full LED format: [when, frame_count, speed, repeat, B, G, R]
                // Colors are BGR order per javl/InstaxBLE reference
                // repeat=255 keeps the color on indefinitely
                protocol::build_packet(
                    OP_LED_PATTERN_SETTINGS,
                    &[*pattern, 0x01, 0x01, 0xFF, *blue, *green, *red],
                )
            }
            Command::Shutdown => protocol::build_packet(OP_SHUT_DOWN, &[]),
            Command::Reset => protocol::build_packet(OP_RESET, &[]),
        }
    }
}

/// Responses received from the printer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Response {
    /// Device info response.
    DeviceInfo { payload: Vec<u8> },
    /// Image support info (width, height).
    ImageSupportInfo {
        width: u16,
        height: u16,
        payload: Vec<u8>,
    },
    /// Battery status.
    BatteryStatus { state: u8, level: u8 },
    /// Printer function info (film remaining, charging).
    PrinterFunctionInfo {
        film_remaining: u8,
        is_charging: bool,
    },
    /// Print history / count.
    HistoryInfo { count: u16 },
    /// Download ACK (ready for next chunk or operation).
    DownloadAck { status: u8 },
    /// Print started / completed notification.
    PrintStatus { status: u8 },
    /// LED settings acknowledged.
    LedAck,
    /// Unknown / unrecognized response.
    Unknown { opcode: u16, payload: Vec<u8> },
}

impl Response {
    /// Decode a response from a parsed protocol packet.
    ///
    /// For SUPPORT_FUNCTION_INFO responses, the first payload byte is a status/return
    /// code and the second byte is the InfoType that identifies the response subtype.
    pub fn decode(packet: &protocol::Packet) -> Self {
        match packet.opcode {
            OP_DEVICE_INFO_SERVICE => Response::DeviceInfo {
                payload: packet.payload.clone(),
            },
            OP_SUPPORT_FUNCTION_INFO => {
                // payload[0] = return code, payload[1] = InfoType, payload[2..] = data
                if packet.payload.len() < 2 {
                    return Response::Unknown {
                        opcode: packet.opcode,
                        payload: packet.payload.clone(),
                    };
                }
                if packet.payload[0] != 0 {
                    return Response::Unknown {
                        opcode: packet.opcode,
                        payload: packet.payload.clone(),
                    };
                }
                let info_type = packet.payload[1];
                let data = &packet.payload[2..];
                match info_type {
                    INFO_IMAGE_SUPPORT => {
                        if data.len() < 4 {
                            return Response::Unknown {
                                opcode: packet.opcode,
                                payload: packet.payload.clone(),
                            };
                        }
                        let width = u16::from_be_bytes([data[0], data[1]]);
                        let height = u16::from_be_bytes([data[2], data[3]]);
                        Response::ImageSupportInfo {
                            width,
                            height,
                            payload: packet.payload.clone(),
                        }
                    }
                    INFO_BATTERY => {
                        // data[0] = battery state, data[1] = battery percentage
                        if data.len() < 2 {
                            return Response::Unknown {
                                opcode: packet.opcode,
                                payload: packet.payload.clone(),
                            };
                        }
                        let state = data[0];
                        let level = data[1];
                        Response::BatteryStatus { state, level }
                    }
                    INFO_PRINTER_FUNCTION => {
                        // data[0]: bits 0-3 = photos left, bit 7 = charging
                        if data.is_empty() {
                            return Response::Unknown {
                                opcode: packet.opcode,
                                payload: packet.payload.clone(),
                            };
                        }
                        let byte = data[0];
                        let film_remaining = byte & 0x0F;
                        let is_charging = (byte & 0x80) != 0;
                        Response::PrinterFunctionInfo {
                            film_remaining,
                            is_charging,
                        }
                    }
                    INFO_PRINT_HISTORY => {
                        if data.len() < 2 {
                            return Response::Unknown {
                                opcode: packet.opcode,
                                payload: packet.payload.clone(),
                            };
                        };
                        let count = u16::from_be_bytes([data[0], data[1]]);
                        Response::HistoryInfo { count }
                    }
                    _ => Response::Unknown {
                        opcode: packet.opcode,
                        payload: packet.payload.clone(),
                    },
                }
            }
            OP_DOWNLOAD_START | OP_DATA | OP_DOWNLOAD_END | OP_DOWNLOAD_CANCEL => {
                match packet.payload.first().copied() {
                    Some(status) => Response::DownloadAck { status },
                    None => Response::Unknown {
                        opcode: packet.opcode,
                        payload: packet.payload.clone(),
                    },
                }
            }
            OP_PRINT_IMAGE => match packet.payload.first().copied() {
                Some(status) => Response::PrintStatus { status },
                None => Response::Unknown {
                    opcode: packet.opcode,
                    payload: packet.payload.clone(),
                },
            },
            OP_LED_PATTERN_SETTINGS => Response::LedAck,
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
        let pkt = Command::DeviceInfo.encode().unwrap();
        let parsed = protocol::parse_packet(&pkt).unwrap();
        assert_eq!(parsed.opcode, OP_DEVICE_INFO_SERVICE);
        assert!(parsed.payload.is_empty());
    }

    #[test]
    fn encode_image_support_info() {
        let pkt = Command::ImageSupportInfo.encode().unwrap();
        let parsed = protocol::parse_packet(&pkt).unwrap();
        assert_eq!(parsed.opcode, OP_SUPPORT_FUNCTION_INFO);
        assert_eq!(parsed.payload, vec![INFO_IMAGE_SUPPORT]);
    }

    #[test]
    fn encode_battery_status() {
        let pkt = Command::BatteryStatus.encode().unwrap();
        let parsed = protocol::parse_packet(&pkt).unwrap();
        assert_eq!(parsed.opcode, OP_SUPPORT_FUNCTION_INFO);
        assert_eq!(parsed.payload, vec![INFO_BATTERY]);
    }

    #[test]
    fn encode_download_start() {
        let cmd = Command::DownloadStart {
            image_size: 50000,
            print_option: 0,
        };
        let pkt = cmd.encode().unwrap();
        let parsed = protocol::parse_packet(&pkt).unwrap();
        assert_eq!(parsed.opcode, OP_DOWNLOAD_START);
        // First 4 bytes: pictureType=0x02, printOption, printOption2, zero
        assert_eq!(&parsed.payload[0..4], &[0x02, 0x00, 0x00, 0x00]);
        // Next 4 bytes: image size
        let size = u32::from_be_bytes(parsed.payload[4..8].try_into().unwrap());
        assert_eq!(size, 50000);
    }

    #[test]
    fn encode_download_start_natural_mode() {
        let cmd = Command::DownloadStart {
            image_size: 50000,
            print_option: 1,
        };
        let pkt = cmd.encode().unwrap();
        let parsed = protocol::parse_packet(&pkt).unwrap();
        assert_eq!(parsed.opcode, OP_DOWNLOAD_START);
        assert_eq!(&parsed.payload[0..4], &[0x02, 0x01, 0x00, 0x00]);
    }

    #[test]
    fn encode_data_chunk() {
        let data = vec![0xAA, 0xBB, 0xCC];
        let cmd = Command::Data {
            index: 3,
            data: data.clone(),
        };
        let pkt = cmd.encode().unwrap();
        let parsed = protocol::parse_packet(&pkt).unwrap();
        assert_eq!(parsed.opcode, OP_DATA);
        let index = u32::from_be_bytes(parsed.payload[0..4].try_into().unwrap());
        assert_eq!(index, 3);
        assert_eq!(&parsed.payload[4..], &[0xAA, 0xBB, 0xCC]);
    }

    #[test]
    fn encode_download_end() {
        let pkt = Command::DownloadEnd.encode().unwrap();
        let parsed = protocol::parse_packet(&pkt).unwrap();
        assert_eq!(parsed.opcode, OP_DOWNLOAD_END);
    }

    #[test]
    fn encode_print_image() {
        let pkt = Command::PrintImage.encode().unwrap();
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
        let pkt = cmd.encode().unwrap();
        let parsed = protocol::parse_packet(&pkt).unwrap();
        assert_eq!(parsed.opcode, OP_LED_PATTERN_SETTINGS);
        // [when=1, count=1, speed=1, repeat=255, B=0, G=128, R=255]
        assert_eq!(parsed.payload, vec![1, 0x01, 0x01, 0xFF, 0, 128, 255]);
    }

    #[test]
    fn decode_image_support_info() {
        // Simulate response: payload[0]=return code, payload[1]=InfoType, payload[2..]=data
        let mut payload = vec![0x00, INFO_IMAGE_SUPPORT];
        payload.extend_from_slice(&600u16.to_be_bytes());
        payload.extend_from_slice(&800u16.to_be_bytes());
        let packet = protocol::Packet {
            opcode: OP_SUPPORT_FUNCTION_INFO,
            payload,
        };
        match Response::decode(&packet) {
            Response::ImageSupportInfo { width, height, .. } => {
                assert_eq!(width, 600);
                assert_eq!(height, 800);
            }
            other => panic!("expected ImageSupportInfo, got {other:?}"),
        }
    }

    #[test]
    fn decode_battery_status() {
        // payload[0]=return code, payload[1]=InfoType(1), payload[2]=state, payload[3]=percentage
        let packet = protocol::Packet {
            opcode: OP_SUPPORT_FUNCTION_INFO,
            payload: vec![0x00, INFO_BATTERY, 0x00, 85],
        };
        match Response::decode(&packet) {
            Response::BatteryStatus { state, level } => {
                assert_eq!(state, 0x00);
                assert_eq!(level, 85);
            }
            other => panic!("expected BatteryStatus, got {other:?}"),
        }
    }

    #[test]
    fn decode_printer_function_info() {
        // payload[0]=return code, payload[1]=InfoType(2), payload[2]=data byte
        // film_remaining = data & 0x0F, is_charging = data & 0x80
        let packet = protocol::Packet {
            opcode: OP_SUPPORT_FUNCTION_INFO,
            payload: vec![0x00, INFO_PRINTER_FUNCTION, 0x85], // 5 remaining, charging
        };
        match Response::decode(&packet) {
            Response::PrinterFunctionInfo {
                film_remaining,
                is_charging,
            } => {
                assert_eq!(film_remaining, 5);
                assert!(is_charging);
            }
            other => panic!("expected PrinterFunctionInfo, got {other:?}"),
        }
    }

    #[test]
    fn decode_history_info() {
        let packet = protocol::Packet {
            opcode: OP_SUPPORT_FUNCTION_INFO,
            payload: vec![0x00, INFO_PRINT_HISTORY, 0x00, 42],
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
    fn decode_download_ack_missing_status_fails_closed() {
        let packet = protocol::Packet {
            opcode: OP_DOWNLOAD_START,
            payload: vec![],
        };
        match Response::decode(&packet) {
            Response::Unknown { opcode, payload } => {
                assert_eq!(opcode, OP_DOWNLOAD_START);
                assert!(payload.is_empty());
            }
            other => panic!("expected Unknown, got {other:?}"),
        }
    }

    #[test]
    fn decode_print_status_missing_status_fails_closed() {
        let packet = protocol::Packet {
            opcode: OP_PRINT_IMAGE,
            payload: vec![],
        };
        match Response::decode(&packet) {
            Response::Unknown { opcode, payload } => {
                assert_eq!(opcode, OP_PRINT_IMAGE);
                assert!(payload.is_empty());
            }
            other => panic!("expected Unknown, got {other:?}"),
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
    fn encode_shutdown() {
        let pkt = Command::Shutdown.encode().unwrap();
        let parsed = protocol::parse_packet(&pkt).unwrap();
        assert_eq!(parsed.opcode, OP_SHUT_DOWN);
        assert!(parsed.payload.is_empty());
    }

    #[test]
    fn encode_reset() {
        let pkt = Command::Reset.encode().unwrap();
        let parsed = protocol::parse_packet(&pkt).unwrap();
        assert_eq!(parsed.opcode, OP_RESET);
        assert!(parsed.payload.is_empty());
    }

    #[test]
    fn encode_decode_roundtrip() {
        let cmd = Command::BatteryStatus;
        let raw = cmd.encode().unwrap();
        let packet = protocol::parse_packet(&raw).unwrap();
        assert_eq!(packet.opcode, OP_SUPPORT_FUNCTION_INFO);
        assert_eq!(packet.payload, vec![INFO_BATTERY]);
    }
}

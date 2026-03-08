//! Instax BLE packet protocol: build, parse, checksum, and MTU fragmentation.
//!
//! ## Wire format
//!
//! ```text
//! [0x41 0x62] [len:2B big-endian] [opcode:2B] [payload...] [checksum:1B]
//! ```
//!
//! - Header: always `0x41 0x62`
//! - Length: 2 bytes big-endian, total packet size (header + length + opcode + payload + checksum)
//! - Checksum: `(255 - (sum_of_all_preceding_bytes & 255)) & 255`
//!
//! ## MTU Fragmentation
//!
//! BLE packets larger than 182 bytes are split into sub-packets for transmission.

/// Request header bytes (client → printer: "Ab").
pub const HEADER: [u8; 2] = [0x41, 0x62];
/// Response header bytes (printer → client: "aB").
pub const RESPONSE_HEADER: [u8; 2] = [0x61, 0x42];

/// Maximum BLE sub-packet size for MTU fragmentation.
pub const MTU_SIZE: usize = 182;

/// Minimum packet size: header(2) + length(2) + opcode(2) + checksum(1) = 7.
pub const MIN_PACKET_SIZE: usize = 7;

/// Maximum JPEG image size in bytes (approximately 105KB).
pub const MAX_IMAGE_SIZE: usize = 105_000;

/// Compute the Instax checksum over a byte slice.
///
/// Formula: `(255 - (sum & 255)) & 255`
pub fn checksum(data: &[u8]) -> u8 {
    let sum: u32 = data.iter().map(|&b| b as u32).sum();
    ((255 - (sum & 255)) & 255) as u8
}

/// Build a complete Instax protocol packet.
///
/// Returns the full packet bytes: `[header][length][opcode][payload][checksum]`.
pub fn build_packet(opcode: u16, payload: &[u8]) -> Vec<u8> {
    // Length = total packet size: header(2) + length(2) + opcode(2) + payload + checksum(1)
    let total_size = 7 + payload.len();
    let mut packet = Vec::with_capacity(total_size);

    // Header
    packet.extend_from_slice(&HEADER);
    // Length (big-endian) — total packet size
    packet.extend_from_slice(&(total_size as u16).to_be_bytes());
    // Opcode (big-endian)
    packet.extend_from_slice(&opcode.to_be_bytes());
    // Payload
    packet.extend_from_slice(payload);
    // Checksum (over everything before it)
    let chk = checksum(&packet);
    packet.push(chk);

    packet
}

/// Parsed Instax protocol packet.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Packet {
    /// The 2-byte opcode.
    pub opcode: u16,
    /// The payload bytes (may be empty).
    pub payload: Vec<u8>,
}

/// Check if the first two bytes are a valid Instax header (request or response).
fn is_valid_header(b0: u8, b1: u8) -> bool {
    (b0 == HEADER[0] && b1 == HEADER[1]) || (b0 == RESPONSE_HEADER[0] && b1 == RESPONSE_HEADER[1])
}

/// Parse a complete Instax protocol packet from raw bytes.
///
/// Validates header, length, and checksum. Returns `None` if invalid.
/// Accepts both request header (`0x41 0x62`) and response header (`0x61 0x42`).
pub fn parse_packet(data: &[u8]) -> Option<Packet> {
    if data.len() < MIN_PACKET_SIZE {
        return None;
    }

    // Check header (accept both request "Ab" and response "aB")
    if !is_valid_header(data[0], data[1]) {
        return None;
    }

    // Parse length (total packet size)
    let expected_total = u16::from_be_bytes([data[2], data[3]]) as usize;
    if expected_total < MIN_PACKET_SIZE || data.len() < expected_total {
        return None;
    }

    // Verify checksum
    let chk_data = &data[..expected_total - 1];
    let expected_chk = checksum(chk_data);
    if data[expected_total - 1] != expected_chk {
        return None;
    }

    // Parse opcode
    let opcode = u16::from_be_bytes([data[4], data[5]]);

    // Extract payload (between opcode and checksum)
    let payload = data[6..expected_total - 1].to_vec();

    Some(Packet { opcode, payload })
}

/// Fragment a packet into MTU-sized sub-packets for BLE transmission.
pub fn fragment(packet: &[u8]) -> Vec<Vec<u8>> {
    packet.chunks(MTU_SIZE).map(|c| c.to_vec()).collect()
}

/// Reassembles fragmented BLE sub-packets into complete protocol packets.
#[derive(Debug, Default)]
pub struct PacketAssembler {
    buffer: Vec<u8>,
}

impl PacketAssembler {
    /// Create a new empty assembler.
    pub fn new() -> Self {
        Self { buffer: Vec::new() }
    }

    /// Feed incoming BLE data into the assembler.
    ///
    /// Returns a complete `Packet` if one has been fully reassembled, or `None`
    /// if more data is needed.
    pub fn feed(&mut self, data: &[u8]) -> Option<Packet> {
        self.buffer.extend_from_slice(data);

        // Need at least the header + length to know the full packet size
        if self.buffer.len() < 4 {
            return None;
        }

        // Check for valid header (accept both request and response headers)
        if !is_valid_header(self.buffer[0], self.buffer[1]) {
            // Invalid header — clear buffer
            self.buffer.clear();
            return None;
        }

        let expected_total = u16::from_be_bytes([self.buffer[2], self.buffer[3]]) as usize;

        if expected_total < MIN_PACKET_SIZE || self.buffer.len() < expected_total {
            return None; // Need more data
        }

        // We have a complete packet — parse it
        let packet_data: Vec<u8> = self.buffer.drain(..expected_total).collect();
        parse_packet(&packet_data)
    }

    /// Reset the assembler, discarding any buffered data.
    pub fn reset(&mut self) {
        self.buffer.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn checksum_empty() {
        assert_eq!(checksum(&[]), 255);
    }

    #[test]
    fn checksum_single_byte() {
        // (255 - (0x41 & 255)) & 255 = 255 - 65 = 190
        assert_eq!(checksum(&[0x41]), 190);
    }

    #[test]
    fn checksum_known_values() {
        // Sum = 0x41 + 0x62 = 163; (255 - 163) & 255 = 92
        assert_eq!(checksum(&[0x41, 0x62]), 92);
    }

    #[test]
    fn build_packet_empty_payload() {
        let pkt = build_packet(0x0102, &[]);
        // header(2) + len(2) + opcode(2) + checksum(1) = 7
        assert_eq!(pkt.len(), 7);
        assert_eq!(&pkt[0..2], &HEADER);
        // length = total packet size = 7
        assert_eq!(u16::from_be_bytes([pkt[2], pkt[3]]), 7);
        // opcode
        assert_eq!(u16::from_be_bytes([pkt[4], pkt[5]]), 0x0102);
        // checksum validates
        let chk = checksum(&pkt[..pkt.len() - 1]);
        assert_eq!(pkt[pkt.len() - 1], chk);
    }

    #[test]
    fn build_packet_with_payload() {
        let payload = vec![0xAA, 0xBB, 0xCC];
        let pkt = build_packet(0x2010, &payload);
        assert_eq!(pkt.len(), 10); // 7 + 3
                                   // length = total packet size = 10
        assert_eq!(u16::from_be_bytes([pkt[2], pkt[3]]), 10);
        assert_eq!(&pkt[6..9], &[0xAA, 0xBB, 0xCC]);
    }

    #[test]
    fn parse_packet_roundtrip() {
        let pkt = build_packet(0x4321, &[0x01, 0x02]);
        let parsed = parse_packet(&pkt).expect("should parse");
        assert_eq!(parsed.opcode, 0x4321);
        assert_eq!(parsed.payload, vec![0x01, 0x02]);
    }

    #[test]
    fn parse_packet_empty_payload() {
        let pkt = build_packet(0x1000, &[]);
        let parsed = parse_packet(&pkt).expect("should parse");
        assert_eq!(parsed.opcode, 0x1000);
        assert!(parsed.payload.is_empty());
    }

    #[test]
    fn parse_packet_bad_header() {
        let mut pkt = build_packet(0x0000, &[]);
        pkt[0] = 0xFF;
        assert!(parse_packet(&pkt).is_none());
    }

    #[test]
    fn parse_packet_bad_checksum() {
        let mut pkt = build_packet(0x0000, &[]);
        let last = pkt.len() - 1;
        pkt[last] ^= 0xFF; // corrupt checksum
        assert!(parse_packet(&pkt).is_none());
    }

    #[test]
    fn parse_packet_too_short() {
        assert!(parse_packet(&[0x41, 0x62]).is_none());
    }

    #[test]
    fn parse_packet_truncated() {
        let pkt = build_packet(0x0000, &[0x01, 0x02, 0x03]);
        // Remove last byte
        assert!(parse_packet(&pkt[..pkt.len() - 1]).is_none());
    }

    #[test]
    fn fragment_small_packet() {
        let pkt = build_packet(0x0000, &[]);
        let fragments = fragment(&pkt);
        assert_eq!(fragments.len(), 1);
        assert_eq!(fragments[0], pkt);
    }

    #[test]
    fn fragment_large_packet() {
        let payload = vec![0u8; MTU_SIZE * 2]; // will need 3 fragments (with overhead)
        let pkt = build_packet(0x0000, &payload);
        let fragments = fragment(&pkt);
        assert!(fragments.len() > 1);
        // Reassemble and verify
        let reassembled: Vec<u8> = fragments.into_iter().flatten().collect();
        assert_eq!(reassembled, pkt);
    }

    #[test]
    fn fragment_exact_mtu() {
        let payload = vec![0u8; MTU_SIZE - 7]; // exactly one MTU fragment
        let pkt = build_packet(0x0000, &payload);
        assert_eq!(pkt.len(), MTU_SIZE);
        let fragments = fragment(&pkt);
        assert_eq!(fragments.len(), 1);
    }

    #[test]
    fn assembler_single_packet() {
        let pkt = build_packet(0x1234, &[0xAB]);
        let mut asm = PacketAssembler::new();
        let result = asm.feed(&pkt);
        assert!(result.is_some());
        let parsed = result.unwrap();
        assert_eq!(parsed.opcode, 0x1234);
        assert_eq!(parsed.payload, vec![0xAB]);
    }

    #[test]
    fn assembler_fragmented() {
        let pkt = build_packet(0x5678, &[0x01, 0x02, 0x03]);
        let mut asm = PacketAssembler::new();

        // Feed first half
        let mid = pkt.len() / 2;
        assert!(asm.feed(&pkt[..mid]).is_none());

        // Feed second half
        let result = asm.feed(&pkt[mid..]);
        assert!(result.is_some());
        let parsed = result.unwrap();
        assert_eq!(parsed.opcode, 0x5678);
    }

    #[test]
    fn assembler_invalid_header_resets() {
        let mut asm = PacketAssembler::new();
        // Feed 4+ bytes with an invalid header — triggers a clear
        assert!(asm.feed(&[0xFF, 0xFF, 0x00, 0x03]).is_none());
        // Buffer should be cleared, so a valid packet can still parse
        let pkt = build_packet(0x0001, &[]);
        assert!(asm.feed(&pkt).is_some());
    }

    #[test]
    fn assembler_reset() {
        let pkt = build_packet(0x0001, &[0x01]);
        let mut asm = PacketAssembler::new();
        asm.feed(&pkt[..3]); // partial
        asm.reset();
        // After reset, feeding the full packet should work
        let result = asm.feed(&pkt);
        assert!(result.is_some());
    }
}

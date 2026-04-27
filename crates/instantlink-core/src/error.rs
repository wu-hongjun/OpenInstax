//! Error types for instantlink-core.

/// Protocol-level errors surfaced by `PacketAssembler::feed`.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum ProtocolError {
    /// The header bytes were invalid; `discarded` bytes were dropped from the buffer.
    #[error("invalid header: {discarded} byte(s) discarded")]
    InvalidHeader { discarded: usize },

    /// The packet header and length were valid but the checksum did not match.
    #[error("bad checksum for opcode 0x{opcode:04x}")]
    BadChecksum { opcode: u16 },

    /// The declared packet length does not match the available data.
    #[error("length mismatch: declared {declared}, actual {actual}")]
    LengthMismatch { declared: u16, actual: usize },
}

/// All errors that can occur in instantlink-core operations.
#[derive(Debug, thiserror::Error)]
pub enum PrinterError {
    /// No printer was found during BLE scanning.
    #[error("no Instax printer found")]
    PrinterNotFound,

    /// Multiple printers found; a device name is required to disambiguate.
    #[error("multiple printers found ({count}); specify a device name")]
    MultiplePrinters { count: usize },

    /// BLE adapter or communication error.
    #[error("BLE error: {0}")]
    Ble(String),

    /// The printer did not respond within the timeout.
    #[error("printer response timed out")]
    Timeout,

    /// The printer returned an unexpected or unparseable response.
    #[error("unexpected printer response: {0}")]
    UnexpectedResponse(String),

    /// A protocol-level error (bad checksum, invalid packet, etc.).
    #[error("protocol error: {0}")]
    Protocol(String),

    /// Image processing failed (load, resize, encode).
    #[error("image error: {0}")]
    Image(String),

    /// The image is too large to send to the printer.
    #[error("image too large: {size} bytes (max {max} bytes)")]
    ImageTooLarge { size: usize, max: usize },

    /// The printer rejected a print command.
    #[error("print rejected: {0}")]
    PrintRejected(String),

    /// No film remaining in the printer.
    #[error("no film remaining")]
    NoFilm,

    /// Printer cover is open.
    #[error("printer cover is open")]
    CoverOpen,

    /// Printer is busy.
    #[error("printer is busy")]
    PrinterBusy,

    /// Battery too low to print.
    #[error("battery too low ({percent}%)")]
    LowBattery { percent: u8 },

    /// I/O error.
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
}

/// A type alias for `Result<T, PrinterError>`.
pub type Result<T> = std::result::Result<T, PrinterError>;

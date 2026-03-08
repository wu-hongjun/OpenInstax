//! openinstax-core — BLE protocol, image processing, and device communication
//! for Fujifilm Instax Link printers.

pub mod commands;
pub mod device;
pub mod error;
pub mod image;
pub mod models;
pub mod printer;
pub mod protocol;
pub mod transport;

// Re-export key types for convenience.
pub use device::{InstaxDevice, PrinterStatus};
pub use error::{InstaxError, Result};
pub use image::FitMode;
pub use models::PrinterModel;

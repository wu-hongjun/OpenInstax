//! Printer model definitions and per-model specifications.

use serde::{Deserialize, Serialize};

/// Supported Instax Link printer models.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum PrinterModel {
    /// Instax Mini Link (600x800, 900B chunks)
    Mini,
    /// Instax Square Link (800x800, 1808B chunks)
    Square,
    /// Instax Wide Link (1260x840, 900B chunks)
    Wide,
}

/// Per-model specifications.
#[derive(Debug, Clone)]
pub struct ModelSpec {
    /// Image width in pixels.
    pub width: u32,
    /// Image height in pixels.
    pub height: u32,
    /// Data chunk size in bytes for image transfer.
    pub chunk_size: usize,
    /// Human-readable model name.
    pub name: &'static str,
}

impl PrinterModel {
    /// Get the specification for this printer model.
    pub fn spec(self) -> ModelSpec {
        match self {
            PrinterModel::Mini => ModelSpec {
                width: 600,
                height: 800,
                chunk_size: 900,
                name: "Instax Mini Link",
            },
            PrinterModel::Square => ModelSpec {
                width: 800,
                height: 800,
                chunk_size: 1808,
                name: "Instax Square Link",
            },
            PrinterModel::Wide => ModelSpec {
                width: 1260,
                height: 840,
                chunk_size: 900,
                name: "Instax Wide Link",
            },
        }
    }

    /// All supported printer models.
    pub fn all() -> &'static [PrinterModel] {
        &[PrinterModel::Mini, PrinterModel::Square, PrinterModel::Wide]
    }
}

impl std::fmt::Display for PrinterModel {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.spec().name)
    }
}

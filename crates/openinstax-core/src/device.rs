//! Instax device abstraction and BLE-backed implementation.
//!
//! The [`InstaxDevice`] trait defines the async interface for interacting with
//! an Instax printer. [`BleInstaxDevice`] provides the btleplug-backed
//! implementation with ACK-based print flow.

use std::path::Path;

use async_trait::async_trait;

use crate::commands::{Command, Response};
use crate::error::{InstaxError, Result};
use crate::image::{self, FitMode};
use crate::models::PrinterModel;
use crate::transport::{Transport, DEFAULT_TIMEOUT};

/// Printer status information.
#[derive(Debug, Clone)]
pub struct PrinterStatus {
    /// Battery level (0–100).
    pub battery: u8,
    /// Remaining film count.
    pub film_remaining: u8,
    /// Total print count.
    pub print_count: u32,
    /// Detected printer model.
    pub model: PrinterModel,
    /// Device name (from BLE advertisement).
    pub name: String,
}

/// Async trait for controlling an Instax printer. Enables mocking in tests.
#[async_trait]
pub trait InstaxDevice: Send + Sync {
    /// Get the printer's current status.
    async fn status(&self) -> Result<PrinterStatus>;

    /// Get battery level (0–100).
    async fn battery(&self) -> Result<u8>;

    /// Get remaining film count.
    async fn film_remaining(&self) -> Result<u8>;

    /// Get total print count.
    async fn print_count(&self) -> Result<u32>;

    /// Get the detected printer model.
    fn model(&self) -> PrinterModel;

    /// Get the device name.
    fn name(&self) -> &str;

    /// Print an image from a file path.
    ///
    /// Calls `progress` with (chunks_sent, total_chunks) during transfer.
    async fn print_file(
        &self,
        path: &Path,
        fit: FitMode,
        quality: u8,
        progress: Option<&(dyn Fn(usize, usize) + Send + Sync)>,
    ) -> Result<()>;

    /// Print from raw image bytes.
    async fn print_bytes(
        &self,
        data: &[u8],
        fit: FitMode,
        quality: u8,
        progress: Option<&(dyn Fn(usize, usize) + Send + Sync)>,
    ) -> Result<()>;

    /// Set the LED color and pattern.
    async fn set_led(&self, r: u8, g: u8, b: u8, pattern: u8) -> Result<()>;

    /// Turn off the LED.
    async fn led_off(&self) -> Result<()> {
        self.set_led(0, 0, 0, 0).await
    }

    /// Disconnect from the printer.
    async fn disconnect(&self) -> Result<()>;
}

/// BLE-backed Instax device.
pub struct BleInstaxDevice {
    transport: Box<dyn Transport>,
    model: PrinterModel,
    name: String,
}

impl BleInstaxDevice {
    /// Create a new BLE Instax device with a connected transport.
    ///
    /// Auto-detects the printer model by querying IMAGE_SUPPORT_INFO.
    pub async fn new(transport: Box<dyn Transport>, name: String) -> Result<Self> {
        // Query image support info to detect model
        let cmd = Command::ImageSupportInfo;
        let packet = transport
            .send_and_receive(&cmd.encode(), DEFAULT_TIMEOUT)
            .await?;
        let response = Response::decode(&packet);

        let model = match response {
            Response::ImageSupportInfo { width, height, .. } => detect_model(width, height)?,
            _ => {
                return Err(InstaxError::UnexpectedResponse(
                    "expected ImageSupportInfo response".into(),
                ));
            }
        };

        log::info!("Connected to {} ({})", name, model);

        Ok(Self {
            transport,
            model,
            name,
        })
    }

    /// Send a command and decode the response.
    async fn command(&self, cmd: &Command) -> Result<Response> {
        let packet = self
            .transport
            .send_and_receive(&cmd.encode(), DEFAULT_TIMEOUT)
            .await?;
        Ok(Response::decode(&packet))
    }

    /// Send JPEG data to the printer with ACK-based flow control.
    async fn send_image_data(
        &self,
        jpeg_data: &[u8],
        chunks: &[Vec<u8>],
        progress: Option<&(dyn Fn(usize, usize) + Send + Sync)>,
    ) -> Result<()> {
        let total = chunks.len();

        // DOWNLOAD_START
        let start_resp = self
            .command(&Command::DownloadStart {
                image_size: jpeg_data.len() as u32,
            })
            .await?;
        match start_resp {
            Response::DownloadAck { status: 0 } => {}
            Response::DownloadAck { status } => {
                return Err(InstaxError::PrintRejected(format!(
                    "download start rejected with status {status}"
                )));
            }
            _ => {
                return Err(InstaxError::UnexpectedResponse(
                    "expected DownloadAck for DownloadStart".into(),
                ));
            }
        }

        // Send data chunks with ACK per chunk
        let mut offset: u32 = 0;
        for (i, chunk) in chunks.iter().enumerate() {
            let data_resp = self
                .command(&Command::Data {
                    offset,
                    data: chunk.clone(),
                })
                .await?;
            match data_resp {
                Response::DownloadAck { status: 0 } => {}
                Response::DownloadAck { status } => {
                    return Err(InstaxError::PrintRejected(format!(
                        "data chunk {i} rejected with status {status}"
                    )));
                }
                _ => {
                    return Err(InstaxError::UnexpectedResponse(
                        "expected DownloadAck for Data".into(),
                    ));
                }
            }
            offset += chunk.len() as u32;

            if let Some(cb) = progress {
                cb(i + 1, total);
            }
        }

        // DOWNLOAD_END
        let end_resp = self.command(&Command::DownloadEnd).await?;
        match end_resp {
            Response::DownloadAck { status: 0 } => {}
            Response::DownloadAck { status } => {
                return Err(InstaxError::PrintRejected(format!(
                    "download end rejected with status {status}"
                )));
            }
            _ => {
                return Err(InstaxError::UnexpectedResponse(
                    "expected DownloadAck for DownloadEnd".into(),
                ));
            }
        }

        Ok(())
    }
}

#[async_trait]
impl InstaxDevice for BleInstaxDevice {
    async fn status(&self) -> Result<PrinterStatus> {
        let battery = self.battery().await?;
        let film_remaining = self.film_remaining().await?;
        let print_count = self.print_count().await?;

        Ok(PrinterStatus {
            battery,
            film_remaining,
            print_count,
            model: self.model,
            name: self.name.clone(),
        })
    }

    async fn battery(&self) -> Result<u8> {
        match self.command(&Command::BatteryStatus).await? {
            Response::BatteryStatus { level } => Ok(level),
            _ => Err(InstaxError::UnexpectedResponse(
                "expected BatteryStatus".into(),
            )),
        }
    }

    async fn film_remaining(&self) -> Result<u8> {
        match self.command(&Command::RemainingInfo).await? {
            Response::RemainingInfo { remaining } => Ok(remaining),
            _ => Err(InstaxError::UnexpectedResponse(
                "expected RemainingInfo".into(),
            )),
        }
    }

    async fn print_count(&self) -> Result<u32> {
        match self.command(&Command::HistoryInfo).await? {
            Response::HistoryInfo { count } => Ok(count),
            _ => Err(InstaxError::UnexpectedResponse(
                "expected HistoryInfo".into(),
            )),
        }
    }

    fn model(&self) -> PrinterModel {
        self.model
    }

    fn name(&self) -> &str {
        &self.name
    }

    async fn print_file(
        &self,
        path: &Path,
        fit: FitMode,
        quality: u8,
        progress: Option<&(dyn Fn(usize, usize) + Send + Sync)>,
    ) -> Result<()> {
        let (jpeg_data, chunks) = image::prepare_image(path, self.model, fit, quality)?;
        self.send_image_data(&jpeg_data, &chunks, progress).await?;

        // Trigger print
        match self.command(&Command::PrintImage).await? {
            Response::PrintStatus { status: 0 } => Ok(()),
            Response::PrintStatus { status } => Err(InstaxError::PrintRejected(format!(
                "print failed with status {status}"
            ))),
            _ => Err(InstaxError::UnexpectedResponse(
                "expected PrintStatus".into(),
            )),
        }
    }

    async fn print_bytes(
        &self,
        data: &[u8],
        fit: FitMode,
        quality: u8,
        progress: Option<&(dyn Fn(usize, usize) + Send + Sync)>,
    ) -> Result<()> {
        let (jpeg_data, chunks) = image::prepare_image_from_bytes(data, self.model, fit, quality)?;
        self.send_image_data(&jpeg_data, &chunks, progress).await?;

        match self.command(&Command::PrintImage).await? {
            Response::PrintStatus { status: 0 } => Ok(()),
            Response::PrintStatus { status } => Err(InstaxError::PrintRejected(format!(
                "print failed with status {status}"
            ))),
            _ => Err(InstaxError::UnexpectedResponse(
                "expected PrintStatus".into(),
            )),
        }
    }

    async fn set_led(&self, r: u8, g: u8, b: u8, pattern: u8) -> Result<()> {
        let cmd = Command::LedPatternSettings {
            red: r,
            green: g,
            blue: b,
            pattern,
        };
        match self.command(&cmd).await? {
            Response::LedAck => Ok(()),
            _ => Err(InstaxError::UnexpectedResponse("expected LedAck".into())),
        }
    }

    async fn disconnect(&self) -> Result<()> {
        self.transport.disconnect().await
    }
}

/// Detect the printer model from image support dimensions.
fn detect_model(width: u16, height: u16) -> Result<PrinterModel> {
    for model in PrinterModel::all() {
        let spec = model.spec();
        if spec.width == width as u32 && spec.height == height as u32 {
            return Ok(*model);
        }
    }
    Err(InstaxError::UnexpectedResponse(format!(
        "unknown printer dimensions: {width}x{height}"
    )))
}

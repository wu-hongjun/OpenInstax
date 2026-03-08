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
    pub print_count: u16,
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
    async fn print_count(&self) -> Result<u16>;

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
        match self.command(&Command::PrinterFunctionInfo).await? {
            Response::PrinterFunctionInfo { film_remaining, .. } => Ok(film_remaining),
            _ => Err(InstaxError::UnexpectedResponse(
                "expected PrinterFunctionInfo".into(),
            )),
        }
    }

    async fn print_count(&self) -> Result<u16> {
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::commands::{
        INFO_BATTERY, INFO_IMAGE_SUPPORT, INFO_PRINTER_FUNCTION, INFO_PRINT_HISTORY, OP_DATA,
        OP_DOWNLOAD_END, OP_DOWNLOAD_START, OP_LED_PATTERN_SETTINGS, OP_SUPPORT_FUNCTION_INFO,
    };
    use crate::protocol;
    use async_trait::async_trait;
    use std::collections::VecDeque;
    use std::sync::{Arc, Mutex};
    use std::time::Duration;

    // ── MockTransport ──────────────────────────────────────────────────────

    struct MockState {
        responses: VecDeque<Result<protocol::Packet>>,
        sent: Vec<Vec<u8>>,
    }

    struct MockTransport {
        state: Arc<Mutex<MockState>>,
    }

    impl MockTransport {
        fn new(
            responses: Vec<Result<protocol::Packet>>,
        ) -> (Box<dyn Transport>, Arc<Mutex<MockState>>) {
            let state = Arc::new(Mutex::new(MockState {
                responses: responses.into(),
                sent: Vec::new(),
            }));
            let transport = Box::new(MockTransport {
                state: Arc::clone(&state),
            });
            (transport, state)
        }
    }

    #[async_trait]
    impl Transport for MockTransport {
        async fn send(&self, data: &[u8]) -> Result<()> {
            self.state.lock().unwrap().sent.push(data.to_vec());
            Ok(())
        }

        async fn receive(&self, _timeout: Duration) -> Result<protocol::Packet> {
            self.state
                .lock()
                .unwrap()
                .responses
                .pop_front()
                .unwrap_or(Err(InstaxError::Timeout))
        }

        async fn disconnect(&self) -> Result<()> {
            Ok(())
        }
    }

    // ── Response helpers ───────────────────────────────────────────────────

    /// Build a SUPPORT_FUNCTION_INFO response packet.
    /// Wire format: payload[0]=return_code, payload[1]=info_type, payload[2..]=data
    fn support_function_packet(info_type: u8, data: &[u8]) -> Result<protocol::Packet> {
        let mut payload = vec![0x00, info_type]; // return_code=0, info_type
        payload.extend_from_slice(data);
        Ok(protocol::Packet {
            opcode: OP_SUPPORT_FUNCTION_INFO,
            payload,
        })
    }

    fn image_support_info_packet(model: PrinterModel) -> Result<protocol::Packet> {
        let spec = model.spec();
        let mut data = Vec::new();
        data.extend_from_slice(&(spec.width as u16).to_be_bytes());
        data.extend_from_slice(&(spec.height as u16).to_be_bytes());
        support_function_packet(INFO_IMAGE_SUPPORT, &data)
    }

    fn download_ack_packet(opcode: u16, status: u8) -> Result<protocol::Packet> {
        Ok(protocol::Packet {
            opcode,
            payload: vec![status],
        })
    }

    fn battery_packet(level: u8) -> Result<protocol::Packet> {
        // data[0]=battery_state, data[1]=battery_percentage
        support_function_packet(INFO_BATTERY, &[0x00, level])
    }

    fn printer_function_packet(film_remaining: u8, is_charging: bool) -> Result<protocol::Packet> {
        // data byte: bits 0-3 = photos left, bit 7 = charging
        let byte = film_remaining | if is_charging { 0x80 } else { 0 };
        support_function_packet(INFO_PRINTER_FUNCTION, &[byte])
    }

    fn history_packet(count: u16) -> Result<protocol::Packet> {
        support_function_packet(INFO_PRINT_HISTORY, &count.to_be_bytes())
    }

    fn led_ack_packet() -> Result<protocol::Packet> {
        Ok(protocol::Packet {
            opcode: OP_LED_PATTERN_SETTINGS,
            payload: vec![],
        })
    }

    // ── Device construction helper ─────────────────────────────────────────

    async fn make_device(
        model: PrinterModel,
        extra_responses: Vec<Result<protocol::Packet>>,
    ) -> (BleInstaxDevice, Arc<Mutex<MockState>>) {
        let mut responses = vec![image_support_info_packet(model)];
        responses.extend(extra_responses);
        let (transport, state) = MockTransport::new(responses);
        let device = BleInstaxDevice::new(transport, "TestPrinter".into())
            .await
            .expect("device creation should succeed");
        (device, state)
    }

    // ── Model Detection ────────────────────────────────────────────────────

    #[tokio::test]
    async fn detect_model_mini() {
        let (device, _) = make_device(PrinterModel::Mini, vec![]).await;
        assert_eq!(device.model(), PrinterModel::Mini);
    }

    #[tokio::test]
    async fn detect_model_square() {
        let (device, _) = make_device(PrinterModel::Square, vec![]).await;
        assert_eq!(device.model(), PrinterModel::Square);
    }

    #[tokio::test]
    async fn detect_model_wide() {
        let (device, _) = make_device(PrinterModel::Wide, vec![]).await;
        assert_eq!(device.model(), PrinterModel::Wide);
    }

    #[tokio::test]
    async fn detect_model_unknown() {
        // Unknown dimensions: 999x999
        let mut data = Vec::new();
        data.extend_from_slice(&999u16.to_be_bytes());
        data.extend_from_slice(&999u16.to_be_bytes());
        let packet = support_function_packet(INFO_IMAGE_SUPPORT, &data);
        let (transport, _) = MockTransport::new(vec![packet]);
        let Err(err) = BleInstaxDevice::new(transport, "Test".into()).await else {
            panic!("expected error");
        };
        assert!(err.to_string().contains("unknown printer dimensions"));
    }

    #[tokio::test]
    async fn detect_model_wrong_response() {
        let (transport, _) = MockTransport::new(vec![battery_packet(50)]);
        let Err(err) = BleInstaxDevice::new(transport, "Test".into()).await else {
            panic!("expected error");
        };
        assert!(err.to_string().contains("expected ImageSupportInfo"));
    }

    // ── Status Queries ─────────────────────────────────────────────────────

    #[tokio::test]
    async fn battery() {
        let (device, _) = make_device(PrinterModel::Mini, vec![battery_packet(85)]).await;
        assert_eq!(device.battery().await.unwrap(), 85);
    }

    #[tokio::test]
    async fn film_remaining() {
        let (device, _) =
            make_device(PrinterModel::Mini, vec![printer_function_packet(8, false)]).await;
        assert_eq!(device.film_remaining().await.unwrap(), 8);
    }

    #[tokio::test]
    async fn print_count() {
        let (device, _) = make_device(PrinterModel::Mini, vec![history_packet(142)]).await;
        assert_eq!(device.print_count().await.unwrap(), 142);
    }

    #[tokio::test]
    async fn status_all() {
        let (device, _) = make_device(
            PrinterModel::Mini,
            vec![
                battery_packet(85),
                printer_function_packet(8, false),
                history_packet(142),
            ],
        )
        .await;
        let status = device.status().await.unwrap();
        assert_eq!(status.battery, 85);
        assert_eq!(status.film_remaining, 8);
        assert_eq!(status.print_count, 142);
        assert_eq!(status.model, PrinterModel::Mini);
        assert_eq!(status.name, "TestPrinter");
    }

    #[tokio::test]
    async fn battery_unexpected_response() {
        let (device, _) =
            make_device(PrinterModel::Mini, vec![printer_function_packet(5, false)]).await;
        let result = device.battery().await;
        assert!(result.is_err());
        assert!(result
            .unwrap_err()
            .to_string()
            .contains("expected BatteryStatus"));
    }

    // ── LED Commands ───────────────────────────────────────────────────────

    #[tokio::test]
    async fn set_led() {
        let (device, _) = make_device(PrinterModel::Mini, vec![led_ack_packet()]).await;
        device.set_led(255, 128, 0, 1).await.unwrap();
    }

    #[tokio::test]
    async fn led_off() {
        let (device, state) = make_device(PrinterModel::Mini, vec![led_ack_packet()]).await;
        device.led_off().await.unwrap();
        let sent = &state.lock().unwrap().sent;
        // sent[0] is ImageSupportInfo query, sent[1] is LED command
        let led_packet = protocol::parse_packet(&sent[1]).unwrap();
        assert_eq!(led_packet.opcode, OP_LED_PATTERN_SETTINGS);
        assert_eq!(led_packet.payload, vec![0, 0, 0, 0]);
    }

    // ── Print Flow ─────────────────────────────────────────────────────────

    #[tokio::test]
    async fn send_image_data_single_chunk() {
        let jpeg_data = vec![0u8; 100];
        let chunks = vec![jpeg_data.clone()];
        let (device, _) = make_device(
            PrinterModel::Mini,
            vec![
                download_ack_packet(OP_DOWNLOAD_START, 0),
                download_ack_packet(OP_DATA, 0),
                download_ack_packet(OP_DOWNLOAD_END, 0),
            ],
        )
        .await;
        device
            .send_image_data(&jpeg_data, &chunks, None)
            .await
            .unwrap();
    }

    #[tokio::test]
    async fn send_image_data_multi_chunk() {
        let jpeg_data = vec![0u8; 2700];
        let chunks = image::chunk_image_data(&jpeg_data, PrinterModel::Mini);
        assert_eq!(chunks.len(), 3);
        let (device, state) = make_device(
            PrinterModel::Mini,
            vec![
                download_ack_packet(OP_DOWNLOAD_START, 0),
                download_ack_packet(OP_DATA, 0),
                download_ack_packet(OP_DATA, 0),
                download_ack_packet(OP_DATA, 0),
                download_ack_packet(OP_DOWNLOAD_END, 0),
            ],
        )
        .await;
        device
            .send_image_data(&jpeg_data, &chunks, None)
            .await
            .unwrap();

        let sent = &state.lock().unwrap().sent;
        // sent[0]=ImageSupportInfo, sent[1]=DownloadStart, sent[2..5]=Data, sent[5]=DownloadEnd

        let pkt0 = protocol::parse_packet(&sent[2]).unwrap();
        assert_eq!(pkt0.opcode, OP_DATA);
        let offset0 = u32::from_be_bytes(pkt0.payload[0..4].try_into().unwrap());
        assert_eq!(offset0, 0);

        let pkt1 = protocol::parse_packet(&sent[3]).unwrap();
        let offset1 = u32::from_be_bytes(pkt1.payload[0..4].try_into().unwrap());
        assert_eq!(offset1, 900);

        let pkt2 = protocol::parse_packet(&sent[4]).unwrap();
        let offset2 = u32::from_be_bytes(pkt2.payload[0..4].try_into().unwrap());
        assert_eq!(offset2, 1800);
    }

    // ── Error Paths ────────────────────────────────────────────────────────

    #[tokio::test]
    async fn download_start_rejected() {
        let (device, _) = make_device(
            PrinterModel::Mini,
            vec![download_ack_packet(OP_DOWNLOAD_START, 1)],
        )
        .await;
        let result = device
            .send_image_data(&[0u8; 100], &[vec![0u8; 100]], None)
            .await;
        assert!(result.is_err());
        assert!(result
            .unwrap_err()
            .to_string()
            .contains("download start rejected"));
    }

    #[tokio::test]
    async fn data_chunk_rejected() {
        let (device, _) = make_device(
            PrinterModel::Mini,
            vec![
                download_ack_packet(OP_DOWNLOAD_START, 0),
                download_ack_packet(OP_DATA, 2),
            ],
        )
        .await;
        let result = device
            .send_image_data(&[0u8; 100], &[vec![0u8; 100]], None)
            .await;
        assert!(result.is_err());
        assert!(result
            .unwrap_err()
            .to_string()
            .contains("data chunk 0 rejected"));
    }

    #[tokio::test]
    async fn download_end_rejected() {
        let (device, _) = make_device(
            PrinterModel::Mini,
            vec![
                download_ack_packet(OP_DOWNLOAD_START, 0),
                download_ack_packet(OP_DATA, 0),
                download_ack_packet(OP_DOWNLOAD_END, 3),
            ],
        )
        .await;
        let result = device
            .send_image_data(&[0u8; 100], &[vec![0u8; 100]], None)
            .await;
        assert!(result.is_err());
        assert!(result
            .unwrap_err()
            .to_string()
            .contains("download end rejected"));
    }

    #[tokio::test]
    async fn unexpected_response_in_download() {
        let (device, _) = make_device(PrinterModel::Mini, vec![battery_packet(50)]).await;
        let result = device
            .send_image_data(&[0u8; 100], &[vec![0u8; 100]], None)
            .await;
        assert!(result.is_err());
        assert!(result
            .unwrap_err()
            .to_string()
            .contains("expected DownloadAck"));
    }

    #[tokio::test]
    async fn transport_error_during_new() {
        let (transport, _) = MockTransport::new(vec![Err(InstaxError::Timeout)]);
        let result = BleInstaxDevice::new(transport, "Test".into()).await;
        assert!(result.is_err());
    }

    // ── Other ──────────────────────────────────────────────────────────────

    #[tokio::test]
    async fn progress_callback() {
        let jpeg_data = vec![0u8; 2700];
        let chunks = image::chunk_image_data(&jpeg_data, PrinterModel::Mini);
        assert_eq!(chunks.len(), 3);

        let progress_log = Arc::new(Mutex::new(Vec::new()));
        let log_clone = Arc::clone(&progress_log);

        let (device, _) = make_device(
            PrinterModel::Mini,
            vec![
                download_ack_packet(OP_DOWNLOAD_START, 0),
                download_ack_packet(OP_DATA, 0),
                download_ack_packet(OP_DATA, 0),
                download_ack_packet(OP_DATA, 0),
                download_ack_packet(OP_DOWNLOAD_END, 0),
            ],
        )
        .await;

        let cb = move |i: usize, total: usize| {
            log_clone.lock().unwrap().push((i, total));
        };

        device
            .send_image_data(&jpeg_data, &chunks, Some(&cb))
            .await
            .unwrap();

        let log = progress_log.lock().unwrap();
        assert_eq!(*log, vec![(1, 3), (2, 3), (3, 3)]);
    }

    #[tokio::test]
    async fn disconnect_delegates() {
        let (device, _) = make_device(PrinterModel::Mini, vec![]).await;
        device.disconnect().await.unwrap();
    }

    #[tokio::test]
    async fn name_stored_and_returned() {
        let (device, _) = make_device(PrinterModel::Mini, vec![]).await;
        assert_eq!(device.name(), "TestPrinter");
    }
}

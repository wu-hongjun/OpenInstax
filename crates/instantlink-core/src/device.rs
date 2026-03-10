//! Instax device abstraction and BLE-backed implementation.
//!
//! The [`PrinterDevice`] trait defines the async interface for interacting with
//! an Instax printer. [`BlePrinterDevice`] provides the btleplug-backed
//! implementation with ACK-based print flow.

use std::path::Path;
use std::time::Duration;

use async_trait::async_trait;

use crate::commands::{Command, Response};
use crate::connect_progress::{ConnectProgressCallback, ConnectStage, emit_connect_progress};
use crate::error::{PrinterError, Result};
use crate::image::{self, FitMode};
use crate::models::PrinterModel;
use crate::transport::{DEFAULT_TIMEOUT, Transport};

/// Printer status information.
#[derive(Debug, Clone)]
pub struct PrinterStatus {
    /// Battery level (0–100).
    pub battery: u8,
    /// Whether the printer is currently charging.
    pub is_charging: bool,
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
pub trait PrinterDevice: Send + Sync {
    /// Get the printer's current status.
    async fn status(&self) -> Result<PrinterStatus>;

    /// Get battery level (0–100).
    async fn battery(&self) -> Result<u8>;

    /// Get remaining film count and charging state.
    async fn film_and_charging(&self) -> Result<(u8, bool)>;

    /// Get remaining film count.
    async fn film_remaining(&self) -> Result<u8> {
        self.film_and_charging().await.map(|(f, _)| f)
    }

    /// Get whether the printer is charging.
    async fn is_charging(&self) -> Result<bool> {
        self.film_and_charging().await.map(|(_, c)| c)
    }

    /// Get total print count.
    async fn print_count(&self) -> Result<u16>;

    /// Get the detected printer model.
    fn model(&self) -> PrinterModel;

    /// Get the device name.
    fn name(&self) -> &str;

    /// Print an image from a file path.
    ///
    /// `print_option`: 0 = Rich mode (vivid), 1 = Natural mode (classic).
    /// Calls `progress` with (chunks_sent, total_chunks) during transfer.
    async fn print_file(
        &self,
        path: &Path,
        fit: FitMode,
        quality: u8,
        print_option: u8,
        progress: Option<&(dyn Fn(usize, usize) + Send + Sync)>,
    ) -> Result<()>;

    /// Print from raw image bytes.
    async fn print_bytes(
        &self,
        data: &[u8],
        fit: FitMode,
        quality: u8,
        print_option: u8,
        progress: Option<&(dyn Fn(usize, usize) + Send + Sync)>,
    ) -> Result<()>;

    /// Set the LED color and pattern.
    async fn set_led(&self, r: u8, g: u8, b: u8, pattern: u8) -> Result<()>;

    /// Turn off the LED.
    async fn led_off(&self) -> Result<()> {
        self.set_led(0, 0, 0, 0).await
    }

    /// Shut down the printer (power off).
    async fn shutdown(&self) -> Result<()>;

    /// Reset the printer.
    async fn reset(&self) -> Result<()>;

    /// Disconnect from the printer.
    async fn disconnect(&self) -> Result<()>;
}

/// BLE-backed Instax device.
pub struct BlePrinterDevice {
    transport: Box<dyn Transport>,
    model: PrinterModel,
    name: String,
}

impl BlePrinterDevice {
    /// Create a new BLE Instax device with a connected transport.
    ///
    /// Auto-detects the printer model by querying IMAGE_SUPPORT_INFO,
    /// using the optional DIS model number hint for Link 3 detection.
    pub async fn new(transport: Box<dyn Transport>, name: String) -> Result<Self> {
        Self::new_with_progress(transport, name, None).await
    }

    /// Create a new BLE Instax device and emit model-detection progress.
    pub async fn new_with_progress(
        transport: Box<dyn Transport>,
        name: String,
        progress: Option<&ConnectProgressCallback>,
    ) -> Result<Self> {
        let dis_model = transport.model_number_hint().map(|s| s.to_string());

        // Query image support info to detect model
        emit_connect_progress(progress, ConnectStage::ModelDetecting, None::<String>);
        let cmd = Command::ImageSupportInfo;
        let packet = transport
            .send_and_receive(&cmd.encode(), DEFAULT_TIMEOUT)
            .await?;
        let response = Response::decode(&packet);

        let model = match response {
            Response::ImageSupportInfo { width, height, .. } => {
                detect_model(width, height, dis_model.as_deref())?
            }
            _ => {
                return Err(PrinterError::UnexpectedResponse(
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

    /// Check if a status code indicates success for this printer model.
    fn is_success(&self, status: u8) -> bool {
        let spec = self.model.spec();
        status == 0 || status == spec.success_code
    }

    /// Check a response status code and return an appropriate error if not success.
    fn check_status(&self, status: u8, context: &str) -> Result<()> {
        if self.is_success(status) {
            return Ok(());
        }
        match status {
            178 => Err(PrinterError::NoFilm),
            179 => Err(PrinterError::CoverOpen),
            180 => Err(PrinterError::LowBattery { percent: 0 }),
            181 => Err(PrinterError::PrinterBusy),
            _ => Err(PrinterError::PrintRejected(format!(
                "{context} rejected with status {status}"
            ))),
        }
    }

    /// Send JPEG data to the printer with ACK-based flow control.
    async fn send_image_data(
        &self,
        jpeg_data: &[u8],
        chunks: &[Vec<u8>],
        print_option: u8,
        progress: Option<&(dyn Fn(usize, usize) + Send + Sync)>,
    ) -> Result<()> {
        let total = chunks.len();
        let delay_ms = self.model.spec().packet_delay_ms;

        // DOWNLOAD_START
        let start_resp = self
            .command(&Command::DownloadStart {
                image_size: jpeg_data.len() as u32,
                print_option,
            })
            .await?;
        match start_resp {
            Response::DownloadAck { status } if self.is_success(status) => {}
            Response::DownloadAck { status } => {
                return self.check_status(status, "download start");
            }
            other => {
                log::error!("DownloadStart got unexpected response: {other:?}");
                return Err(PrinterError::UnexpectedResponse(format!(
                    "expected DownloadAck for DownloadStart, got {other:?}"
                )));
            }
        }

        let transfer_result = async {
            // Send data chunks with ACK per chunk
            for (i, chunk) in chunks.iter().enumerate() {
                let data_resp = self
                    .command(&Command::Data {
                        index: i as u32,
                        data: chunk.clone(),
                    })
                    .await?;
                match data_resp {
                    Response::DownloadAck { status } if self.is_success(status) => {}
                    Response::DownloadAck { status } => {
                        return self.check_status(status, &format!("data chunk {i}"));
                    }
                    _ => {
                        return Err(PrinterError::UnexpectedResponse(
                            "expected DownloadAck for Data".into(),
                        ));
                    }
                }

                if let Some(cb) = progress {
                    cb(i + 1, total);
                }

                // Inter-packet delay (required for Link 3, Square, Wide)
                if delay_ms > 0 {
                    tokio::time::sleep(Duration::from_millis(delay_ms)).await;
                }
            }

            // DOWNLOAD_END
            let end_resp = self.command(&Command::DownloadEnd).await?;
            match end_resp {
                Response::DownloadAck { status } if self.is_success(status) => {}
                Response::DownloadAck { status } => {
                    return self.check_status(status, "download end");
                }
                _ => {
                    return Err(PrinterError::UnexpectedResponse(
                        "expected DownloadAck for DownloadEnd".into(),
                    ));
                }
            }
            Ok(())
        }
        .await;

        if transfer_result.is_err() {
            let _ = self.transport.send(&Command::DownloadCancel.encode()).await;
        }

        transfer_result
    }
}

#[async_trait]
impl PrinterDevice for BlePrinterDevice {
    async fn status(&self) -> Result<PrinterStatus> {
        // Pipeline: send all 3 queries back-to-back, then receive all 3 responses.
        // This overlaps BLE round-trips instead of waiting sequentially.
        self.transport
            .send(&Command::BatteryStatus.encode())
            .await?;
        self.transport
            .send(&Command::PrinterFunctionInfo.encode())
            .await?;
        self.transport.send(&Command::HistoryInfo.encode()).await?;

        let mut battery = None;
        let mut film_remaining = None;
        let mut is_charging = None;
        let mut print_count = None;

        let deadline = tokio::time::Instant::now() + STATUS_QUERY_TIMEOUT;

        while tokio::time::Instant::now() < deadline {
            let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
            let packet = self
                .transport
                .receive(remaining.min(STATUS_RESPONSE_SLICE_TIMEOUT))
                .await?;
            match Response::decode(&packet) {
                Response::BatteryStatus { level, .. } => battery = Some(level),
                Response::PrinterFunctionInfo {
                    film_remaining: f,
                    is_charging: c,
                } => {
                    film_remaining = Some(f);
                    is_charging = Some(c);
                }
                Response::HistoryInfo { count } => print_count = Some(count),
                other => {
                    log::warn!("ignoring spurious notification during status query: {other:?}");
                    continue;
                }
            }
            // Break early once we have all three responses.
            if battery.is_some() && film_remaining.is_some() && print_count.is_some() {
                break;
            }
        }

        Ok(PrinterStatus {
            battery: battery.ok_or_else(|| {
                PrinterError::UnexpectedResponse("missing battery response".into())
            })?,
            is_charging: is_charging.ok_or_else(|| {
                PrinterError::UnexpectedResponse("missing charging response".into())
            })?,
            film_remaining: film_remaining
                .ok_or_else(|| PrinterError::UnexpectedResponse("missing film response".into()))?,
            print_count: print_count.ok_or_else(|| {
                PrinterError::UnexpectedResponse("missing print count response".into())
            })?,
            model: self.model,
            name: self.name.clone(),
        })
    }

    async fn battery(&self) -> Result<u8> {
        match self.command(&Command::BatteryStatus).await? {
            Response::BatteryStatus { state, level } => {
                log::debug!("battery raw: state={state}, level={level}");
                Ok(level)
            }
            _ => Err(PrinterError::UnexpectedResponse(
                "expected BatteryStatus".into(),
            )),
        }
    }

    async fn film_and_charging(&self) -> Result<(u8, bool)> {
        match self.command(&Command::PrinterFunctionInfo).await? {
            Response::PrinterFunctionInfo {
                film_remaining,
                is_charging,
            } => Ok((film_remaining, is_charging)),
            _ => Err(PrinterError::UnexpectedResponse(
                "expected PrinterFunctionInfo".into(),
            )),
        }
    }

    async fn print_count(&self) -> Result<u16> {
        match self.command(&Command::HistoryInfo).await? {
            Response::HistoryInfo { count } => Ok(count),
            _ => Err(PrinterError::UnexpectedResponse(
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
        print_option: u8,
        progress: Option<&(dyn Fn(usize, usize) + Send + Sync)>,
    ) -> Result<()> {
        let (jpeg_data, chunks) = image::prepare_image(path, self.model, fit, quality)?;
        self.send_image_data(&jpeg_data, &chunks, print_option, progress)
            .await?;

        // Pre-execute delay (critical for Link 3 and Square)
        let pre_delay = self.model.spec().pre_execute_delay_ms;
        if pre_delay > 0 {
            log::debug!("Waiting {}ms before print execute", pre_delay);
            tokio::time::sleep(Duration::from_millis(pre_delay)).await;
        }

        // Trigger print
        match self.command(&Command::PrintImage).await? {
            Response::PrintStatus { status } if self.is_success(status) => Ok(()),
            Response::PrintStatus { status } => self.check_status(status, "print"),
            _ => Err(PrinterError::UnexpectedResponse(
                "expected PrintStatus".into(),
            )),
        }
    }

    async fn print_bytes(
        &self,
        data: &[u8],
        fit: FitMode,
        quality: u8,
        print_option: u8,
        progress: Option<&(dyn Fn(usize, usize) + Send + Sync)>,
    ) -> Result<()> {
        let (jpeg_data, chunks) = image::prepare_image_from_bytes(data, self.model, fit, quality)?;
        self.send_image_data(&jpeg_data, &chunks, print_option, progress)
            .await?;

        // Pre-execute delay (critical for Link 3 and Square)
        let pre_delay = self.model.spec().pre_execute_delay_ms;
        if pre_delay > 0 {
            log::debug!("Waiting {}ms before print execute", pre_delay);
            tokio::time::sleep(Duration::from_millis(pre_delay)).await;
        }

        match self.command(&Command::PrintImage).await? {
            Response::PrintStatus { status } if self.is_success(status) => Ok(()),
            Response::PrintStatus { status } => self.check_status(status, "print"),
            _ => Err(PrinterError::UnexpectedResponse(
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
            _ => Err(PrinterError::UnexpectedResponse("expected LedAck".into())),
        }
    }

    async fn shutdown(&self) -> Result<()> {
        self.transport.send(&Command::Shutdown.encode()).await?;
        // Printer powers off; no response expected
        Ok(())
    }

    async fn reset(&self) -> Result<()> {
        self.transport.send(&Command::Reset.encode()).await?;
        // Printer resets; no response expected
        Ok(())
    }

    async fn disconnect(&self) -> Result<()> {
        self.transport.disconnect().await
    }
}

/// Detect the printer model from image support dimensions and optional DIS model string.
fn detect_model(width: u16, height: u16, dis_model: Option<&str>) -> Result<PrinterModel> {
    // Check DIS model string first for Link 3 detection
    if let Some(model_str) = dis_model
        && model_str.contains("FI033")
    {
        return Ok(PrinterModel::MiniLink3);
    }

    // Fall back to dimension matching (skip MiniLink3 since it shares Mini's dimensions)
    match (width as u32, height as u32) {
        (600, 800) => Ok(PrinterModel::Mini),
        (800, 800) => Ok(PrinterModel::Square),
        (1260, 840) => Ok(PrinterModel::Wide),
        _ => Err(PrinterError::UnexpectedResponse(format!(
            "unknown printer dimensions: {width}x{height}"
        ))),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::commands::{
        INFO_BATTERY, INFO_IMAGE_SUPPORT, INFO_PRINT_HISTORY, INFO_PRINTER_FUNCTION, OP_DATA,
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
        dis_model: Option<String>,
    }

    impl MockTransport {
        fn new(
            responses: Vec<Result<protocol::Packet>>,
        ) -> (Box<dyn Transport>, Arc<Mutex<MockState>>) {
            Self::new_with_dis(responses, None)
        }

        fn new_with_dis(
            responses: Vec<Result<protocol::Packet>>,
            dis_model: Option<String>,
        ) -> (Box<dyn Transport>, Arc<Mutex<MockState>>) {
            let state = Arc::new(Mutex::new(MockState {
                responses: responses.into(),
                sent: Vec::new(),
            }));
            let transport = Box::new(MockTransport {
                state: Arc::clone(&state),
                dis_model,
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
                .unwrap_or(Err(PrinterError::Timeout))
        }

        async fn disconnect(&self) -> Result<()> {
            Ok(())
        }

        fn model_number_hint(&self) -> Option<&str> {
            self.dis_model.as_deref()
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
    ) -> (BlePrinterDevice, Arc<Mutex<MockState>>) {
        make_device_with_dis(model, extra_responses, None).await
    }

    async fn make_device_with_dis(
        model: PrinterModel,
        extra_responses: Vec<Result<protocol::Packet>>,
        dis_model: Option<String>,
    ) -> (BlePrinterDevice, Arc<Mutex<MockState>>) {
        let mut responses = vec![image_support_info_packet(model)];
        responses.extend(extra_responses);
        let (transport, state) = MockTransport::new_with_dis(responses, dis_model);
        let device = BlePrinterDevice::new(transport, "TestPrinter".into())
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
    async fn detect_model_link3_via_dis() {
        // Link 3 reports same dimensions as Mini (600x800) but DIS contains "FI033"
        let (device, _) =
            make_device_with_dis(PrinterModel::Mini, vec![], Some("FI033".into())).await;
        assert_eq!(device.model(), PrinterModel::MiniLink3);
    }

    #[tokio::test]
    async fn detect_model_mini_without_dis() {
        // Without DIS, 600x800 should fall back to Mini (not Link 3)
        let (device, _) = make_device_with_dis(PrinterModel::Mini, vec![], None).await;
        assert_eq!(device.model(), PrinterModel::Mini);
    }

    #[tokio::test]
    async fn detect_model_unknown() {
        // Unknown dimensions: 999x999
        let mut data = Vec::new();
        data.extend_from_slice(&999u16.to_be_bytes());
        data.extend_from_slice(&999u16.to_be_bytes());
        let packet = support_function_packet(INFO_IMAGE_SUPPORT, &data);
        let (transport, _) = MockTransport::new(vec![packet]);
        let Err(err) = BlePrinterDevice::new(transport, "Test".into()).await else {
            panic!("expected error");
        };
        assert!(err.to_string().contains("unknown printer dimensions"));
    }

    #[tokio::test]
    async fn detect_model_wrong_response() {
        let (transport, _) = MockTransport::new(vec![battery_packet(50)]);
        let Err(err) = BlePrinterDevice::new(transport, "Test".into()).await else {
            panic!("expected error");
        };
        assert!(err.to_string().contains("expected ImageSupportInfo"));
    }

    // ── Success Code Handling ─────────────────────────────────────────────

    #[tokio::test]
    async fn is_print_success_mini() {
        let (device, _) = make_device(PrinterModel::Mini, vec![]).await;
        assert!(device.is_success(0));
        assert!(!device.is_success(12));
    }

    #[tokio::test]
    async fn is_print_success_square() {
        let (device, _) = make_device(PrinterModel::Square, vec![]).await;
        assert!(device.is_success(0));
        assert!(device.is_success(12)); // Square-specific success
        assert!(!device.is_success(15));
    }

    #[tokio::test]
    async fn is_print_success_wide() {
        let (device, _) = make_device(PrinterModel::Wide, vec![]).await;
        assert!(device.is_success(0));
        assert!(device.is_success(15)); // Wide-specific success
        assert!(!device.is_success(12));
    }

    // ── Error Code Mapping ────────────────────────────────────────────────

    #[tokio::test]
    async fn check_status_no_film() {
        let (device, _) = make_device(PrinterModel::Mini, vec![]).await;
        let err = device.check_status(178, "test").unwrap_err();
        assert!(matches!(err, PrinterError::NoFilm));
    }

    #[tokio::test]
    async fn check_status_cover_open() {
        let (device, _) = make_device(PrinterModel::Mini, vec![]).await;
        let err = device.check_status(179, "test").unwrap_err();
        assert!(matches!(err, PrinterError::CoverOpen));
    }

    #[tokio::test]
    async fn check_status_low_battery() {
        let (device, _) = make_device(PrinterModel::Mini, vec![]).await;
        let err = device.check_status(180, "test").unwrap_err();
        assert!(matches!(err, PrinterError::LowBattery { .. }));
    }

    #[tokio::test]
    async fn check_status_busy() {
        let (device, _) = make_device(PrinterModel::Mini, vec![]).await;
        let err = device.check_status(181, "test").unwrap_err();
        assert!(matches!(err, PrinterError::PrinterBusy));
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
        assert!(!status.is_charging);
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
        assert!(
            result
                .unwrap_err()
                .to_string()
                .contains("expected BatteryStatus")
        );
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
        // [when=0, count=1, speed=1, repeat=255, B=0, G=0, R=0]
        assert_eq!(led_packet.payload, vec![0, 1, 1, 255, 0, 0, 0]);
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
            .send_image_data(&jpeg_data, &chunks, 0, None)
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
            .send_image_data(&jpeg_data, &chunks, 0, None)
            .await
            .unwrap();

        let sent = &state.lock().unwrap().sent;
        // sent[0]=ImageSupportInfo, sent[1]=DownloadStart, sent[2..5]=Data, sent[5]=DownloadEnd

        let pkt0 = protocol::parse_packet(&sent[2]).unwrap();
        assert_eq!(pkt0.opcode, OP_DATA);
        let index0 = u32::from_be_bytes(pkt0.payload[0..4].try_into().unwrap());
        assert_eq!(index0, 0);

        let pkt1 = protocol::parse_packet(&sent[3]).unwrap();
        let index1 = u32::from_be_bytes(pkt1.payload[0..4].try_into().unwrap());
        assert_eq!(index1, 1);

        let pkt2 = protocol::parse_packet(&sent[4]).unwrap();
        let index2 = u32::from_be_bytes(pkt2.payload[0..4].try_into().unwrap());
        assert_eq!(index2, 2);
    }

    #[tokio::test]
    async fn send_image_data_with_model_success_code() {
        // Square returns success code 12 in ACKs
        let jpeg_data = vec![0u8; 100];
        let chunks = vec![jpeg_data.clone()];
        let (device, _) = make_device(
            PrinterModel::Square,
            vec![
                download_ack_packet(OP_DOWNLOAD_START, 12),
                download_ack_packet(OP_DATA, 12),
                download_ack_packet(OP_DOWNLOAD_END, 12),
            ],
        )
        .await;
        device
            .send_image_data(&jpeg_data, &chunks, 0, None)
            .await
            .unwrap();
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
            .send_image_data(&[0u8; 100], &[vec![0u8; 100]], 0, None)
            .await;
        assert!(result.is_err());
        assert!(
            result
                .unwrap_err()
                .to_string()
                .contains("download start rejected")
        );
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
            .send_image_data(&[0u8; 100], &[vec![0u8; 100]], 0, None)
            .await;
        assert!(result.is_err());
        assert!(
            result
                .unwrap_err()
                .to_string()
                .contains("data chunk 0 rejected")
        );
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
            .send_image_data(&[0u8; 100], &[vec![0u8; 100]], 0, None)
            .await;
        assert!(result.is_err());
        assert!(
            result
                .unwrap_err()
                .to_string()
                .contains("download end rejected")
        );
    }

    #[tokio::test]
    async fn download_no_film_error() {
        // Status 178 = no film
        let (device, _) = make_device(
            PrinterModel::Mini,
            vec![download_ack_packet(OP_DOWNLOAD_START, 178)],
        )
        .await;
        let result = device
            .send_image_data(&[0u8; 100], &[vec![0u8; 100]], 0, None)
            .await;
        assert!(matches!(result.unwrap_err(), PrinterError::NoFilm));
    }

    #[tokio::test]
    async fn unexpected_response_in_download() {
        let (device, _) = make_device(PrinterModel::Mini, vec![battery_packet(50)]).await;
        let result = device
            .send_image_data(&[0u8; 100], &[vec![0u8; 100]], 0, None)
            .await;
        assert!(result.is_err());
        assert!(
            result
                .unwrap_err()
                .to_string()
                .contains("expected DownloadAck")
        );
    }

    #[tokio::test]
    async fn transport_error_during_new() {
        let (transport, _) = MockTransport::new(vec![Err(PrinterError::Timeout)]);
        let result = BlePrinterDevice::new(transport, "Test".into()).await;
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
            .send_image_data(&jpeg_data, &chunks, 0, Some(&cb))
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
const STATUS_QUERY_TIMEOUT: Duration = Duration::from_secs(4);
const STATUS_RESPONSE_SLICE_TIMEOUT: Duration = Duration::from_millis(1500);

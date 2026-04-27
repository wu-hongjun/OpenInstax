//! BLE GATT transport layer using btleplug.
//!
//! Handles service/characteristic discovery, notification subscription,
//! send/receive with timeouts, and MTU fragmentation.

use std::time::Duration;

use async_trait::async_trait;
use btleplug::api::{
    Central, Characteristic, Manager as _, Peripheral as _, ScanFilter, WriteType,
};
use btleplug::platform::{Adapter, Manager, Peripheral};
use futures::StreamExt;
use tokio::sync::{Mutex, mpsc};
use uuid::Uuid;

use crate::connect_progress::{ConnectProgressCallback, ConnectStage, emit_connect_progress};
use crate::error::{PrinterError, Result};
use crate::protocol::{self, PacketAssembler};

async fn receive_packet_from_channel(
    rx: &mut mpsc::Receiver<Vec<u8>>,
    assembler: &mut PacketAssembler,
    timeout: Duration,
) -> Result<protocol::Packet> {
    let deadline = tokio::time::Instant::now() + timeout;

    loop {
        match assembler.feed(&[]) {
            Ok(Some(packet)) => return Ok(packet),
            Ok(None) => {}
            Err(e) => log::warn!("protocol error (buffered data): {e}"),
        }

        let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
        if remaining.is_zero() {
            return Err(PrinterError::Timeout);
        }

        let data = tokio::time::timeout(remaining, rx.recv())
            .await
            .map_err(|_| PrinterError::Timeout)?
            .ok_or_else(|| PrinterError::Ble("notification channel closed".into()))?;

        match assembler.feed(&data) {
            Ok(Some(packet)) => return Ok(packet),
            Ok(None) => {}
            Err(e) => log::warn!("protocol error (incoming data): {e}"),
        }
    }
}

/// Instax BLE service UUID.
pub const SERVICE_UUID: Uuid = Uuid::from_u128(0x70954782_2d83_473d_9e5f_81e1d02d5273);
/// Instax BLE write characteristic UUID.
pub const WRITE_CHAR_UUID: Uuid = Uuid::from_u128(0x70954783_2d83_473d_9e5f_81e1d02d5273);
/// Instax BLE notify characteristic UUID.
pub const NOTIFY_CHAR_UUID: Uuid = Uuid::from_u128(0x70954784_2d83_473d_9e5f_81e1d02d5273);

/// Standard BLE Device Information Service UUID.
pub const DIS_SERVICE_UUID: Uuid = Uuid::from_u128(0x0000180a_0000_1000_8000_00805f9b34fb);
/// Standard BLE DIS Model Number characteristic UUID.
pub const DIS_MODEL_NUMBER_UUID: Uuid = Uuid::from_u128(0x00002a24_0000_1000_8000_00805f9b34fb);

/// Default scan duration.
pub const DEFAULT_SCAN_DURATION: Duration = Duration::from_secs(5);
/// Default command timeout.
pub const DEFAULT_TIMEOUT: Duration = Duration::from_secs(10);
/// Timeout for individual BLE write operations.
pub const DEFAULT_WRITE_TIMEOUT: Duration = Duration::from_secs(2);
/// Timeout for BLE disconnect/cleanup operations.
pub const DEFAULT_DISCONNECT_TIMEOUT: Duration = Duration::from_secs(2);

/// Trait for BLE transport operations (enables mocking in tests).
#[async_trait]
pub trait Transport: Send + Sync {
    /// Send raw bytes to the printer's write characteristic.
    async fn send(&self, data: &[u8]) -> Result<()>;

    /// Receive the next complete protocol packet, with timeout.
    async fn receive(&self, timeout: Duration) -> Result<protocol::Packet>;

    /// Send a command packet and wait for the response.
    async fn send_and_receive(&self, data: &[u8], timeout: Duration) -> Result<protocol::Packet>;

    /// Disconnect from the printer.
    async fn disconnect(&self) -> Result<()>;

    /// Optional DIS Model Number hint for printer model detection.
    /// Returns `None` if DIS was not available or not read.
    fn model_number_hint(&self) -> Option<&str> {
        None
    }
}

/// Get the default BLE adapter.
pub async fn get_adapter() -> Result<Adapter> {
    let manager = Manager::new()
        .await
        .map_err(|e| PrinterError::Ble(format!("failed to create BLE manager: {e}")))?;
    let adapters = manager
        .adapters()
        .await
        .map_err(|e| PrinterError::Ble(format!("failed to list BLE adapters: {e}")))?;
    adapters
        .into_iter()
        .next()
        .ok_or_else(|| PrinterError::Ble("no BLE adapter found".into()))
}

/// Scan for Instax printers.
///
/// Returns a list of `(peripheral, local_name)` pairs for nearby Instax devices.
/// Uses name-based matching ("INSTAX") because some printers don't advertise
/// the service UUID until after connection.
pub async fn scan(adapter: &Adapter, duration: Duration) -> Result<Vec<(Peripheral, String)>> {
    // Scan without service UUID filter — some Instax printers don't advertise
    // the service UUID in their BLE advertisements, only exposing it after
    // connection during service discovery.
    adapter
        .start_scan(ScanFilter::default())
        .await
        .map_err(|e| PrinterError::Ble(format!("scan failed: {e}")))?;

    tokio::time::sleep(duration).await;

    adapter
        .stop_scan()
        .await
        .map_err(|e| PrinterError::Ble(format!("stop scan failed: {e}")))?;

    let peripherals = adapter
        .peripherals()
        .await
        .map_err(|e| PrinterError::Ble(format!("failed to list peripherals: {e}")))?;

    let mut results = Vec::new();
    for p in peripherals {
        if let Ok(Some(props)) = p.properties().await {
            let matches_name = props
                .local_name
                .as_deref()
                .is_some_and(|name| name.starts_with("INSTAX"));
            let matches_service = props.services.contains(&SERVICE_UUID);

            if matches_name || matches_service {
                let display_name = props
                    .local_name
                    .clone()
                    .unwrap_or_else(|| p.id().to_string());
                results.push((p, display_name));
            }
        }
    }

    Ok(results)
}

/// Real BLE transport backed by btleplug.
pub struct BleTransport {
    peripheral: Peripheral,
    write_char: Characteristic,
    notify_char: Characteristic,
    rx: tokio::sync::Mutex<mpsc::Receiver<Vec<u8>>>,
    assembler: tokio::sync::Mutex<PacketAssembler>,
    command_lock: Mutex<()>,
    /// DIS Model Number string, if available.
    dis_model_number: Option<String>,
}

impl BleTransport {
    /// Connect to a peripheral and set up characteristics and notifications.
    pub async fn connect(peripheral: Peripheral) -> Result<Self> {
        Self::connect_with_progress(peripheral, None).await
    }

    /// Connect to a peripheral and emit setup stages as progress events.
    pub async fn connect_with_progress(
        peripheral: Peripheral,
        progress: Option<&ConnectProgressCallback>,
    ) -> Result<Self> {
        if peripheral.is_connected().await.unwrap_or(false) {
            Self::disconnect_quietly(&peripheral, None).await;
        }

        emit_connect_progress(progress, ConnectStage::BleConnecting, None::<String>);
        peripheral
            .connect()
            .await
            .map_err(|e| PrinterError::Ble(format!("connect failed: {e}")))?;

        emit_connect_progress(progress, ConnectStage::ServiceDiscovery, None::<String>);
        if let Err(e) = peripheral.discover_services().await {
            Self::disconnect_quietly(&peripheral, None).await;
            return Err(PrinterError::Ble(format!("service discovery failed: {e}")));
        }

        let chars = peripheral.characteristics();
        emit_connect_progress(progress, ConnectStage::CharacteristicLookup, None::<String>);

        let write_char = match chars.iter().find(|c| c.uuid == WRITE_CHAR_UUID).cloned() {
            Some(characteristic) => characteristic,
            None => {
                Self::disconnect_quietly(&peripheral, None).await;
                return Err(PrinterError::Ble("write characteristic not found".into()));
            }
        };

        let notify_char = match chars.iter().find(|c| c.uuid == NOTIFY_CHAR_UUID).cloned() {
            Some(characteristic) => characteristic,
            None => {
                Self::disconnect_quietly(&peripheral, None).await;
                return Err(PrinterError::Ble("notify characteristic not found".into()));
            }
        };

        // Try to read DIS Model Number characteristic for Link 3 detection
        let dis_model_number = Self::read_dis_model_number(&peripheral, &chars).await;
        if let Some(ref model) = dis_model_number {
            log::debug!("DIS Model Number: {}", model);
        }

        // Subscribe to notifications BEFORE spawning the listener task
        // so that a failed subscribe() doesn't leak a spawned task.
        emit_connect_progress(
            progress,
            ConnectStage::NotificationSubscribe,
            None::<String>,
        );
        if let Err(e) = peripheral.subscribe(&notify_char).await {
            Self::disconnect_quietly(&peripheral, Some(&notify_char)).await;
            return Err(PrinterError::Ble(format!(
                "notification subscribe failed: {e}"
            )));
        }

        let (tx, rx) = mpsc::channel(64);
        let mut notification_stream = match peripheral.notifications().await {
            Ok(stream) => stream,
            Err(e) => {
                Self::disconnect_quietly(&peripheral, Some(&notify_char)).await;
                return Err(PrinterError::Ble(format!(
                    "notification stream failed: {e}"
                )));
            }
        };

        tokio::spawn(async move {
            log::debug!("Notification listener task started");
            while let Some(notification) = notification_stream.next().await {
                log::debug!(
                    "Got notification: {} bytes: {:02x?}",
                    notification.value.len(),
                    &notification.value[..notification.value.len().min(20)]
                );
                if tx.send(notification.value).await.is_err() {
                    log::debug!("Notification channel closed");
                    break;
                }
            }
            log::debug!("Notification stream ended");
        });

        // Brief delay to let the BLE connection stabilize
        tokio::time::sleep(Duration::from_millis(200)).await;

        Ok(Self {
            peripheral,
            write_char,
            notify_char,
            rx: tokio::sync::Mutex::new(rx),
            assembler: tokio::sync::Mutex::new(PacketAssembler::new()),
            command_lock: Mutex::new(()),
            dis_model_number,
        })
    }

    /// Try to read the DIS Model Number characteristic from the peripheral.
    async fn read_dis_model_number(
        peripheral: &Peripheral,
        chars: &std::collections::BTreeSet<Characteristic>,
    ) -> Option<String> {
        let dis_char = chars.iter().find(|c| c.uuid == DIS_MODEL_NUMBER_UUID)?;
        match peripheral.read(dis_char).await {
            Ok(data) => {
                let s = String::from_utf8_lossy(&data).trim().to_string();
                if s.is_empty() { None } else { Some(s) }
            }
            Err(e) => {
                log::debug!("Failed to read DIS Model Number: {e}");
                None
            }
        }
    }

    async fn disconnect_quietly(peripheral: &Peripheral, notify_char: Option<&Characteristic>) {
        if let Some(notify_char) = notify_char {
            let _ = tokio::time::timeout(
                DEFAULT_DISCONNECT_TIMEOUT,
                peripheral.unsubscribe(notify_char),
            )
            .await;
        }
        let is_connected =
            tokio::time::timeout(DEFAULT_DISCONNECT_TIMEOUT, peripheral.is_connected())
                .await
                .ok()
                .and_then(|result| result.ok())
                .unwrap_or(false);
        if is_connected {
            let _ = tokio::time::timeout(DEFAULT_DISCONNECT_TIMEOUT, peripheral.disconnect()).await;
            tokio::time::sleep(Duration::from_millis(250)).await;
        }
    }
}

#[async_trait]
impl Transport for BleTransport {
    async fn send(&self, data: &[u8]) -> Result<()> {
        log::debug!(
            "Sending {} bytes: {:02x?}",
            data.len(),
            &data[..data.len().min(20)]
        );
        // Fragment into MTU-sized sub-packets
        let fragments = protocol::fragment(data);
        for frag in fragments {
            tokio::time::timeout(
                DEFAULT_WRITE_TIMEOUT,
                self.peripheral
                    .write(&self.write_char, &frag, WriteType::WithoutResponse),
            )
            .await
            .map_err(|_| PrinterError::Timeout)?
            .map_err(|e| PrinterError::Ble(format!("write failed: {e}")))?;
        }
        Ok(())
    }

    async fn receive(&self, timeout: Duration) -> Result<protocol::Packet> {
        let mut rx = self.rx.lock().await;
        let mut assembler = self.assembler.lock().await;
        receive_packet_from_channel(&mut rx, &mut assembler, timeout).await
    }

    async fn send_and_receive(&self, data: &[u8], timeout: Duration) -> Result<protocol::Packet> {
        let _guard = self.command_lock.lock().await;
        self.send(data).await?;
        self.receive(timeout).await
    }

    async fn disconnect(&self) -> Result<()> {
        let _ = tokio::time::timeout(
            DEFAULT_DISCONNECT_TIMEOUT,
            self.peripheral.unsubscribe(&self.notify_char),
        )
        .await;
        let is_connected =
            tokio::time::timeout(DEFAULT_DISCONNECT_TIMEOUT, self.peripheral.is_connected())
                .await
                .map_err(|_| PrinterError::Timeout)?
                .map_err(|e| PrinterError::Ble(format!("is_connected failed: {e}")))?;
        if is_connected {
            tokio::time::timeout(DEFAULT_DISCONNECT_TIMEOUT, self.peripheral.disconnect())
                .await
                .map_err(|_| PrinterError::Timeout)?
                .map_err(|e| PrinterError::Ble(format!("disconnect failed: {e}")))?;
            tokio::time::sleep(Duration::from_millis(250)).await;
        }
        Ok(())
    }

    fn model_number_hint(&self) -> Option<&str> {
        self.dis_model_number.as_deref()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test(start_paused = true)]
    async fn receive_packet_from_channel_uses_total_deadline_across_fragments() {
        let (tx, mut rx) = mpsc::channel(4);
        let mut assembler = PacketAssembler::new();
        let packet = protocol::build_packet(0x1234, &[0xAB; 176]);
        let fragments = protocol::fragment(&packet);
        assert!(
            fragments.len() > 1,
            "test packet must span multiple fragments"
        );

        let sender = tokio::spawn(async move {
            tx.send(fragments[0].clone()).await.unwrap();
            tokio::time::sleep(Duration::from_millis(40)).await;
            tx.send(fragments[1].clone()).await.unwrap();
        });

        let start = tokio::time::Instant::now();
        let err = receive_packet_from_channel(&mut rx, &mut assembler, Duration::from_millis(25))
            .await
            .unwrap_err();
        sender.await.unwrap();

        assert!(matches!(err, PrinterError::Timeout));
        assert!(start.elapsed() < Duration::from_millis(60));
    }

    #[tokio::test]
    async fn receive_packet_from_channel_returns_packet_when_fragments_arrive_in_time() {
        let (tx, mut rx) = mpsc::channel(4);
        let mut assembler = PacketAssembler::new();
        let packet = protocol::build_packet(0x1234, &[0xAA, 0xBB]);
        let fragments = protocol::fragment(&packet);

        let sender = tokio::spawn(async move {
            for fragment in fragments {
                tx.send(fragment).await.unwrap();
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        });

        let received =
            receive_packet_from_channel(&mut rx, &mut assembler, Duration::from_millis(100))
                .await
                .unwrap();
        sender.await.unwrap();

        assert_eq!(received.opcode, 0x1234);
        assert_eq!(received.payload, vec![0xAA, 0xBB]);
    }

    #[tokio::test]
    async fn receive_packet_from_channel_returns_buffered_packet_without_waiting() {
        let (_tx, mut rx) = mpsc::channel(1);
        let first = protocol::build_packet(0x1111, &[0x00]);
        let second = protocol::build_packet(0x4321, &[0x01, 0x02, 0x03]);
        let mut assembler = PacketAssembler::new();
        let mut combined = first;
        combined.extend_from_slice(&second);
        let initial = assembler
            .feed(&combined)
            .expect("should not error")
            .unwrap();
        assert_eq!(initial.opcode, 0x1111);

        let received =
            receive_packet_from_channel(&mut rx, &mut assembler, Duration::from_millis(10))
                .await
                .unwrap();

        assert_eq!(received.opcode, 0x4321);
        assert_eq!(received.payload, vec![0x01, 0x02, 0x03]);
    }

    #[tokio::test]
    async fn receive_packet_from_channel_errors_when_notification_channel_closes() {
        let (tx, mut rx) = mpsc::channel::<Vec<u8>>(1);
        let mut assembler = PacketAssembler::new();
        drop(tx);

        let err = receive_packet_from_channel(&mut rx, &mut assembler, Duration::from_millis(20))
            .await
            .unwrap_err();

        assert!(
            matches!(err, PrinterError::Ble(message) if message == "notification channel closed")
        );
    }

    /// Corrupt data (bad header) must not abort the receive loop — the next
    /// valid packet should still arrive.
    #[tokio::test]
    async fn receive_packet_from_channel_logs_corruption_then_resumes() {
        let (tx, mut rx) = mpsc::channel(8);
        let mut assembler = PacketAssembler::new();

        // Corrupt fragment: invalid header bytes followed by a valid packet.
        let corrupt: Vec<u8> = vec![0xDE, 0xAD, 0x00, 0x07, 0x00, 0x00, 0x00];
        let valid = protocol::build_packet(0xCAFE, &[0x42]);

        let sender = tokio::spawn(async move {
            tx.send(corrupt).await.unwrap();
            tokio::time::sleep(Duration::from_millis(5)).await;
            tx.send(valid).await.unwrap();
        });

        let received =
            receive_packet_from_channel(&mut rx, &mut assembler, Duration::from_millis(200))
                .await
                .expect("should receive valid packet after corruption");
        sender.await.unwrap();

        assert_eq!(received.opcode, 0xCAFE);
        assert_eq!(received.payload, vec![0x42]);
    }

    /// Persistent corruption with no valid packet in sight must ultimately
    /// time out rather than loop forever.
    #[tokio::test(start_paused = true)]
    async fn receive_packet_from_channel_surfaces_persistent_corruption() {
        let (tx, mut rx) = mpsc::channel(8);
        let mut assembler = PacketAssembler::new();

        // Keep sending corrupt data; never send a valid packet.
        let sender = tokio::spawn(async move {
            for _ in 0..5u8 {
                tx.send(vec![0xDE, 0xAD, 0x00, 0x07]).await.unwrap();
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        });

        let err = receive_packet_from_channel(&mut rx, &mut assembler, Duration::from_millis(10))
            .await
            .unwrap_err();
        sender.await.unwrap();

        assert!(matches!(err, PrinterError::Timeout));
    }
}

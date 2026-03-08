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
use tokio::sync::mpsc;
use uuid::Uuid;

use crate::error::{PrinterError, Result};
use crate::protocol::{self, PacketAssembler};

/// Instax BLE service UUID.
pub const SERVICE_UUID: Uuid = Uuid::from_u128(0x70954782_2d83_473d_9e5f_81e1d02d5273);
/// Instax BLE write characteristic UUID.
pub const WRITE_CHAR_UUID: Uuid = Uuid::from_u128(0x70954783_2d83_473d_9e5f_81e1d02d5273);
/// Instax BLE notify characteristic UUID.
pub const NOTIFY_CHAR_UUID: Uuid = Uuid::from_u128(0x70954784_2d83_473d_9e5f_81e1d02d5273);

/// Default scan duration.
pub const DEFAULT_SCAN_DURATION: Duration = Duration::from_secs(5);
/// Default command timeout.
pub const DEFAULT_TIMEOUT: Duration = Duration::from_secs(10);

/// Trait for BLE transport operations (enables mocking in tests).
#[async_trait]
pub trait Transport: Send + Sync {
    /// Send raw bytes to the printer's write characteristic.
    async fn send(&self, data: &[u8]) -> Result<()>;

    /// Receive the next complete protocol packet, with timeout.
    async fn receive(&self, timeout: Duration) -> Result<protocol::Packet>;

    /// Send a command packet and wait for the response.
    async fn send_and_receive(&self, data: &[u8], timeout: Duration) -> Result<protocol::Packet> {
        self.send(data).await?;
        self.receive(timeout).await
    }

    /// Disconnect from the printer.
    async fn disconnect(&self) -> Result<()>;
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
            if let Some(ref name) = props.local_name {
                // Match by name prefix or by advertised service UUID
                if name.starts_with("INSTAX") || props.services.contains(&SERVICE_UUID) {
                    results.push((p, name.clone()));
                }
            }
        }
    }

    Ok(results)
}

/// Real BLE transport backed by btleplug.
pub struct BleTransport {
    peripheral: Peripheral,
    write_char: Characteristic,
    rx: tokio::sync::Mutex<mpsc::Receiver<Vec<u8>>>,
}

impl BleTransport {
    /// Connect to a peripheral and set up characteristics and notifications.
    pub async fn connect(peripheral: Peripheral) -> Result<Self> {
        peripheral
            .connect()
            .await
            .map_err(|e| PrinterError::Ble(format!("connect failed: {e}")))?;

        peripheral
            .discover_services()
            .await
            .map_err(|e| PrinterError::Ble(format!("service discovery failed: {e}")))?;

        let chars = peripheral.characteristics();

        let write_char = chars
            .iter()
            .find(|c| c.uuid == WRITE_CHAR_UUID)
            .cloned()
            .ok_or_else(|| PrinterError::Ble("write characteristic not found".into()))?;

        let notify_char = chars
            .iter()
            .find(|c| c.uuid == NOTIFY_CHAR_UUID)
            .cloned()
            .ok_or_else(|| PrinterError::Ble("notify characteristic not found".into()))?;

        // Set up notification channel BEFORE subscribing to avoid race condition
        // where early notifications are lost.
        let (tx, rx) = mpsc::channel(64);
        let mut notification_stream = peripheral
            .notifications()
            .await
            .map_err(|e| PrinterError::Ble(format!("notification stream failed: {e}")))?;

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

        // Now subscribe to notifications
        peripheral
            .subscribe(&notify_char)
            .await
            .map_err(|e| PrinterError::Ble(format!("notification subscribe failed: {e}")))?;

        // Brief delay to let the BLE connection stabilize
        tokio::time::sleep(Duration::from_millis(200)).await;

        Ok(Self {
            peripheral,
            write_char,
            rx: tokio::sync::Mutex::new(rx),
        })
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
            self.peripheral
                .write(&self.write_char, &frag, WriteType::WithoutResponse)
                .await
                .map_err(|e| PrinterError::Ble(format!("write failed: {e}")))?;
        }
        Ok(())
    }

    async fn receive(&self, timeout: Duration) -> Result<protocol::Packet> {
        let mut rx = self.rx.lock().await;
        let mut assembler = PacketAssembler::new();

        loop {
            let data = tokio::time::timeout(timeout, rx.recv())
                .await
                .map_err(|_| PrinterError::Timeout)?
                .ok_or_else(|| PrinterError::Ble("notification channel closed".into()))?;

            if let Some(packet) = assembler.feed(&data) {
                return Ok(packet);
            }
        }
    }

    async fn disconnect(&self) -> Result<()> {
        self.peripheral
            .disconnect()
            .await
            .map_err(|e| PrinterError::Ble(format!("disconnect failed: {e}")))?;
        Ok(())
    }
}

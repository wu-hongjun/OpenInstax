//! High-level API for scanning, connecting, and printing with Instax printers.

use std::path::Path;
use std::time::Duration;

use crate::connect_progress::{ConnectProgressCallback, ConnectStage, emit_connect_progress};
use crate::device::{BlePrinterDevice, PrinterDevice, PrinterStatus};
use crate::error::{PrinterError, Result};
use crate::image::FitMode;
use crate::transport::{self, BleTransport, DEFAULT_SCAN_DURATION};

/// Information about a discovered printer (before connecting).
#[derive(Debug, Clone)]
pub struct DiscoveredPrinter {
    /// BLE device name.
    pub name: String,
    /// Internal index for connection.
    _index: usize,
}

impl std::fmt::Display for DiscoveredPrinter {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.name)
    }
}

/// Scan for nearby Instax printers.
pub async fn scan(duration: Option<Duration>) -> Result<Vec<DiscoveredPrinter>> {
    let adapter = transport::get_adapter().await?;
    let duration = duration.unwrap_or(DEFAULT_SCAN_DURATION);
    let results = transport::scan(&adapter, duration).await?;

    Ok(results
        .into_iter()
        .enumerate()
        .map(|(i, (_, name))| DiscoveredPrinter { name, _index: i })
        .collect())
}

/// Connect to a specific printer by name.
pub async fn connect(
    device_name: &str,
    duration: Option<Duration>,
) -> Result<Box<dyn PrinterDevice>> {
    connect_internal(device_name, duration, None, false).await
}

/// Connect to a specific printer by name and emit progress stages.
pub async fn connect_with_progress(
    device_name: &str,
    duration: Option<Duration>,
    progress: Option<&ConnectProgressCallback>,
) -> Result<Box<dyn PrinterDevice>> {
    connect_internal(device_name, duration, progress, true).await
}

async fn connect_internal(
    device_name: &str,
    duration: Option<Duration>,
    progress: Option<&ConnectProgressCallback>,
    fetch_initial_status: bool,
) -> Result<Box<dyn PrinterDevice>> {
    let result: Result<Box<dyn PrinterDevice>> = async {
        emit_connect_progress(progress, ConnectStage::ScanStarted, None::<String>);
        let adapter = transport::get_adapter().await?;
        let results = transport::scan(&adapter, duration.unwrap_or(DEFAULT_SCAN_DURATION)).await?;
        emit_connect_progress(progress, ConnectStage::ScanFinished, None::<String>);

        let mut exact_matches = Vec::new();
        let mut partial_matches = Vec::new();

        for result in results {
            if printer_name_matches(&result.1, device_name) {
                exact_matches.push(result);
            } else if normalized_printer_name(&result.1).contains(&normalized_printer_name(device_name))
                || normalized_printer_name(device_name).contains(&normalized_printer_name(&result.1))
            {
                partial_matches.push(result);
            }
        }

        let matches = if exact_matches.is_empty() {
            partial_matches
        } else {
            exact_matches
        };

        let match_count = matches.len();
        let (peripheral, name) = match match_count {
            0 => return Err(PrinterError::PrinterNotFound),
            1 => matches.into_iter().next().expect("one match must exist"),
            count => return Err(PrinterError::MultiplePrinters { count }),
        };

        emit_connect_progress(progress, ConnectStage::DeviceMatched, Some(name.clone()));

        let transport = BleTransport::connect_with_progress(peripheral, progress).await?;
        let device =
            BlePrinterDevice::new_with_progress(Box::new(transport), name, progress).await?;

        if fetch_initial_status {
            emit_connect_progress(progress, ConnectStage::StatusFetching, None::<String>);
            let _ = device.status().await?;
        }
        emit_connect_progress(
            progress,
            ConnectStage::Connected,
            Some(device.name().to_owned()),
        );

        Ok::<Box<dyn PrinterDevice>, PrinterError>(Box::new(device))
    }
    .await;

    if let Err(err) = &result {
        emit_connect_progress(
            progress,
            ConnectStage::Failed,
            Some::<String>(err.to_string()),
        );
    }

    result
}

fn printer_name_matches(discovered_name: &str, target_name: &str) -> bool {
    if discovered_name == target_name {
        return true;
    }

    let discovered_normalized = normalized_printer_name(discovered_name);
    let target_normalized = normalized_printer_name(target_name);
    if discovered_normalized == target_normalized {
        return true;
    }

    match (
        extracted_printer_serial(&discovered_normalized),
        extracted_printer_serial(&target_normalized),
    ) {
        (Some(discovered_serial), Some(target_serial)) => discovered_serial == target_serial,
        _ => false,
    }
}

fn normalized_printer_name(name: &str) -> String {
    let trimmed = name.trim();
    let without_parenthetical_suffix = trimmed.split('(').next().unwrap_or(trimmed).trim();
    without_parenthetical_suffix
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_ascii_uppercase()
}

fn extracted_printer_serial(name: &str) -> Option<String> {
    let normalized = normalized_printer_name(name);
    let suffix = normalized.strip_prefix("INSTAX-")?;
    let digits: String = suffix.chars().take_while(|ch| ch.is_ascii_digit()).collect();
    (!digits.is_empty()).then_some(digits)
}

/// Connect to the first available Instax printer.
pub async fn connect_any(duration: Option<Duration>) -> Result<Box<dyn PrinterDevice>> {
    let adapter = transport::get_adapter().await?;
    let results = transport::scan(&adapter, duration.unwrap_or(DEFAULT_SCAN_DURATION)).await?;

    let (peripheral, name) = results
        .into_iter()
        .next()
        .ok_or(PrinterError::PrinterNotFound)?;

    let transport = BleTransport::connect(peripheral).await?;
    let device = BlePrinterDevice::new(Box::new(transport), name).await?;
    Ok(Box::new(device))
}

#[cfg(test)]
mod tests {
    use super::{extracted_printer_serial, normalized_printer_name, printer_name_matches};

    #[test]
    fn normalizes_parenthetical_suffixes() {
        assert_eq!(
            normalized_printer_name("INSTAX-12345678 (iOS)"),
            "INSTAX-12345678"
        );
    }

    #[test]
    fn matches_same_printer_with_ios_suffix_variants() {
        assert!(printer_name_matches(
            "INSTAX-12345678 (iOS)",
            "INSTAX-12345678"
        ));
        assert!(printer_name_matches(
            "INSTAX-12345678",
            "INSTAX-12345678 (IOS)"
        ));
    }

    #[test]
    fn extracts_serial_from_instax_name() {
        assert_eq!(
            extracted_printer_serial("INSTAX-12345678 (iOS)").as_deref(),
            Some("12345678")
        );
    }
}

/// One-shot print: connect to a printer, print an image, disconnect.
///
/// If `device_name` is None, connects to the first available printer.
pub async fn print_file(
    path: &Path,
    fit: FitMode,
    quality: u8,
    device_name: Option<&str>,
    progress: Option<&(dyn Fn(usize, usize) + Send + Sync)>,
) -> Result<()> {
    let device = match device_name {
        Some(name) => connect(name, None).await?,
        None => connect_any(None).await?,
    };

    let print_result = device.print_file(path, fit, quality, 0, progress).await;
    let disconnect_result = device.disconnect().await;

    match (print_result, disconnect_result) {
        (Err(err), _) => Err(err),
        (Ok(()), Err(err)) => Err(err),
        (Ok(()), Ok(())) => Ok(()),
    }
}

/// Get printer status: connect, query, disconnect.
pub async fn get_status(
    device_name: Option<&str>,
    duration: Option<Duration>,
) -> Result<PrinterStatus> {
    let device = match device_name {
        Some(name) => connect(name, duration).await?,
        None => connect_any(duration).await?,
    };

    let status_result = device.status().await;
    let disconnect_result = device.disconnect().await;

    match (status_result, disconnect_result) {
        (Err(err), _) => Err(err),
        (Ok(_), Err(err)) => Err(err),
        (Ok(status), Ok(())) => Ok(status),
    }
}

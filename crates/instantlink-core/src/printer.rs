//! High-level API for scanning, connecting, and printing with Instax printers.

use std::path::Path;
use std::time::Duration;

use crate::connect_progress::{ConnectProgressCallback, ConnectStage, emit_connect_progress};
use crate::device::{BlePrinterDevice, PrinterDevice, PrinterStatus};
use crate::error::{PrinterError, Result};
use crate::image::FitMode;
use crate::transport::{self, BleTransport, DEFAULT_SCAN_DURATION};

/// Poll cadence while waiting for the target printer to appear during the active scan window.
/// A named connect proceeds as soon as the printer is uniquely matched instead of waiting out the
/// full scan duration, trimming several seconds off every (re)connect. See `docs/plans/031` Phase 1.
const DISCOVERY_POLL_INTERVAL: Duration = Duration::from_millis(400);

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
    emit_connect_progress(progress, ConnectStage::ScanStarted, None::<String>);

    let adapter = match transport::get_adapter().await {
        Ok(adapter) => adapter,
        Err(err) => {
            emit_connect_progress(progress, ConnectStage::Failed, Some(err.to_string()));
            return Err(err);
        }
    };

    // Use an active scan ONLY to confirm the printer is present/advertising, then stop it before
    // connecting. On the Pi Zero 2 W controller an active scan running *during* the connect makes
    // the radio time-slice between scanning and connecting, stretching connection establishment to
    // ~11 s; with the scan stopped a direct connect to the matched peripheral completes in ~0.3 s
    // (measured). The scan is still needed up front so we never try to connect to an absent printer
    // (which wedges BlueZ). See `docs/plans/031` Phase 1.
    if let Err(err) = transport::start_scan(&adapter).await {
        emit_connect_progress(progress, ConnectStage::Failed, Some(err.to_string()));
        return Err(err);
    }

    let result: Result<Box<dyn PrinterDevice>> = async {
        // Poll the candidate list during the active scan and connect as soon as the target is
        // uniquely matched, rather than always waiting out the full scan window. The scan stays
        // active throughout (started above, stopped below). On a not-found/ambiguous result we
        // keep polling until the deadline, then surface the last selection error.
        let deadline = tokio::time::Instant::now() + duration.unwrap_or(DEFAULT_SCAN_DURATION);
        let (peripheral, name) = loop {
            let candidates = transport::collect_instax_peripherals(&adapter).await?;
            match select_matching_result(candidates, device_name) {
                Ok(found) => break found,
                Err(err) => {
                    if tokio::time::Instant::now() >= deadline {
                        return Err(err);
                    }
                    tokio::time::sleep(DISCOVERY_POLL_INTERVAL).await;
                }
            }
        };
        emit_connect_progress(progress, ConnectStage::ScanFinished, None::<String>);

        emit_connect_progress(progress, ConnectStage::DeviceMatched, Some(name.clone()));

        // Stop scanning before connecting so the connect does not contend with the scan for the
        // radio (~11 s with the scan running vs ~0.3 s without). The target is already matched.
        let _ = transport::stop_scan(&adapter).await;

        let transport = BleTransport::connect_with_progress(peripheral, progress).await?;
        let device =
            BlePrinterDevice::new_with_progress(Box::new(transport), name, progress).await?;

        if fetch_initial_status {
            emit_connect_progress(progress, ConnectStage::StatusFetching, None::<String>);
            if let Err(error) = device.status().await {
                let _ = device.disconnect().await;
                return Err(error);
            }
        }
        emit_connect_progress(
            progress,
            ConnectStage::Connected,
            Some(device.name().to_owned()),
        );

        Ok::<Box<dyn PrinterDevice>, PrinterError>(Box::new(device))
    }
    .await;

    // Stop the active scan regardless of outcome so the adapter is not left scanning between polls.
    let _ = transport::stop_scan(&adapter).await;

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
    let serial: String = suffix
        .chars()
        .take_while(|ch| ch.is_ascii_alphanumeric())
        .collect();
    (!serial.is_empty()).then_some(serial)
}

fn select_matching_result<T>(results: Vec<(T, String)>, device_name: &str) -> Result<(T, String)> {
    let target_name = device_name.trim();
    let mut exact_name_matches = Vec::new();
    let mut normalized_matches = Vec::new();
    let mut partial_matches = Vec::new();

    for result in results {
        if result.1.trim() == target_name {
            exact_name_matches.push(result);
        } else if printer_name_matches(&result.1, device_name) {
            normalized_matches.push(result);
        } else if normalized_printer_name(&result.1).contains(&normalized_printer_name(device_name))
            || normalized_printer_name(device_name).contains(&normalized_printer_name(&result.1))
        {
            partial_matches.push(result);
        }
    }

    let target_has_platform_suffix = has_platform_suffix(target_name);
    if target_has_platform_suffix && exact_name_matches.len() == 1 {
        return Ok(exact_name_matches
            .into_iter()
            .next()
            .expect("one match must exist"));
    }
    if target_has_platform_suffix && exact_name_matches.len() > 1 {
        return select_preferred_advertisement(exact_name_matches);
    }

    let matches = if exact_name_matches.is_empty() && normalized_matches.is_empty() {
        partial_matches
    } else {
        exact_name_matches.append(&mut normalized_matches);
        exact_name_matches
    };

    match matches.len() {
        0 => Err(PrinterError::PrinterNotFound),
        1 => Ok(matches.into_iter().next().expect("one match must exist")),
        _count => select_preferred_advertisement(matches),
    }
}

fn has_platform_suffix(name: &str) -> bool {
    let upper = name.to_ascii_uppercase();
    upper.ends_with("(IOS)") || upper.ends_with("(ANDROID)")
}

fn select_preferred_advertisement<T>(mut matches: Vec<(T, String)>) -> Result<(T, String)> {
    if !all_same_printer_identity(&matches) {
        return Err(PrinterError::MultiplePrinters {
            count: matches.len(),
        });
    }
    matches.sort_by_key(|(_, name)| advertisement_priority(name));
    let best_priority = matches
        .first()
        .map(|(_, name)| advertisement_priority(name))
        .ok_or(PrinterError::PrinterNotFound)?;
    let same_priority_count = matches
        .iter()
        .filter(|(_, name)| advertisement_priority(name) == best_priority)
        .count();
    if same_priority_count == 1 {
        return Ok(matches.remove(0));
    }
    Err(PrinterError::MultiplePrinters {
        count: matches.len(),
    })
}

fn all_same_printer_identity<T>(matches: &[(T, String)]) -> bool {
    let Some((_, first_name)) = matches.first() else {
        return false;
    };
    let first_identity = printer_identity(first_name);
    matches
        .iter()
        .all(|(_, name)| printer_identity(name) == first_identity)
}

fn printer_identity(name: &str) -> String {
    extracted_printer_serial(name).unwrap_or_else(|| normalized_printer_name(name))
}

fn advertisement_priority(name: &str) -> u8 {
    let upper = name.to_ascii_uppercase();
    if upper.ends_with("(IOS)") {
        0
    } else if upper.ends_with("(ANDROID)") {
        1
    } else {
        2
    }
}

fn combine_operation_and_disconnect<T>(
    operation_result: Result<T>,
    disconnect_result: Result<()>,
) -> Result<T> {
    match (operation_result, disconnect_result) {
        (Err(err), _) => Err(err),
        (Ok(_), Err(err)) => Err(err),
        (Ok(value), Ok(())) => Ok(value),
    }
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
    use super::{
        combine_operation_and_disconnect, extracted_printer_serial, normalized_printer_name,
        printer_name_matches, select_matching_result,
    };
    use crate::error::PrinterError;

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
        assert_eq!(
            extracted_printer_serial("INSTAX-1N034655 (iOS)").as_deref(),
            Some("1N034655")
        );
    }

    #[test]
    fn normalized_printer_name_collapses_whitespace_and_uppercases() {
        assert_eq!(
            normalized_printer_name("  instax-12345678   (iOS)  "),
            "INSTAX-12345678"
        );
    }

    #[test]
    fn extracted_printer_serial_returns_none_for_non_instax_names() {
        assert_eq!(extracted_printer_serial("mini-link"), None);
        assert_eq!(extracted_printer_serial("INSTAX-"), None);
    }

    #[test]
    fn printer_name_matches_returns_false_for_different_serials() {
        assert!(!printer_name_matches(
            "INSTAX-12345678 (iOS)",
            "INSTAX-87654321"
        ));
        assert!(!printer_name_matches(
            "INSTAX-1N034655 (iOS)",
            "INSTAX-1X999999"
        ));
    }

    #[test]
    fn select_matching_result_prefers_exact_match_over_partial_match() {
        let results = vec![
            (1, "INSTAX-1234".to_string()),
            (2, "INSTAX-12345678 (iOS)".to_string()),
        ];
        let (matched, name) = select_matching_result(results, "INSTAX-12345678").unwrap();
        assert_eq!(matched, 2);
        assert_eq!(name, "INSTAX-12345678 (iOS)");
    }

    #[test]
    fn select_matching_result_prefers_ios_when_serial_has_multiple_advertisements() {
        let results = vec![
            (1, "INSTAX-12345678".to_string()),
            (2, "INSTAX-12345678 (iOS)".to_string()),
        ];
        let (matched, name) = select_matching_result(results, "INSTAX-12345678").unwrap();
        assert_eq!(matched, 2);
        assert_eq!(name, "INSTAX-12345678 (iOS)");
    }

    #[test]
    fn select_matching_result_prefers_exact_platform_suffix() {
        let results = vec![
            (1, "INSTAX-12345678 (ANDROID)".to_string()),
            (2, "INSTAX-12345678 (iOS)".to_string()),
        ];
        let (matched, name) = select_matching_result(results, "INSTAX-12345678 (ANDROID)").unwrap();
        assert_eq!(matched, 1);
        assert_eq!(name, "INSTAX-12345678 (ANDROID)");
    }

    #[test]
    fn select_matching_result_falls_back_to_partial_match() {
        let results = vec![
            (1, "INSTAX-1234".to_string()),
            (2, "INSTAX-9999".to_string()),
        ];
        let (matched, name) = select_matching_result(results, "1234").unwrap();
        assert_eq!(matched, 1);
        assert_eq!(name, "INSTAX-1234");
    }

    #[test]
    fn select_matching_result_returns_not_found_when_no_match_exists() {
        let results = vec![(1, "INSTAX-1234".to_string())];
        let err = select_matching_result(results, "INSTAX-9999").unwrap_err();
        assert!(matches!(err, PrinterError::PrinterNotFound));
    }

    #[test]
    fn select_matching_result_returns_multiple_printers_for_multiple_partial_matches() {
        let results = vec![
            (1, "INSTAX-1234".to_string()),
            (2, "INSTAX-12345".to_string()),
        ];
        let err = select_matching_result(results, "123").unwrap_err();
        assert!(matches!(err, PrinterError::MultiplePrinters { count: 2 }));
    }

    #[test]
    fn select_matching_result_does_not_prefer_platform_suffix_across_different_printers() {
        let results = vec![
            (1, "INSTAX-1N034655 (iOS)".to_string()),
            (2, "INSTAX-1X999999 (ANDROID)".to_string()),
        ];
        let err = select_matching_result(results, "1").unwrap_err();
        assert!(matches!(err, PrinterError::MultiplePrinters { count: 2 }));
    }

    #[test]
    fn combine_operation_and_disconnect_prefers_operation_error() {
        let err = combine_operation_and_disconnect::<()>(
            Err(PrinterError::PrinterBusy),
            Err(PrinterError::Timeout),
        )
        .unwrap_err();
        assert!(matches!(err, PrinterError::PrinterBusy));
    }

    #[test]
    fn combine_operation_and_disconnect_returns_disconnect_error_after_success() {
        let err = combine_operation_and_disconnect(Ok(()), Err(PrinterError::Timeout)).unwrap_err();
        assert!(matches!(err, PrinterError::Timeout));
    }

    #[test]
    fn combine_operation_and_disconnect_returns_success_value() {
        let value = combine_operation_and_disconnect(Ok(42_u8), Ok(())).unwrap();
        assert_eq!(value, 42);
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
    combine_operation_and_disconnect(print_result, disconnect_result)
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
    combine_operation_and_disconnect(status_result, disconnect_result)
}

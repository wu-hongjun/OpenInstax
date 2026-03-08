//! Output formatting: progress bars and JSON output mode.

use indicatif::{ProgressBar, ProgressStyle};
use serde::Serialize;

/// Create a progress bar for image transfer.
pub fn transfer_progress(total: u64) -> ProgressBar {
    let pb = ProgressBar::new(total);
    pb.set_style(
        ProgressStyle::default_bar()
            .template("{spinner:.green} [{bar:40.cyan/blue}] {pos}/{len} chunks ({eta})")
            .expect("valid template")
            .progress_chars("=>-"),
    );
    pb
}

/// Create a spinner for indeterminate operations (scanning, connecting).
pub fn spinner(message: &str) -> ProgressBar {
    let pb = ProgressBar::new_spinner();
    pb.set_style(
        ProgressStyle::default_spinner()
            .template("{spinner:.green} {msg}")
            .expect("valid template"),
    );
    pb.set_message(message.to_string());
    pb.enable_steady_tick(std::time::Duration::from_millis(100));
    pb
}

/// Print a value as JSON to stdout.
pub fn print_json<T: Serialize>(value: &T) -> anyhow::Result<()> {
    let json = serde_json::to_string_pretty(value)?;
    println!("{json}");
    Ok(())
}

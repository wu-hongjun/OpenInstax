//! OpenInstax CLI — command-line interface for Instax Link printers.

mod output;

use std::path::PathBuf;

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use openinstax_core::image::FitMode;
use openinstax_core::printer;

#[derive(Parser)]
#[command(
    name = "openinstax",
    version,
    about = "Print to Fujifilm Instax Link printers"
)]
struct Cli {
    /// Target a specific printer by name.
    #[arg(long, global = true)]
    device: Option<String>,

    /// Output as JSON (for machine consumption).
    #[arg(long, global = true)]
    json: bool,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Scan for nearby Instax printers
    Scan,
    /// Show printer info (battery, film, firmware, print count)
    Info,
    /// Print an image
    Print {
        /// Path to the image file
        image: PathBuf,
        /// JPEG quality (1-100, default 97)
        #[arg(long, default_value = "97")]
        quality: u8,
        /// How to fit the image: crop, contain, or stretch
        #[arg(long, default_value = "crop")]
        fit: String,
    },
    /// Control the printer LED
    Led {
        #[command(subcommand)]
        action: LedAction,
    },
    /// Show printer status (connectivity + info)
    Status,
}

#[derive(Subcommand)]
enum LedAction {
    /// Set LED color and pattern
    Set {
        /// Color as hex (#RRGGBB) or named color
        color: String,
        /// Pattern: solid, blink, or breathe
        #[arg(long, default_value = "solid")]
        pattern: String,
    },
    /// Turn LED off
    Off,
}

#[tokio::main]
async fn main() -> Result<()> {
    env_logger::init();
    let cli = Cli::parse();

    match cli.command {
        Commands::Scan => {
            let sp = output::spinner("Scanning for Instax printers...");
            let printers = printer::scan(None).await.context("scan failed")?;
            sp.finish_and_clear();

            if cli.json {
                let names: Vec<&str> = printers.iter().map(|p| p.name.as_str()).collect();
                output::print_json(&names)?;
            } else if printers.is_empty() {
                println!("No Instax printers found");
            } else {
                println!("Found {} printer(s):", printers.len());
                for p in &printers {
                    println!("  {}", p.name);
                }
            }
        }

        Commands::Info => {
            let sp = output::spinner("Connecting...");
            let status = printer::get_status(cli.device.as_deref())
                .await
                .context("failed to get printer status")?;
            sp.finish_and_clear();

            if cli.json {
                #[derive(serde::Serialize)]
                struct Info {
                    name: String,
                    model: String,
                    battery: u8,
                    film_remaining: u8,
                    print_count: u32,
                }
                output::print_json(&Info {
                    name: status.name,
                    model: status.model.to_string(),
                    battery: status.battery,
                    film_remaining: status.film_remaining,
                    print_count: status.print_count,
                })?;
            } else {
                println!("Printer:    {}", status.name);
                println!("Model:      {}", status.model);
                println!("Battery:    {}%", status.battery);
                println!("Film:       {} remaining", status.film_remaining);
                println!("Prints:     {}", status.print_count);
            }
        }

        Commands::Print {
            image,
            quality,
            fit,
        } => {
            let fit_mode = FitMode::from_str_lossy(&fit);

            let sp = output::spinner("Connecting to printer...");
            let device = match cli.device.as_deref() {
                Some(name) => printer::connect(name).await?,
                None => printer::connect_any().await?,
            };
            sp.finish_and_clear();

            let model = device.model();
            println!("Printing to {} ({})", device.name(), model);

            // Prepare image to know total chunks for progress bar
            let (_jpeg_data, chunks) =
                openinstax_core::image::prepare_image(&image, model, fit_mode, quality)
                    .context("failed to prepare image")?;

            let pb = output::transfer_progress(chunks.len() as u64);
            let progress = move |sent: usize, _total: usize| {
                pb.set_position(sent as u64);
            };

            device
                .print_file(&image, fit_mode, quality, Some(&progress))
                .await
                .context("print failed")?;

            device.disconnect().await?;
            println!("Print complete!");
        }

        Commands::Led { action } => match action {
            LedAction::Set { color, pattern } => {
                let (r, g, b) = parse_hex_color(&color)?;
                let pattern_byte = match pattern.to_lowercase().as_str() {
                    "blink" => 1,
                    "breathe" => 2,
                    _ => 0, // solid
                };

                let device = match cli.device.as_deref() {
                    Some(name) => printer::connect(name).await?,
                    None => printer::connect_any().await?,
                };

                device
                    .set_led(r, g, b, pattern_byte)
                    .await
                    .context("failed to set LED")?;
                device.disconnect().await?;
                println!("LED set to #{:02x}{:02x}{:02x} ({})", r, g, b, pattern);
            }
            LedAction::Off => {
                let device = match cli.device.as_deref() {
                    Some(name) => printer::connect(name).await?,
                    None => printer::connect_any().await?,
                };

                device.led_off().await.context("failed to turn off LED")?;
                device.disconnect().await?;
                println!("LED off");
            }
        },

        Commands::Status => {
            let sp = output::spinner("Checking printer status...");
            let status = printer::get_status(cli.device.as_deref()).await;
            sp.finish_and_clear();

            match status {
                Ok(status) => {
                    if cli.json {
                        #[derive(serde::Serialize)]
                        struct StatusOutput {
                            connected: bool,
                            name: String,
                            model: String,
                            battery: u8,
                            film_remaining: u8,
                            print_count: u32,
                        }
                        output::print_json(&StatusOutput {
                            connected: true,
                            name: status.name,
                            model: status.model.to_string(),
                            battery: status.battery,
                            film_remaining: status.film_remaining,
                            print_count: status.print_count,
                        })?;
                    } else {
                        println!("Connected:  yes");
                        println!("Printer:    {}", status.name);
                        println!("Model:      {}", status.model);
                        println!("Battery:    {}%", status.battery);
                        println!("Film:       {} remaining", status.film_remaining);
                        println!("Prints:     {}", status.print_count);
                    }
                }
                Err(_) => {
                    if cli.json {
                        #[derive(serde::Serialize)]
                        struct Disconnected {
                            connected: bool,
                        }
                        output::print_json(&Disconnected { connected: false })?;
                    } else {
                        println!("Connected:  no");
                        println!("No Instax printer found");
                    }
                }
            }
        }
    }

    Ok(())
}

/// Parse a hex color string like "#FF0000" or "FF0000" into (r, g, b).
fn parse_hex_color(s: &str) -> Result<(u8, u8, u8)> {
    let hex = s.trim_start_matches('#');
    if hex.len() != 6 {
        anyhow::bail!("invalid hex color: {s} (expected 6 hex digits)");
    }
    let r = u8::from_str_radix(&hex[0..2], 16).context("invalid red component")?;
    let g = u8::from_str_radix(&hex[2..4], 16).context("invalid green component")?;
    let b = u8::from_str_radix(&hex[4..6], 16).context("invalid blue component")?;
    Ok((r, g, b))
}

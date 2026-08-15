//! Forger CLI — Rust binary entry point.
//!
//! This is the standalone CLI binary. When built via maturin, the same
//! core library is also exposed as a Python extension module (`_core`).

use std::process::ExitCode;

use clap::Parser;
use forger_core::cli::{Cli, Commands, BuildConfigCommands};
use forger_core::check_build_config;

fn main() -> ExitCode {
    let cli = Cli::parse();

    let _log_level = if cli.verbose { "debug" } else { "info" };

    match cli.command {
        Commands::Compile(args) => {
            eprintln!("compile: source={} output={} entry={}", args.source, args.output, args.entry_point);
            ExitCode::SUCCESS
        }
        Commands::Forge(args) => {
            eprintln!("forge: vfs_dir={} output={}", args.vfs_dir, args.output);
            ExitCode::SUCCESS
        }
        Commands::Build(args) => {
            eprintln!("build: artifact={} target={}", args.artifact, args.target);
            ExitCode::SUCCESS
        }
        Commands::Info(args) => {
            eprintln!("info: artifact={}", args.artifact);
            ExitCode::SUCCESS
        }
        Commands::BuildConfig(BuildConfigCommands::Check(args)) => {
            match check_build_config(&args.target) {
                Ok(result) => {
                    println!("{}", result.format_report());
                    if result.has_issues() {
                        ExitCode::from(1)
                    } else {
                        ExitCode::SUCCESS
                    }
                }
                Err(e) => {
                    eprintln!("Error: {e}");
                    ExitCode::from(2)
                }
            }
        }
    }
}

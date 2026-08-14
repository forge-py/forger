//! Forger CLI — Rust binary entry point.
//!
//! This is the standalone CLI binary. When built via maturin, the same
//! core library is also exposed as a Python extension module (`_core`).

use std::process::ExitCode;

use clap::Parser;
use forger_core::cli::{Cli, Commands};

fn main() -> ExitCode {
    let cli = Cli::parse();

    match cli.command {
        Commands::Compile(args) => {
            eprintln!("compile: source={} output={} entry={}", args.source, args.output, args.entry_point);
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
    }
}

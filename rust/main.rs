//! Forger CLI — Rust binary entry point.
//!
//! This is the standalone CLI binary. When built via maturin, the same
//! core library is also exposed as a Python extension module (`_core`).

use std::process::ExitCode;

use clap::Parser;
use forger_core::cli::{Cli, Commands, BuildConfigCommands, CpythonCommands};
use forger_core::check_build_config;
use forger_core::{CpythonModuleRegistry, CpythonBuildConfig};

fn parse_cpython_version(version: &str) -> Option<(u32, u32)> {
    let parts: Vec<&str> = version.split('.').collect();
    if parts.len() == 2 {
        let major = parts[0].parse::<u32>().ok()?;
        let minor = parts[1].parse::<u32>().ok()?;
        Some((major, minor))
    } else {
        None
    }
}

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
        Commands::Cpython(CpythonCommands::Analyze(args)) => {
            let version = parse_cpython_version(&args.version).unwrap_or((3, 12));
            let registry = CpythonModuleRegistry::new(version);
            let analysis = registry.analyze_required_sources_inner(&args.modules);
            println!("{}", analysis.format_report());
            ExitCode::SUCCESS
        }
        Commands::Cpython(CpythonCommands::BuildConfig(args)) => {
            let version = parse_cpython_version(&args.version).unwrap_or((3, 12));
            let registry = CpythonModuleRegistry::new(version);
            let analysis = registry.analyze_required_sources_inner(&args.modules);
            let build_cfg = CpythonBuildConfig::from_analysis_inner(
                &analysis,
                &args.target,
                &args.version,
            );
            println!("{}", build_cfg.format_report());
            ExitCode::SUCCESS
        }
    }
}

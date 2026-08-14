//! CLI argument parsing for Forger.
//!
//! Uses clap derive macros for type-safe CLI argument parsing,
//! same pattern as Ruff.

use clap::Parser;

/// Forger — Python application compiler, analyzer, bundler, and cross-platform executable builder.
#[derive(Parser, Debug)]
#[command(name = "forger")]
#[command(about = "Python application compiler and bundler", long_about = None)]
#[command(version)]
pub struct Cli {
    /// Enable verbose output.
    #[arg(short, long, global = true)]
    pub verbose: bool,

    #[command(subcommand)]
    pub command: Commands,
}

#[derive(clap::Subcommand, Debug)]
pub enum Commands {
    /// Analyze and compile a Python project into a .forge artifact.
    Compile(CompileArgs),

    /// Build a platform-specific executable from a .forge artifact.
    Build(BuildArgs),

    /// Show information about a .forge artifact.
    Info(InfoArgs),
}

/// Arguments for the `compile` subcommand.
#[derive(Parser, Debug)]
pub struct CompileArgs {
    /// Source directory to compile.
    #[arg(short, long, default_value = ".")]
    pub source: String,

    /// Output .forge file path.
    #[arg(short, long, default_value = "app.forge")]
    pub output: String,

    /// Entry point module name.
    #[arg(short = 'e', long, default_value = "main")]
    pub entry_point: String,

    /// Path to virtual environment.
    #[arg(long)]
    pub venv: Option<String>,

    /// Path to forger.py project extension.
    #[arg(long)]
    pub forger_py: Option<String>,
}

/// Arguments for the `build` subcommand.
#[derive(Parser, Debug)]
pub struct BuildArgs {
    /// Path to the .forge artifact.
    pub artifact: String,

    /// Target platform (e.g., windows-x64, linux-x64, android-arm64).
    #[arg(short, long, default_value = "windows-x64")]
    pub target: String,

    /// Output directory.
    #[arg(short, long)]
    pub output: Option<String>,
}

/// Arguments for the `info` subcommand.
#[derive(Parser, Debug)]
pub struct InfoArgs {
    /// Path to the .forge artifact.
    pub artifact: String,
}

//! CLI argument parsing for Forger.
//!
//! Uses clap derive macros for type-safe CLI argument parsing,
//! same pattern as Ruff.

use clap::{Parser, Subcommand, Args};

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

#[derive(Subcommand, Debug)]
pub enum Commands {
    /// Analyze and compile a Python project into a VFS directory.
    Compile(CompileArgs),

    /// Package the VFS directory into a .forge artifact.
    Forge(ForgeArgs),

    /// Build a platform-specific executable from a .forge artifact.
    Build(BuildArgs),

    /// Show information about a .forge artifact.
    Info(InfoArgs),

    /// Build-config subcommands.
    #[command(subcommand)]
    BuildConfig(BuildConfigCommands),

    /// CPython module analysis and minimal build configuration.
    #[command(subcommand)]
    Cpython(CpythonCommands),
}

#[derive(Subcommand, Debug)]
pub enum BuildConfigCommands {
    /// Check if required build tools (MSVC, etc.) are present on the machine.
    Check(CheckArgs),
}

/// CPython analysis subcommands.
#[derive(Subcommand, Debug)]
pub enum CpythonCommands {
    /// Analyze which CPython modules are required for a given set of modules.
    Analyze(CpythonAnalyzeArgs),

    /// Generate a minimal CPython build configuration.
    BuildConfig(CpythonBuildConfigArgs),
}

/// Arguments for the `cpython analyze` subcommand.
#[derive(Args, Debug)]
pub struct CpythonAnalyzeArgs {
    /// Python module names to analyze (space-separated).
    #[arg(index = 1, num_args = 1..)]
    pub modules: Vec<String>,

    /// CPython version major.minor (default: 3.12).
    #[arg(long, default_value = "3.12")]
    pub version: String,
}

/// Arguments for the `cpython build-config` subcommand.
#[derive(Args, Debug)]
pub struct CpythonBuildConfigArgs {
    /// Python module names to include (space-separated).
    #[arg(index = 1, num_args = 1..)]
    pub modules: Vec<String>,

    /// Target platform (e.g., windows-x64, linux-x64).
    #[arg(short, long, default_value = "windows-x64")]
    pub target: String,

    /// CPython version major.minor (default: 3.12).
    #[arg(long, default_value = "3.12")]
    pub version: String,
}

/// Arguments for the `compile` subcommand.
#[derive(Args, Debug)]
pub struct CompileArgs {
    /// Source directory to compile.
    #[arg(index = 1, default_value = ".")]
    pub source: String,

    /// Output VFS directory.
    #[arg(short, long, default_value = "dist")]
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

/// Arguments for the `forge` subcommand.
#[derive(Args, Debug)]
pub struct ForgeArgs {
    /// VFS directory to package (output of `forger compile`).
    #[arg(index = 1, default_value = "dist")]
    pub vfs_dir: String,

    /// Output .forge artifact path.
    #[arg(short, long, default_value = "app.forge")]
    pub output: String,
}

/// Arguments for the `build` subcommand.
#[derive(Args, Debug)]
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
#[derive(Args, Debug)]
pub struct InfoArgs {
    /// Path to the .forge artifact.
    pub artifact: String,
}

/// Arguments for the `build-config check` subcommand.
#[derive(Args, Debug)]
pub struct CheckArgs {
    /// Target platform to check build tools for (e.g., windows-x64, linux-x64).
    #[arg(short, long, default_value = "windows-x64")]
    pub target: String,
}

//! Build configuration checker.
//!
//! Verifies that required build tools (MSVC toolchain, etc.)
//! are present and functional for the target platform.

use std::path::{Path, PathBuf};
use std::process::Command;

use crate::result::{ForgerError, ForgerResult};

// ---------------------------------------------------------------------------
// Tool check types
// ---------------------------------------------------------------------------

/// The result of checking for a single build tool.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ToolCheck {
    /// The tool is present and functional.
    Present { version: Option<String> },
    /// The tool could not be found.
    Missing,
    /// The tool exists but is misconfigured.
    Misconfigured(String),
}

impl std::fmt::Display for ToolCheck {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ToolCheck::Present { version } => {
                if let Some(v) = version {
                    write!(f, "present ({v})")
                } else {
                    write!(f, "present")
                }
            }
            ToolCheck::Missing => write!(f, "missing"),
            ToolCheck::Misconfigured(reason) => write!(f, "misconfigured ({reason})"),
        }
    }
}

// ---------------------------------------------------------------------------
// Check result aggregation
// ---------------------------------------------------------------------------

/// Result of a full build-config check for a target.
#[derive(Debug)]
pub struct BuildConfigResult {
    /// The target platform that was checked.
    pub target: String,
    /// Individual tool check results.
    pub tools: Vec<(String, ToolCheck)>,
    /// Overall pass/fail.
    pub passed: bool,
}

impl BuildConfigResult {
    pub fn has_issues(&self) -> bool {
        !self.passed
    }

    /// Format a human-readable report.
    pub fn format_report(&self) -> String {
        let mut lines = vec![
            format!("Build Configuration Check: {}", self.target),
            "─".repeat(50),
        ];

        for (name, check) in &self.tools {
            let status = match check {
                ToolCheck::Present { .. } => "✓",
                ToolCheck::Missing => "✗",
                ToolCheck::Misconfigured(_) => "⚠",
            };
            lines.push(format!("  {status} {name}: {check}"));
        }

        lines.push("─".repeat(50));
        lines.push(format!(
            "Result: {}",
            if self.passed {
                "All required tools are available"
            } else {
                "Some required tools are missing or misconfigured"
            }
        ));

        lines.join("\n")
    }
}

// ---------------------------------------------------------------------------
// MSVC toolchain detection (Windows)
// ---------------------------------------------------------------------------

fn find_msvc_compiler() -> ToolCheck {
    // Try cl.exe via PATH
    if let Some(cl_path) = find_in_path("cl.exe") {
        match Command::new("cl.exe").output() {
            Ok(output) => {
                let stderr = String::from_utf8_lossy(&output.stderr);
                // cl.exe prints version info to stderr
                if let Some(version_start) = stderr.find("Microsoft") {
                    let version_line = stderr.lines().find(|l| l.contains("Version")).unwrap_or("");
                    let version = Some(version_line.trim().to_string());
                    return ToolCheck::Present { version };
                }
                ToolCheck::Present { version: None }
            }
            Err(_) => ToolCheck::Missing,
        }
    } else {
        ToolCheck::Missing
    }
}

fn find_msvc_lib() -> ToolCheck {
    find_in_path("lib.exe")
        .map(|_| ToolCheck::Present { version: None })
        .unwrap_or(ToolCheck::Missing)
}

fn find_msvc_link() -> ToolCheck {
    find_in_path("link.exe")
        .map(|_| ToolCheck::Present { version: None })
        .unwrap_or(ToolCheck::Missing)
}

fn find_windows_sdk() -> ToolCheck {
    // Check for Windows SDK via environment variable
    if let Some(sdk_dir) = std::env::var_os("WindowsSdkDir") {
        let path = PathBuf::from(sdk_dir);
        if path.exists() {
            return ToolCheck::Present {
                version: Some(format!("{:?}", path)),
            };
        }
    }

    // Check common Windows SDK install locations
    let common_paths = [
        "C:\\Program Files (x86)\\Windows Kits\\10\\Include",
        "C:\\Program Files\\Windows Kits\\10\\Include",
    ];

    for path in &common_paths {
        if Path::new(path).exists() {
            return ToolCheck::Present {
                version: Some(path.to_string()),
            };
        }
    }

    ToolCheck::Missing
}

// ---------------------------------------------------------------------------
// Linux toolchain detection
// ---------------------------------------------------------------------------

fn find_gcc() -> ToolCheck {
    check_tool_with_flag("gcc", "--version")
}

fn find_gxx() -> ToolCheck {
    check_tool_with_flag("g++", "--version")
}

fn find_make() -> ToolCheck {
    check_tool_with_flag("make", "--version")
}

fn find_pkg_config() -> ToolCheck {
    check_tool_with_flag("pkg-config", "--version")
}

// ---------------------------------------------------------------------------
// macOS toolchain detection
// ---------------------------------------------------------------------------

fn find_clang() -> ToolCheck {
    check_tool_with_flag("clang", "--version")
}

fn find_xcode_select() -> ToolCheck {
    // xcode-select -p tells us if Xcode command line tools are installed
    match Command::new("xcode-select").arg("-p").output() {
        Ok(output) => {
            let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
            if !stdout.is_empty() {
                return ToolCheck::Present { version: Some(stdout) };
            }
            ToolCheck::Missing
        }
        Err(_) => ToolCheck::Missing,
    }
}

// ---------------------------------------------------------------------------
// Python detection (all platforms)
// ---------------------------------------------------------------------------

fn find_python() -> ToolCheck {
    check_tool_with_flag("python3", "--version")
}

fn find_python_windows() -> ToolCheck {
    check_tool_with_flag("python", "--version")
}

fn find_python_executable() -> ToolCheck {
    // Try the current interpreter first
    if let Ok(output) = Command::new("python3")
        .arg("-c")
        .arg("import sys; print(sys.executable)")
        .output()
    {
        let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
        if !path.is_empty() {
            return ToolCheck::Present { version: Some(path) };
        }
    }

    // Fallback to python
    if let Ok(output) = Command::new("python")
        .arg("-c")
        .arg("import sys; print(sys.executable)")
        .output()
    {
        let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
        if !path.is_empty() {
            return ToolCheck::Present { version: Some(path) };
        }
    }

    ToolCheck::Missing
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Run a build-config check for the given target.
pub fn check_build_config(target: &str) -> ForgerResult<BuildConfigResult> {
    let os = parse_target_os(target)?;
    let mut tools = Vec::new();
    let mut passed = true;

    match os.as_str() {
        "windows" => check_windows_tools(&mut tools, &mut passed),
        "linux" => check_linux_tools(&mut tools, &mut passed),
        "macos" => check_macos_tools(&mut tools, &mut passed),
        "android" => check_android_tools(&mut tools, &mut passed),
        "emscripten" => check_emscripten_tools(&mut tools, &mut passed),
        _ => {
            tools.push((format!("target: {os}"), ToolCheck::Misconfigured(
                "unknown target platform".into()
            )));
            passed = false;
        }
    }

    // Common tools
    tools.push(("python".into(), find_python_executable()));
    if tools.last().unwrap().1 == ToolCheck::Missing {
        passed = false;
    }

    Ok(BuildConfigResult {
        target: target.to_string(),
        tools,
        passed,
    })
}

// ---------------------------------------------------------------------------
// Platform-specific checks
// ---------------------------------------------------------------------------

fn check_windows_tools(tools: &mut Vec<(String, ToolCheck)>, passed: &mut bool) {
    let checks = [
        ("MSVC Compiler (cl.exe)", find_msvc_compiler()),
        ("MSVC Librarian (lib.exe)", find_msvc_lib()),
        ("MSVC Linker (link.exe)", find_msvc_link()),
        ("Windows SDK", find_windows_sdk()),
    ];

    for (name, check) in &checks {
        if matches!(check, ToolCheck::Missing | ToolCheck::Misconfigured(_)) {
            *passed = false;
        }
        tools.push((name.to_string(), check.clone()));
    }
}

fn check_linux_tools(tools: &mut Vec<(String, ToolCheck)>, passed: &mut bool) {
    let checks = [
        ("GCC", find_gcc()),
        ("G++", find_gxx()),
        ("Make", find_make()),
        ("pkg-config", find_pkg_config()),
    ];

    for (name, check) in &checks {
        if matches!(check, ToolCheck::Missing | ToolCheck::Misconfigured(_)) {
            *passed = false;
        }
        tools.push((name.to_string(), check.clone()));
    }
}

fn check_macos_tools(tools: &mut Vec<(String, ToolCheck)>, passed: &mut bool) {
    let checks = [
        ("Xcode Command Line Tools", find_xcode_select()),
        ("Clang", find_clang()),
    ];

    for (name, check) in &checks {
        if matches!(check, ToolCheck::Missing | ToolCheck::Misconfigured(_)) {
            *passed = false;
        }
        tools.push((name.to_string(), check.clone()));
    }
}

fn check_android_tools(tools: &mut Vec<(String, ToolCheck)>, passed: &mut bool) {
    let ndk_check = check_tool_with_flag("ndk-build", "--version");
    if matches!(ndk_check, ToolCheck::Missing | ToolCheck::Misconfigured(_)) {
        *passed = false;
    }
    tools.push(("Android NDK (ndk-build)".into(), ndk_check));

    let sdk_check = check_tool_with_flag("sdkmanager", "--version");
    if matches!(sdk_check, ToolCheck::Missing | ToolCheck::Misconfigured(_)) {
        *passed = false;
    }
    tools.push(("Android SDK Manager".into(), sdk_check));
}

fn check_emscripten_tools(tools: &mut Vec<(String, ToolCheck)>, passed: &mut bool) {
    let emcc_check = check_tool_with_flag("emcc", "--version");
    if matches!(emcc_check, ToolCheck::Missing | ToolCheck::Misconfigured(_)) {
        *passed = false;
    }
    tools.push(("Emscripten (emcc)".into(), emcc_check));
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Parse the OS portion of a target triple.
fn parse_target_os(target: &str) -> ForgerResult<String> {
    let parts: Vec<&str> = target.split('-').collect();
    if parts.is_empty() {
        return Err(ForgerError::InvalidArgument(format!(
            "invalid target triple: {target}"
        )));
    }
    Ok(parts[0].to_string())
}

/// Find an executable in PATH.
fn find_in_path(name: &str) -> Option<PathBuf> {
    for dir in std::env::var_os("PATH")
        .as_ref()
        .map(|p| std::env::split_paths(p))
        .unwrap_or_default()
    {
        let candidate = dir.join(name);
        if candidate.exists() {
            return Some(candidate);
        }
    }
    None
}

/// Check for a tool and optionally extract its version.
fn check_tool_with_flag(tool: &str, flag: &str) -> ToolCheck {
    match Command::new(tool).arg(flag).output() {
        Ok(output) => {
            let stdout = String::from_utf8_lossy(&output.stdout);
            let stderr = String::from_utf8_lossy(&output.stderr);
            let version = stdout
                .lines()
                .chain(stderr.lines())
                .next()
                .map(|l| l.trim().to_string());
            ToolCheck::Present { version }
        }
        Err(_) => ToolCheck::Missing,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_target_os() {
        assert_eq!(parse_target_os("windows-x64").unwrap(), "windows");
        assert_eq!(parse_target_os("linux-arm64-gnu").unwrap(), "linux");
        assert_eq!(parse_target_os("android-arm64").unwrap(), "android");
    }

    #[test]
    fn test_parse_target_os_invalid() {
        assert!(parse_target_os("").is_err());
    }

    #[test]
    fn test_tool_check_display() {
        assert_eq!(format!("{}", ToolCheck::Present { version: None }), "present");
        assert_eq!(format!("{}", ToolCheck::Missing), "missing");
        assert_eq!(
            format!("{}", ToolCheck::Misconfigured("reason".into())),
            "misconfigured (reason)"
        );
    }

    #[test]
    fn test_build_config_result_report() {
        let result = BuildConfigResult {
            target: "test-target".into(),
            tools: vec![
                ("tool1".into(), ToolCheck::Present { version: Some("1.0".into()) }),
                ("tool2".into(), ToolCheck::Missing),
            ],
            passed: false,
        };
        let report = result.format_report();
        assert!(report.contains("test-target"));
        assert!(report.contains("tool1"));
        assert!(report.contains("tool2"));
    }
}
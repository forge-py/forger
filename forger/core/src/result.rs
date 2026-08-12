//! Unified error handling for Forger core operations.

use thiserror::Error;

/// Error types that can occur during Forger operations.
#[derive(Debug, Error)]
pub enum ForgerError {
    #[error("filesystem error: {0}")]
    Filesystem(String),

    #[error("module resolution failed for '{0}': {1}")]
    ModuleResolution(String, String),

    #[error("dependency graph error: {0}")]
    DependencyGraph(String),

    #[error("artifact error: {0}")]
    Artifact(String),

    #[error("cache error: {0}")]
    Cache(String),

    #[error("VFS error: {0}")]
    VirtualFileSystem(String),

    #[error("hash computation failed: {0}")]
    Hash(String),

    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),

    #[error("Python interop error: {0}")]
    PythonInterop(String),

    #[error("invalid argument: {0}")]
    InvalidArgument(String),

    #[error("configuration error: {0}")]
    Configuration(String),
}

/// Result type alias for Forger operations.
pub type ForgerResult<T> = Result<T, ForgerError>;

impl ForgerError {
    /// Returns true if this error indicates a non-fatal warning.
    pub fn is_warning(&self) -> bool {
        matches!(
            self,
            ForgerError::ModuleResolution(_, _) | ForgerError::Cache(_)
        )
    }
}

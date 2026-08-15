//! Forger Core — High-performance infrastructure for Python application analysis and bundling.
//!
//! This crate provides the performance-critical foundation for Forger:
//! - Filesystem traversal and discovery
//! - Dependency graph construction and manipulation
//! - Module resolution and path normalization
//! - Hashing, caching, and incremental build detection
//! - `.forge` artifact serialization/deserialization
//! - Parallel processing orchestration
//! - CLI argument parsing (clap)
//! - Build configuration checking
//! - PyO3 bindings for Python integration

pub mod buildconfig;
pub mod cache;
pub mod cli;
pub mod cpython;
pub mod depgraph;
pub mod filesystem;
pub mod forge;
pub mod graphbuilder;
pub mod graphstore;
pub mod hash;
pub mod module;
pub mod pathutil;
pub mod result;
pub mod vfs;

// Re-export core types for convenient access
pub use buildconfig::{check_build_config, BuildConfigResult, ToolCheck};
pub use cache::Cache;
pub use cpython::{CpythonModuleRegistry, CpythonAnalysisResult, CpythonBuildConfig, CpythonSourceAnalyzer};
pub use depgraph::{DependencyGraph, DependencyNode, DependencyEdge, NodeType, EdgeType, EdgeProvenance};
pub use filesystem::{FileDiscovery, FileEntry, DiscoveryOptions};
pub use graphbuilder::GraphBuilder;
pub use graphstore::GraphStore;
pub use forge::{ForgeArtifact, ForgeManifest, ForgeBuilder};
pub use hash::{content_hash, content_hash_reader, HashValue};
pub use module::{ModuleResolver, ModuleSpec};
pub use pathutil::{normalize_path, normalize_path_buf, PathSet};
pub use result::{ForgerError, ForgerResult};
pub use vfs::{VirtualFileSystem, VfsNode, VfsEntry};

// PyO3 Python module binding
// Exposed as `forger._core` when built via maturin.
use pyo3::prelude::*;

/// Hash a byte slice using BLAKE3 and return the hex string.
#[pyfunction]
fn content_hash_bytes_py(data: &[u8]) -> PyResult<String> {
    Ok(hash::content_hash_bytes(data).0)
}

/// Check build configuration for the given target.
#[pyfunction]
fn check_build_config_py(target: String) -> PyResult<BuildConfigResult> {
    check_build_config(&target).map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
}

#[pymodule]
fn forger(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction_bound!(content_hash_bytes_py)(m).unwrap())?;
    m.add_function(wrap_pyfunction_bound!(check_build_config_py)(m).unwrap())?;
    m.add_class::<BuildConfigResult>()?;
    m.add_class::<CpythonModuleRegistry>()?;
    m.add_class::<CpythonAnalysisResult>()?;
    m.add_class::<CpythonBuildConfig>()?;
    Ok(())
}

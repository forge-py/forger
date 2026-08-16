//! Forger Core — High-performance infrastructure for Python application analysis and bundling.
//!
//! This crate provides the performance-critical foundation for Forger:
//! - Filesystem traversal and discovery
//! - Dependency graph construction and manipulation
//! - Module resolution and path normalization
//! - Hashing, caching, and incremental build detection
//! - `.forge` artifact serialization/deserialization
//! - Parallel processing orchestration
//! - Build configuration checking
//! - PyO3 bindings for Python integration
//!
//! The CLI is a Python entry point that calls into this library via PyO3.
//! No separate Rust binary is produced.

pub mod buildconfig;
pub mod cache;
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
pub use cpython::{
    CpythonAnalysisResult, CpythonBuildConfig, CpythonModuleRegistry, CpythonSourceAnalyzer,
};
pub use depgraph::{
    DependencyEdge, DependencyGraph, DependencyNode, EdgeProvenance, EdgeType, NodeType,
};
pub use filesystem::{DiscoveryOptions, FileDiscovery, FileEntry};
pub use forge::{ForgeArtifact, ForgeBuilder, ForgeManifest};
pub use graphbuilder::GraphBuilder;
pub use graphstore::GraphStore;
pub use hash::{content_hash, content_hash_reader, HashValue};
pub use module::{ModuleResolver, ModuleSpec};
pub use pathutil::{normalize_path, normalize_path_buf, PathSet};
pub use result::{ForgerError, ForgerResult};
pub use vfs::{VfsEntry, VfsNode, VirtualFileSystem};

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
fn forger_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction_bound!(content_hash_bytes_py)(m).unwrap())?;
    m.add_function(wrap_pyfunction_bound!(check_build_config_py)(m).unwrap())?;
    m.add_class::<BuildConfigResult>()?;
    m.add_class::<CpythonModuleRegistry>()?;
    m.add_class::<CpythonAnalysisResult>()?;
    m.add_class::<CpythonBuildConfig>()?;

    // Dependency graph types
    m.add_class::<NodeType>()?;
    m.add_class::<EdgeType>()?;
    m.add_class::<EdgeProvenance>()?;
    m.add_class::<DependencyNode>()?;
    m.add_class::<DependencyEdge>()?;
    m.add_class::<DependencyGraph>()?;

    // Graph builder and store
    m.add_class::<GraphBuilder>()?;
    m.add_class::<GraphStore>()?;

    Ok(())
}

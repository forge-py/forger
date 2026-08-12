//! Forger Core — High-performance infrastructure for Python application analysis and bundling.
//!
//! This crate provides the performance-critical foundation for Forger:
//! - Filesystem traversal and discovery
//! - Dependency graph construction and manipulation
//! - Module resolution and path normalization
//! - Hashing, caching, and incremental build detection
//! - `.forge` artifact serialization/deserialization
//! - Parallel processing orchestration

pub mod cache;
pub mod depgraph;
pub mod filesystem;
pub mod forge;
pub mod hash;
pub mod module;
pub mod pathutil;
pub mod result;
pub mod vfs;

// Re-export core types for convenient access
pub use cache::Cache;
pub use depgraph::{DependencyGraph, DependencyNode, DependencyEdge, NodeType, EdgeType, EdgeProvenance};
pub use filesystem::{FileDiscovery, FileEntry, DiscoveryOptions};
pub use forge::{ForgeArtifact, ForgeManifest, ForgeBuilder};
pub use hash::{content_hash, content_hash_reader, HashValue};
pub use module::{ModuleResolver, ModuleSpec};
pub use pathutil::{normalize_path, normalize_path_buf, PathSet};
pub use result::{ForgerError, ForgerResult};
pub use vfs::{VirtualFileSystem, VfsNode, VfsEntry};

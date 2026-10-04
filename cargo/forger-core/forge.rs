//! `.forge` artifact format — serialization, deserialization, and building.
//!
//! The `.forge` file is the intermediate application artifact produced by
//! `forger compile`. It contains everything needed to build for any target
//! without re-analyzing the source.

use std::collections::HashMap;
use std::fs;
use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::depgraph::{DependencyGraph, NodeType};
use crate::hash::{content_hash, content_hash_bytes, HashValue};
use crate::result::{ForgerError, ForgerResult};

/// Magic bytes at the start of a .forge file.
const FORGE_MAGIC: [u8; 4] = [b'F', b'R', b'G', b'0'];

/// Current format version.
const FORGE_VERSION: u32 = 1;

/// Header of a .forge artifact.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ForgeHeader {
    /// Format version for forward/backward compatibility.
    pub version: u32,
    /// Content hash of the source tree at compile time.
    pub source_hash: String,
    /// Python version(s) this artifact is compatible with.
    pub python_versions: Vec<String>,
    /// Build timestamp (for diagnostics, not used for reproducibility).
    pub build_timestamp: Option<u64>,
    /// Forger version that produced this artifact.
    pub forger_version: String,
}

/// Manifest describing the contents of a .forge artifact.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ForgeManifest {
    /// Project name.
    pub name: String,
    /// Project version.
    pub version: Option<String>,
    /// Entry point module.
    pub entry_point: String,
    /// Number of Python modules.
    pub python_module_count: usize,
    /// Number of stdlib modules.
    pub stdlib_module_count: usize,
    /// Number of native extensions.
    pub native_extension_count: usize,
    /// Number of resources.
    pub resource_count: usize,
    /// Target platforms for which artifacts are available.
    pub available_targets: Vec<String>,
    /// Dependency graph summary.
    pub dep_graph_summary: ForgeDepGraphSummary,
    /// Metadata.
    pub metadata: HashMap<String, String>,
}

/// Summary of the dependency graph for quick inspection.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ForgeDepGraphSummary {
    pub node_count: usize,
    pub edge_count: usize,
    pub entry_points: Vec<String>,
}

/// A file entry within the .forge artifact.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ForgeFileEntry {
    /// Logical path within the VFS.
    pub vfs_path: String,
    /// Source path (for diagnostics).
    pub source_path: Option<String>,
    /// Content hash.
    pub hash: String,
    /// Size in bytes.
    pub size: u64,
    /// Whether this file is required (reachable).
    pub required: bool,
    /// Target platform (None = universal).
    pub target: Option<String>,
    /// Compression type used.
    pub compression: Option<String>,
}

/// Target platform specification.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub struct TargetPlatform {
    pub os: String,
    pub arch: String,
    pub env: Option<String>,
}

impl TargetPlatform {
    /// Parse from triple string like "windows-x64" or "linux-arm64".
    pub fn parse(triple: &str) -> ForgerResult<Self> {
        let parts: Vec<&str> = triple.split('-').collect();
        if parts.len() < 2 {
            return Err(ForgerError::InvalidArgument(format!(
                "invalid target triple: {triple}"
            )));
        }

        Ok(Self {
            os: parts[0].to_string(),
            arch: parts[1].to_string(),
            env: parts.get(2).map(|s| s.to_string()),
        })
    }

    /// Format as triple string.
    pub fn to_triple(&self) -> String {
        match &self.env {
            Some(env) => format!("{}-{}-{}", self.os, self.arch, env),
            None => format!("{}-{}", self.os, self.arch),
        }
    }
}

impl std::fmt::Display for TargetPlatform {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.to_triple())
    }
}

/// The complete .forge artifact in memory.
#[derive(Debug, Clone)]
pub struct ForgeArtifact {
    /// Header with version and metadata.
    pub header: ForgeHeader,
    /// Manifest describing contents.
    pub manifest: ForgeManifest,
    /// Dependency graph.
    pub dep_graph: DependencyGraph,
    /// File entries to be bundled.
    pub files: Vec<ForgeFileEntry>,
    /// Raw file data (for in-memory artifacts).
    pub file_data: HashMap<String, Vec<u8>>,
}

impl ForgeArtifact {
    /// Create a new empty artifact.
    pub fn new(name: impl Into<String>, entry_point: impl Into<String>) -> Self {
        Self {
            header: ForgeHeader {
                version: FORGE_VERSION,
                source_hash: String::new(),
                python_versions: vec!["3.10".into(), "3.11".into(), "3.12".into()],
                build_timestamp: None,
                forger_version: env!("CARGO_PKG_VERSION").to_string(),
            },
            manifest: ForgeManifest {
                name: name.into(),
                version: None,
                entry_point: entry_point.into(),
                python_module_count: 0,
                stdlib_module_count: 0,
                native_extension_count: 0,
                resource_count: 0,
                available_targets: vec![],
                dep_graph_summary: ForgeDepGraphSummary {
                    node_count: 0,
                    edge_count: 0,
                    entry_points: vec![],
                },
                metadata: HashMap::new(),
            },
            dep_graph: DependencyGraph::new(),
            files: Vec::new(),
            file_data: HashMap::new(),
        }
    }

    /// Add a file to the artifact.
    pub fn add_file(
        &mut self,
        vfs_path: impl Into<String>,
        source_path: impl AsRef<Path>,
        target: Option<String>,
    ) -> ForgerResult<()> {
        let vfs_path = vfs_path.into();
        let source_path = source_path.as_ref();

        let metadata = fs::metadata(source_path).map_err(|e| {
            ForgerError::Filesystem(format!("cannot read metadata for {:?}: {e}", source_path))
        })?;

        let hash = content_hash(source_path).map_err(|e| {
            ForgerError::Filesystem(format!("cannot hash {:?}: {e}", source_path))
        })?;

        let data = fs::read(source_path).map_err(|e| {
            ForgerError::Filesystem(format!("cannot read {:?}: {e}", source_path))
        })?;

        self.files.push(ForgeFileEntry {
            vfs_path: vfs_path.clone(),
            source_path: Some(source_path.to_string_lossy().to_string()),
            hash: hash.0,
            size: metadata.len(),
            required: true,
            target: target.clone(),
            compression: None,
        });

        self.file_data.insert(vfs_path, data);

        // Update counts
        self.update_manifest_counts();

        Ok(())
    }

    /// Add file data directly (without reading from disk).
    pub fn add_file_data(
        &mut self,
        vfs_path: impl Into<String>,
        data: Vec<u8>,
        target: Option<String>,
    ) {
        let vfs_path = vfs_path.into();
        let hash = content_hash_bytes(&data);

        self.files.push(ForgeFileEntry {
            vfs_path: vfs_path.clone(),
            source_path: None,
            hash: hash.0,
            size: data.len() as u64,
            required: true,
            target,
            compression: None,
        });

        self.file_data.insert(vfs_path, data);
        self.update_manifest_counts();
    }

    /// Update manifest counts from current state.
    fn update_manifest_counts(&mut self) {
        self.manifest.python_module_count = self
            .dep_graph
            .nodes_by_type(NodeType::PythonModule)
            .len();
        self.manifest.stdlib_module_count = self
            .dep_graph
            .nodes_by_type(NodeType::StdlibModule)
            .len();
        self.manifest.native_extension_count = self
            .dep_graph
            .nodes_by_type(NodeType::NativeExtension)
            .len();
        self.manifest.resource_count = self.dep_graph.nodes_by_type(NodeType::Resource).len();
        self.manifest.dep_graph_summary.node_count = self.dep_graph.node_count();
        self.manifest.dep_graph_summary.edge_count = self.dep_graph.edge_count();
        self.manifest
            .dep_graph_summary
            .entry_points = self.dep_graph.entry_points().to_vec();
    }

    /// Compute the overall artifact hash.
    pub fn compute_hash(&self) -> HashValue {
        use crate::hash::combine_hashes;

        let mut hashes = Vec::new();

        // Hash all file entries
        for file in &self.files {
            hashes.push(HashValue(file.hash.clone()));
        }

        // Hash the dependency graph structure
        let graph_str = format!(
            "{}:{}:{}",
            self.dep_graph.node_count(),
            self.dep_graph.edge_count(),
            self.dep_graph.entry_points().len()
        );
        hashes.push(content_hash_bytes(graph_str.as_bytes()));

        combine_hashes(&hashes)
    }

    /// Serialize to bytes.
    pub fn serialize(&self) -> ForgerResult<Vec<u8>> {
        let mut output = Vec::new();

        // Write magic
        output.extend_from_slice(&FORGE_MAGIC);

        // Write version
        output.extend_from_slice(&self.header.version.to_le_bytes());

        // Serialize header
        let header_data = serde_json::to_vec(&self.header).map_err(|e| {
            ForgerError::Artifact(format!("cannot serialize header: {e}"))
        })?;
        output.extend_from_slice(&(header_data.len() as u32).to_le_bytes());
        output.extend_from_slice(&header_data);

        // Serialize manifest
        let manifest_data = serde_json::to_vec(&self.manifest).map_err(|e| {
            ForgerError::Artifact(format!("cannot serialize manifest: {e}"))
        })?;
        output.extend_from_slice(&(manifest_data.len() as u32).to_le_bytes());
        output.extend_from_slice(&manifest_data);

        // Serialize file entries
        let file_entries_data = serde_json::to_vec(&self.files).map_err(|e| {
            ForgerError::Artifact(format!("cannot serialize file entries: {e}"))
        })?;
        output.extend_from_slice(&(file_entries_data.len() as u32).to_le_bytes());
        output.extend_from_slice(&file_entries_data);

        // Write file data with offsets
        for file_entry in &self.files {
            if let Some(data) = self.file_data.get(&file_entry.vfs_path) {
                output.extend_from_slice(&(data.len() as u32).to_le_bytes());
                output.extend_from_slice(data);
            }
        }

        Ok(output)
    }

    /// Deserialize from bytes.
    pub fn deserialize(data: &[u8]) -> ForgerResult<Self> {
        if data.len() < 8 {
            return Err(ForgerError::Artifact("file too small for header".into()));
        }

        // Check magic
        if data[..4] != FORGE_MAGIC {
            return Err(ForgerError::Artifact("invalid magic bytes".into()));
        }

        // Read version
        let version = u32::from_le_bytes([data[4], data[5], data[6], data[7]]);
        if version != FORGE_VERSION {
            return Err(ForgerError::Artifact(format!(
                "unsupported format version: {version}"
            )));
        }

        let mut offset = 8;

        // Read header
        let (header, _hdr) = Self::read_json::<ForgeHeader>(data, &mut offset)?;

        // Read manifest
        let (manifest, _mst) = Self::read_json::<ForgeManifest>(data, &mut offset)?;

        // Read file entries
        let (_file_entries, _fee) = Self::read_json::<Vec<ForgeFileEntry>>(data, &mut offset)?;

        Ok(Self {
            header,
            manifest,
            dep_graph: DependencyGraph::new(),
            files: Vec::new(),
            file_data: HashMap::new(),
        })
    }

    /// Helper to read a JSON-serialized struct from the data stream.
    fn read_json<T: serde::de::DeserializeOwned>(
        data: &[u8],
        offset: &mut usize,
    ) -> ForgerResult<(T, usize)> {
        if *offset + 4 > data.len() {
            return Err(ForgerError::Artifact("truncated length field".into()));
        }

        let len = u32::from_le_bytes([
            data[*offset],
            data[*offset + 1],
            data[*offset + 2],
            data[*offset + 3],
        ]) as usize;
        *offset += 4;

        if *offset + len > data.len() {
            return Err(ForgerError::Artifact("truncated JSON data".into()));
        }

        let json_data = &data[*offset..*offset + len];
        let value: T = serde_json::from_slice(json_data).map_err(|e| {
            ForgerError::Artifact(format!("cannot deserialize JSON: {e}"))
        })?;

        *offset += len;
        Ok((value, len))
    }

    /// Write the artifact to a file.
    pub fn write_to_path(&self, path: &Path) -> ForgerResult<()> {
        let data = self.serialize()?;
        fs::write(path, data).map_err(|e| {
            ForgerError::Filesystem(format!("cannot write artifact to {:?}: {e}", path))
        })?;
        Ok(())
    }

    /// Read an artifact from a file.
    pub fn read_from_path(path: &Path) -> ForgerResult<Self> {
        let data = fs::read(path).map_err(|e| {
            ForgerError::Filesystem(format!("cannot read artifact from {:?}: {e}", path))
        })?;
        Self::deserialize(&data)
    }

    /// Get the total size of all file data.
    pub fn total_data_size(&self) -> u64 {
        self.file_data.values().map(|v| v.len() as u64).sum()
    }

    /// Filter files for a specific target.
    pub fn files_for_target(&self, target: &str) -> Vec<&ForgeFileEntry> {
        self.files
            .iter()
            .filter(|f| f.target.as_deref() == Some(target) || f.target.is_none())
            .collect()
    }

    /// Get diagnostic summary.
    pub fn diagnostic_summary(&self) -> String {
        format!(
            "Forge Artifact: {}\n\
             Version: {}\n\
             Entry Point: {}\n\
             Nodes: {}\n\
             Edges: {}\n\
             Files: {}\n\
             Total Size: {} bytes\n\
             Available Targets: {:?}",
            self.manifest.name,
            self.header.version,
            self.manifest.entry_point,
            self.dep_graph.node_count(),
            self.dep_graph.edge_count(),
            self.files.len(),
            self.total_data_size(),
            self.manifest.available_targets
        )
    }
}

/// Builder for constructing .forge artifacts.
pub struct ForgeBuilder {
    artifact: ForgeArtifact,
}

impl ForgeBuilder {
    /// Create a new builder.
    pub fn new(name: impl Into<String>, entry_point: impl Into<String>) -> Self {
        Self {
            artifact: ForgeArtifact::new(name, entry_point),
        }
    }

    /// Set the project version.
    pub fn version(mut self, version: impl Into<String>) -> Self {
        self.artifact.manifest.version = Some(version.into());
        self
    }

    /// Add a dependency graph.
    pub fn with_dep_graph(mut self, graph: DependencyGraph) -> Self {
        self.artifact.dep_graph = graph;
        self.artifact.update_manifest_counts();
        self
    }

    /// Add a file.
    pub fn add_file(
        mut self,
        vfs_path: impl Into<String>,
        source_path: impl AsRef<Path>,
        target: Option<String>,
    ) -> ForgerResult<Self> {
        self.artifact.add_file(vfs_path, source_path, target)?;
        Ok(self)
    }

    /// Add raw file data.
    pub fn add_file_data(
        mut self,
        vfs_path: impl Into<String>,
        data: Vec<u8>,
        target: Option<String>,
    ) -> Self {
        self.artifact
            .add_file_data(vfs_path, data, target);
        self
    }

    /// Add metadata.
    pub fn with_metadata<K: Into<String>, V: Into<String>>(mut self, key: K, value: V) -> Self {
        self.artifact
            .manifest
            .metadata
            .insert(key.into(), value.into());
        self
    }

    /// Add an available target.
    pub fn with_target(mut self, target: String) -> Self {
        self.artifact
            .manifest
            .available_targets
            .push(target);
        self
    }

    /// Set source hash.
    pub fn with_source_hash(mut self, hash: String) -> Self {
        self.artifact.header.source_hash = hash;
        self
    }

    /// Build the final artifact.
    pub fn build(self) -> ForgeArtifact {
        self.artifact
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn test_create_artifact() {
        let artifact = ForgeArtifact::new("test_project", "main");
        assert_eq!(artifact.manifest.name, "test_project");
        assert_eq!(artifact.manifest.entry_point, "main");
        assert_eq!(artifact.header.version, FORGE_VERSION);
    }

    #[test]
    fn test_add_file_data() {
        let mut artifact = ForgeArtifact::new("test", "main");
        artifact.add_file_data("test.py", b"x = 1".to_vec(), None);

        assert_eq!(artifact.files.len(), 1);
        assert_eq!(artifact.total_data_size(), 5);
    }

    #[test]
    fn test_serialize_roundtrip() {
        let mut artifact = ForgeArtifact::new("test", "main");
        artifact.add_file_data("hello.py", b"print('hi')".to_vec(), None);

        let data = artifact.serialize().unwrap();
        assert!(data.starts_with(&FORGE_MAGIC));

        let deserialized = ForgeArtifact::deserialize(&data).unwrap();
        assert_eq!(deserialized.manifest.name, "test");
    }

    #[test]
    fn test_write_and_read() {
        let tmp = TempDir::with_prefix("forger-test").unwrap();
        let path = tmp.path().join("test.forge");

        let mut artifact = ForgeArtifact::new("test", "main");
        artifact.add_file_data("test.py", b"pass".to_vec(), None);
        artifact.write_to_path(&path).unwrap();

        let loaded = ForgeArtifact::read_from_path(&path).unwrap();
        assert_eq!(loaded.manifest.name, "test");
    }

    #[test]
    fn test_forge_builder() {
        let artifact = ForgeBuilder::new("builder_test", "entry")
            .version("1.0.0")
            .add_file_data("mod.py", b"y = 2".to_vec(), None)
            .with_metadata("author", "test")
            .build();

        assert_eq!(artifact.manifest.name, "builder_test");
        assert_eq!(artifact.manifest.version, Some("1.0.0".into()));
        assert_eq!(artifact.files.len(), 1);
    }

    #[test]
    fn test_target_platform_parse() {
        let target = TargetPlatform::parse("windows-x64").unwrap();
        assert_eq!(target.os, "windows");
        assert_eq!(target.arch, "x64");
        assert_eq!(target.to_triple(), "windows-x64");

        let target = TargetPlatform::parse("linux-arm64-gnu").unwrap();
        assert_eq!(target.to_triple(), "linux-arm64-gnu");
    }

    #[test]
    fn test_diagnostic_summary() {
        let artifact = ForgeArtifact::new("diag_test", "main");
        let summary = artifact.diagnostic_summary();
        assert!(summary.contains("diag_test"));
        assert!(summary.contains("main"));
    }

    #[test]
    fn test_invalid_magic() {
        let data = vec![0, 0, 0, 0, 0, 0, 0, 1];
        assert!(ForgeArtifact::deserialize(&data).is_err());
    }
}

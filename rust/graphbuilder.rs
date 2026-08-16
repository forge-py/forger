//! Parallel graph builder using Rust for filesystem traversal.
//!
//! Discovers files across all CPU cores, populates dependency graph nodes
//! with source content directly (not file references).

use std::fs;
use std::path::PathBuf;

use pyo3::prelude::*;
use rayon::prelude::*;

use crate::depgraph::{DependencyGraph, DependencyNode, NodeType};
use crate::filesystem::{FileDiscovery, DiscoveryOptions, FileType};
use crate::result::ForgerResult;

/// Builder that constructs a DependencyGraph from a project root.
///
/// Uses parallel filesystem discovery to populate nodes with content.
#[pyclass]
pub struct GraphBuilder {
    entry_point: String,
    options: DiscoveryOptions,
}

impl GraphBuilder {
    /// Create a new GraphBuilder for the given project root and entry point.
    pub fn new(project_root: impl Into<PathBuf>, entry_point: impl Into<String>) -> Self {
        let root = project_root.into();
        Self {
            entry_point: entry_point.into(),
            options: DiscoveryOptions::for_python_project(root),
        }
    }

    /// Set custom discovery options.
    pub fn with_options(mut self, options: DiscoveryOptions) -> Self {
        self.options = options;
        self
    }

    /// Build the dependency graph by discovering files in parallel.
    ///
    /// Each discovered file becomes a DependencyNode with content loaded
    /// for Python source, configuration, and resource files.
    pub fn build_parallel(&self) -> ForgerResult<DependencyGraph> {
        let discovery = FileDiscovery::new(self.options.clone());
        let file_entries = discovery.discover()?;

        let mut graph = DependencyGraph::with_capacity(file_entries.len());

        // Add entry point node - convert file path to module name if needed
        let entry_module = path_to_module_name(
            std::path::Path::new(&self.entry_point),
        );
        graph.add_node(DependencyNode::new(entry_module.clone(), NodeType::EntryPoint));
        graph.add_entry_point(entry_module);

        // Classify and build nodes in parallel
        let nodes: Vec<DependencyNode> = file_entries
            .par_iter()
            .filter_map(|entry| {
                let node_type = classify_file_type(entry.file_type);
                let module_name = path_to_module_name(&entry.relative);

                let mut node = DependencyNode::new(module_name, node_type);
                node.path = Some(entry.path.clone());
                node.size = Some(entry.size);

                // Read content for text-based files
                let should_read_content = matches!(
                    entry.file_type,
                    FileType::PythonSource
                        | FileType::DataResource
                        | FileType::Documentation
                        | FileType::Configuration
                );

                if should_read_content {
                    if let Ok(content) = fs::read_to_string(&entry.path) {
                        node.content = Some(content);
                    }
                }

                Some(node)
            })
            .collect();

        for node in nodes {
            let node_id = node.id.clone();
            let node_content = node.content.clone();
            if !graph.add_node(node) {
                // Node already exists (e.g., entry point). If the existing node
                // lacks content but the discovered file has it, fill it in.
                if let Some(existing) = graph.get_node_mut(&node_id) {
                    if existing.content.is_none() {
                        existing.content = node_content;
                    }
                }
            }
        }

        Ok(graph)
    }

    /// Build the graph and return both the graph and a content index
    /// mapping node IDs to their content lengths (for diagnostics).
    pub fn build_with_stats(&self) -> ForgerResult<(DependencyGraph, usize, usize)> {
        let graph = self.build_parallel()?;
        let total_content: usize = graph
            .all_nodes()
            .filter_map(|(_, n)| n.get_content().map(|c| c.len()))
            .sum();
        let node_count = graph.node_count();
        Ok((graph, node_count, total_content))
    }
}

/// Classify a FileType into a NodeType for the dependency graph.
fn classify_file_type(file_type: FileType) -> NodeType {
    match file_type {
        FileType::PythonSource => NodeType::PythonModule,
        FileType::Configuration => NodeType::Configuration,
        FileType::NativeExtension => NodeType::NativeExtension,
        FileType::NativeLibrary => NodeType::NativeLibrary,
        FileType::PythonBytecode => NodeType::PythonModule,
        FileType::Documentation => NodeType::Resource,
        FileType::DataResource => NodeType::Resource,
        FileType::Other => NodeType::Resource,
    }
}

/// Convert a relative file path to a Python module name.
/// e.g., "app/views/main.py" -> "app.views.main"
/// Non-Python files return the relative path unchanged.
fn path_to_module_name(relative: &std::path::Path) -> String {
    let ext = relative.extension().and_then(|e| e.to_str());
    if ext == Some("py") || ext == Some("pyi") {
        let mut parts: Vec<String> = relative
            .components()
            .map(|c| c.as_os_str().to_string_lossy().to_string())
            .collect();
        if let Some(last) = parts.last_mut() {
            // Strip .py or .pyi extension
            if last.ends_with(".py") {
                last.truncate(last.len() - 3);
            } else if last.ends_with(".pyi") {
                last.truncate(last.len() - 4);
            }
        }
        // Skip __init__
        parts.retain(|p| p != "__init__");
        if parts.is_empty() {
            return relative.to_string_lossy().to_string();
        }
        parts.join(".")
    } else {
        // Non-Python files: use relative path as-is
        relative.to_string_lossy().to_string()
    }
}

// --- PyO3 bindings ---

#[pymethods]
impl GraphBuilder {
    #[new]
    fn py_new(project_root: String, entry_point: String) -> Self {
        GraphBuilder::new(PathBuf::from(project_root), entry_point)
    }

    /// Build the dependency graph by discovering files in parallel.
    #[pyo3(name = "build_parallel")]
    fn _py_build_parallel(&self) -> PyResult<DependencyGraph> {
        GraphBuilder::build_parallel(self)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    /// Build the graph and return (graph, node_count, total_content_bytes).
    #[pyo3(name = "build_with_stats")]
    fn _py_build_with_stats(&self) -> PyResult<(DependencyGraph, usize, usize)> {
        GraphBuilder::build_with_stats(self)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    fn __repr__(&self) -> String {
        format!("GraphBuilder(entry_point={})", self.entry_point)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn test_graph_builder_empty() {
        let tmp = TempDir::with_prefix("forger-test").unwrap();
        let builder = GraphBuilder::new(tmp.path(), "main.py");
        let graph = builder.build_parallel().unwrap();
        assert_eq!(graph.node_count(), 1); // entry point only
    }

    #[test]
    fn test_graph_builder_with_files() {
        let tmp = TempDir::with_prefix("forger-test").unwrap();
        std::fs::write(tmp.path().join("main.py"), "print('hello')").unwrap();
        std::fs::write(tmp.path().join("utils.py"), "def util(): pass").unwrap();
        std::fs::create_dir_all(tmp.path().join("templates")).unwrap();
        std::fs::write(
            tmp.path().join("templates").join("index.html"),
            "<html></html>",
        )
        .unwrap();

        let builder = GraphBuilder::new(tmp.path(), "main");
        let graph = builder.build_parallel().unwrap();

        // 3 files discovered (entry point is one of them)
        assert_eq!(graph.node_count(), 3);

        // Python files should have content (node ID is module name, not file path)
        let main_node = graph.get_node("main").unwrap();
        assert!(main_node.content.is_some());
        assert_eq!(main_node.content.as_ref().unwrap(), "print('hello')");

        // Non-Python files use relative path as node ID (may use OS separator)
        assert!(graph.get_node("templates/index.html").is_some()
            || graph.get_node(r"templates\index.html").is_some());
    }

    #[test]
    fn test_classify_file_type() {
        assert_eq!(classify_file_type(FileType::PythonSource), NodeType::PythonModule);
        assert_eq!(classify_file_type(FileType::Configuration), NodeType::Configuration);
        assert_eq!(classify_file_type(FileType::DataResource), NodeType::Resource);
        assert_eq!(
            classify_file_type(FileType::NativeExtension),
            NodeType::NativeExtension
        );
    }
}

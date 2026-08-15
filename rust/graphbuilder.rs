//! Parallel graph builder using Rust for filesystem traversal.
//!
//! Discovers files across all CPU cores, populates dependency graph nodes
//! with source content directly (not file references).

use std::fs;
use std::path::PathBuf;

use rayon::prelude::*;

use crate::depgraph::{DependencyGraph, DependencyNode, NodeType};
use crate::filesystem::{FileDiscovery, DiscoveryOptions, FileType};
use crate::result::ForgerResult;

/// Builder that constructs a DependencyGraph from a project root.
///
/// Uses parallel filesystem discovery to populate nodes with content.
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

        // Add entry point node
        graph.add_node(DependencyNode::new(self.entry_point.clone(), NodeType::EntryPoint));
        graph.add_entry_point(self.entry_point.clone());

        // Classify and build nodes in parallel
        let nodes: Vec<DependencyNode> = file_entries
            .par_iter()
            .filter_map(|entry| {
                let node_type = classify_file_type(entry.file_type);

                let mut node = DependencyNode::new(entry.normalized.clone(), node_type);
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
            graph.add_node(node);
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

        let builder = GraphBuilder::new(tmp.path(), "main.py");
        let graph = builder.build_parallel().unwrap();

        // Entry point + 3 files
        assert_eq!(graph.node_count(), 4);

        // Python files should have content
        let main_node = graph.get_node("main.py").unwrap();
        assert!(main_node.content.is_some());
        assert_eq!(main_node.content.as_ref().unwrap(), "print('hello')");
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

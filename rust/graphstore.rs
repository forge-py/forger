//! Filesystem-backed storage for dependency graphs.
//!
//! Persists graph data to `.forger/graph/` directory so that large graphs
//! can be stored on disk and reconstructed later.

use std::fs;
use std::path::PathBuf;

use pyo3::prelude::*;

use crate::depgraph::{DependencyGraph, DependencyNode, DependencyEdge};
use crate::result::{ForgerError, ForgerResult};

/// Filesystem-backed storage for dependency graphs.
///
/// Stores graph data in a directory structure:
/// - `nodes.json` — serialized nodes
/// - `edges.json` — serialized edges
/// - `entrypoints.json` — entry point IDs
/// - `metadata.json` — graph-level metadata
#[pyclass]
pub struct GraphStore {
    base_path: PathBuf,
}

impl GraphStore {
    /// Create a new GraphStore backed by the given directory.
    pub fn new(base_path: impl Into<PathBuf>) -> Self {
        Self {
            base_path: base_path.into(),
        }
    }

    /// Ensure the storage directory exists.
    fn ensure_dirs(&self) -> ForgerResult<()> {
        fs::create_dir_all(&self.base_path)
            .map_err(|e| ForgerError::Filesystem(format!("cannot create graph store dir: {e}")))
    }

    /// Persist an entire DependencyGraph to disk.
    pub fn flush(&self, graph: &DependencyGraph) -> ForgerResult<()> {
        self.ensure_dirs()?;

        // Serialize nodes
        let nodes: Vec<DependencyNode> = graph
            .all_nodes()
            .map(|(_, n)| n.clone())
            .collect();
        let nodes_json = serde_json::to_string_pretty(&nodes)
            .map_err(|e| ForgerError::DependencyGraph(format!("failed to serialize nodes: {e}")))?;
        fs::write(self.base_path.join("nodes.json"), nodes_json)
            .map_err(|e| ForgerError::Filesystem(format!("failed to write nodes: {e}")))?;

        // Serialize edges
        let edges: Vec<DependencyEdge> = graph.all_edges().cloned().collect();
        let edges_json = serde_json::to_string_pretty(&edges)
            .map_err(|e| ForgerError::DependencyGraph(format!("failed to serialize edges: {e}")))?;
        fs::write(self.base_path.join("edges.json"), edges_json)
            .map_err(|e| ForgerError::Filesystem(format!("failed to write edges: {e}")))?;

        // Serialize entry points
        let eps_json = serde_json::to_string_pretty(graph.entry_points())
            .map_err(|e| ForgerError::DependencyGraph(format!("failed to serialize entry points: {e}")))?;
        fs::write(self.base_path.join("entrypoints.json"), eps_json)
            .map_err(|e| ForgerError::Filesystem(format!("failed to write entry points: {e}")))?;

        // Serialize graph metadata
        let meta: Vec<(&String, &String)> = graph
            .get_metadata_entries()
            .collect();
        let meta_json = serde_json::to_string_pretty(&meta)
            .map_err(|e| ForgerError::DependencyGraph(format!("failed to serialize metadata: {e}")))?;
        fs::write(self.base_path.join("metadata.json"), meta_json)
            .map_err(|e| ForgerError::Filesystem(format!("failed to write metadata: {e}")))?;

        Ok(())
    }

    /// Load a DependencyGraph from disk.
    pub fn load(&self) -> ForgerResult<DependencyGraph> {
        let mut graph = DependencyGraph::new();

        // Load nodes
        let nodes_path = self.base_path.join("nodes.json");
        if nodes_path.exists() {
            let nodes: Vec<DependencyNode> = serde_json::from_reader(
                fs::File::open(&nodes_path)
                    .map_err(|e| ForgerError::Filesystem(format!("failed to read nodes: {e}")))?,
            ).map_err(|e| ForgerError::DependencyGraph(format!("failed to deserialize nodes: {e}")))?;
            for node in nodes {
                graph.add_node(node);
            }
        }

        // Load edges
        let edges_path = self.base_path.join("edges.json");
        if edges_path.exists() {
            let edges: Vec<DependencyEdge> = serde_json::from_reader(
                fs::File::open(&edges_path)
                    .map_err(|e| ForgerError::Filesystem(format!("failed to read edges: {e}")))?,
            ).map_err(|e| ForgerError::DependencyGraph(format!("failed to deserialize edges: {e}")))?;
            for edge in edges {
                graph.add_edge(edge);
            }
        }

        // Load entry points
        let eps_path = self.base_path.join("entrypoints.json");
        if eps_path.exists() {
            let entry_points: Vec<String> = serde_json::from_reader(
                fs::File::open(&eps_path)
                    .map_err(|e| ForgerError::Filesystem(format!("failed to read entry points: {e}")))?,
            ).map_err(|e| ForgerError::DependencyGraph(format!("failed to deserialize entry points: {e}")))?;
            for ep in entry_points {
                graph.add_entry_point(ep);
            }
        }

        Ok(graph)
    }

    /// Check if the store has persisted data.
    pub fn is_empty(&self) -> bool {
        !self.base_path.join("nodes.json").exists()
    }

    /// Clear all persisted data.
    pub fn clear(&self) -> ForgerResult<()> {
        if self.base_path.exists() {
            fs::remove_dir_all(&self.base_path)
                .map_err(|e| ForgerError::Filesystem(format!("failed to clear graph store: {e}")))?;
        }
        Ok(())
    }
}

// --- PyO3 bindings ---

#[pymethods]
impl GraphStore {
    #[new]
    fn py_new(base_path: String) -> Self {
        GraphStore::new(PathBuf::from(base_path))
    }

    /// Persist an entire DependencyGraph to disk.
    #[pyo3(name = "flush")]
    fn _py_flush(&self, graph: &DependencyGraph) -> PyResult<()> {
        GraphStore::flush(self, graph)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    /// Load a DependencyGraph from disk.
    #[pyo3(name = "load")]
    fn _py_load(&self) -> PyResult<DependencyGraph> {
        GraphStore::load(self)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    /// Check if the store has persisted data.
    #[pyo3(name = "is_empty")]
    fn _py_is_empty(&self) -> bool {
        GraphStore::is_empty(self)
    }

    /// Clear all persisted data.
    #[pyo3(name = "clear")]
    fn _py_clear(&self) -> PyResult<()> {
        GraphStore::clear(self)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    fn __repr__(&self) -> String {
        format!("GraphStore(path={})", self.base_path.display())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::depgraph::{NodeType, EdgeType, EdgeProvenance};
    use tempfile::TempDir;

    #[test]
    fn test_flush_and_load_roundtrip() {
        let tmp = TempDir::with_prefix("forger-store").unwrap();
        let store = GraphStore::new(tmp.path());

        // Create a graph
        let mut graph = DependencyGraph::new();
        let mut node1 = DependencyNode::new("main.py", NodeType::PythonModule);
        node1.content = Some("print('hello')".into());
        graph.add_node(node1);

        let node2 = DependencyNode::new("utils.py", NodeType::PythonModule);
        graph.add_node(node2);

        graph.add_edge(DependencyEdge::new(
            "main.py",
            "utils.py",
            EdgeType::Import(),
            EdgeProvenance {
                source: Some(("main.py".into(), 1)),
                discovered_by: "test".into(),
                description: None,
            },
        ));
        graph.add_entry_point("main.py");

        // Flush
        store.flush(&graph).unwrap();

        // Load
        let loaded = store.load().unwrap();
        assert_eq!(loaded.node_count(), 2);
        assert!(loaded.get_node("main.py").is_some());
        assert!(loaded.get_node("utils.py").is_some());

        // Content should be preserved
        let main_node = loaded.get_node("main.py").unwrap();
        assert_eq!(main_node.content.as_ref().map(|s| s.as_str()), Some("print('hello')"));
    }

    #[test]
    fn test_load_empty_store() {
        let tmp = TempDir::with_prefix("forger-store").unwrap();
        let store = GraphStore::new(tmp.path());
        let graph = store.load().unwrap();
        assert_eq!(graph.node_count(), 0);
    }

    #[test]
    fn test_clear_store() {
        let tmp = TempDir::with_prefix("forger-store").unwrap();
        let store = GraphStore::new(tmp.path());

        let mut graph = DependencyGraph::new();
        graph.add_node(DependencyNode::new("test.py", NodeType::PythonModule));
        store.flush(&graph).unwrap();

        store.clear().unwrap();
        assert!(store.is_empty());
    }
}

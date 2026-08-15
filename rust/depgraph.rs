//! Dependency graph data structures and operations.
//!
//! The dependency graph is the central abstraction in Forger.
//! It tracks all nodes (modules, resources, native extensions, etc.)
//! and edges (imports, resource dependencies, dynamic imports, etc.)
//! with full provenance tracking.

use std::collections::{HashMap, HashSet, VecDeque};
use std::fmt;
use std::fmt::Write;
use std::path::PathBuf;

use hashbrown::HashMap as FastHashMap;
use serde::{Serialize, Deserialize};

use crate::filesystem::FileType;
use crate::result::{ForgerError, ForgerResult};

/// Types of nodes in the dependency graph.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NodeType {
    PythonModule,
    PythonPackage,
    StdlibModule,
    StdlibPackage,
    NativeExtension,
    NativeLibrary,
    Resource,
    Configuration,
    EntryPoint,
    VfsPath,
    DynamicImport,
    ExternalPackage,
}

impl fmt::Display for NodeType {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            NodeType::PythonModule => write!(f, "python_module"),
            NodeType::PythonPackage => write!(f, "python_package"),
            NodeType::StdlibModule => write!(f, "stdlib_module"),
            NodeType::StdlibPackage => write!(f, "stdlib_package"),
            NodeType::NativeExtension => write!(f, "native_extension"),
            NodeType::NativeLibrary => write!(f, "native_library"),
            NodeType::Resource => write!(f, "resource"),
            NodeType::Configuration => write!(f, "configuration"),
            NodeType::EntryPoint => write!(f, "entry_point"),
            NodeType::VfsPath => write!(f, "vfs_path"),
            NodeType::DynamicImport => write!(f, "dynamic_import"),
            NodeType::ExternalPackage => write!(f, "external_package"),
        }
    }
}

/// Types of edges (dependency relationships) in the graph.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EdgeType {
    Import,
    FromImport,
    RelativeImport,
    DynamicImport,
    ResourceDependency,
    NativeDependency,
    ConfigDependency,
    PluginDependency,
    EntryPointDependency,
    StdlibDependency,
    Indirect,
    Custom(String),
}

impl fmt::Display for EdgeType {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            EdgeType::Import => write!(f, "import"),
            EdgeType::FromImport => write!(f, "from_import"),
            EdgeType::RelativeImport => write!(f, "relative_import"),
            EdgeType::DynamicImport => write!(f, "dynamic_import"),
            EdgeType::ResourceDependency => write!(f, "resource"),
            EdgeType::NativeDependency => write!(f, "native"),
            EdgeType::ConfigDependency => write!(f, "config"),
            EdgeType::PluginDependency => write!(f, "plugin"),
            EdgeType::EntryPointDependency => write!(f, "entry_point"),
            EdgeType::StdlibDependency => write!(f, "stdlib"),
            EdgeType::Indirect => write!(f, "indirect"),
            EdgeType::Custom(label) => write!(f, "custom:{label}"),
        }
    }
}

/// Provenance information for an edge — why this dependency exists.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EdgeProvenance {
    /// Source file and line number where the dependency was discovered.
    pub source: Option<(String, usize)>,
    /// Analyzer or optimizer that discovered this dependency.
    pub discovered_by: String,
    /// Additional context/description.
    pub description: Option<String>,
}

impl fmt::Display for EdgeProvenance {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let source_str = self
            .source
            .as_ref()
            .map(|(file, line)| format!("{file}:{line}"))
            .unwrap_or_else(|| "unknown".into());
        write!(f, "by {} at {}", self.discovered_by, source_str)
    }
}

/// A node in the dependency graph.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DependencyNode {
    /// Unique identifier (e.g., module name, file path, resource key).
    pub id: String,
    /// Type of this node.
    pub node_type: NodeType,
    /// File system path (if applicable).
    pub path: Option<PathBuf>,
    /// Content hash (for incremental builds).
    pub hash: Option<String>,
    /// Size in bytes (if applicable).
    pub size: Option<u64>,
    /// File type classification.
    pub file_type: Option<FileType>,
    /// Whether this node is required (proven reachable).
    pub required: bool,
    /// Whether this node is retained conservatively (unknown reachability).
    pub conservative: bool,
    /// Target platform specificity (e.g., "windows-x64", "linux-arm64", None = universal).
    pub target: Option<String>,
    /// Metadata key-value pairs for extended information.
    pub metadata: HashMap<String, String>,
    /// Optional content stored with this node.
    pub content: Option<String>,
}

impl DependencyNode {
    pub fn new(id: impl Into<String>, node_type: NodeType) -> Self {
        Self {
            id: id.into(),
            node_type,
            path: None,
            hash: None,
            size: None,
            file_type: None,
            required: false,
            conservative: false,
            target: None,
            metadata: HashMap::new(),
            content: None,
        }
    }

    /// Add metadata to this node.
    pub fn with_metadata<K: Into<String>, V: Into<String>>(
        mut self,
        key: K,
        value: V,
    ) -> Self {
        self.metadata.insert(key.into(), value.into());
        self
    }

    /// Set the content of this node.
    pub fn set_content(&mut self, content: String) {
        self.content = Some(content);
    }

    /// Get an immutable reference to the content of this node.
    pub fn get_content(&self) -> Option<&String> {
        self.content.as_ref()
    }

    /// Get a mutable reference to the content of this node.
    pub fn get_content_mut(&mut self) -> Option<&mut String> {
        self.content.as_mut()
    }
}

/// An edge representing a dependency relationship.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DependencyEdge {
    /// Source node ID.
    pub from: String,
    /// Target node ID.
    pub to: String,
    /// Type of dependency.
    pub edge_type: EdgeType,
    /// Provenance information.
    pub provenance: EdgeProvenance,
}

impl DependencyEdge {
    pub fn new(
        from: impl Into<String>,
        to: impl Into<String>,
        edge_type: EdgeType,
        provenance: EdgeProvenance,
    ) -> Self {
        Self {
            from: from.into(),
            to: to.into(),
            edge_type,
            provenance,
        }
    }
}

/// The main dependency graph structure.
///
/// Uses hashbrown HashMaps for performance-critical lookups.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(crate = "serde")]
pub struct DependencyGraph {
    /// Nodes indexed by ID.
    nodes: FastHashMap<String, DependencyNode>,
    /// Edges indexed by source node ID.
    edges_from: FastHashMap<String, Vec<DependencyEdge>>,
    /// Reverse edges indexed by target node ID.
    edges_to: FastHashMap<String, Vec<DependencyEdge>>,
    /// Entry points (roots of the dependency graph).
    entry_points: Vec<String>,
    /// Graph-level metadata.
    metadata: HashMap<String, String>,
}

impl DependencyGraph {
    /// Create a new empty dependency graph.
    pub fn new() -> Self {
        Self {
            nodes: FastHashMap::default(),
            edges_from: FastHashMap::default(),
            edges_to: FastHashMap::default(),
            entry_points: Vec::new(),
            metadata: HashMap::new(),
        }
    }

    /// Create a graph with estimated capacity.
    pub fn with_capacity(nodes: usize) -> Self {
        Self {
            nodes: FastHashMap::with_capacity(nodes),
            edges_from: FastHashMap::default(),
            edges_to: FastHashMap::default(),
            entry_points: Vec::new(),
            metadata: HashMap::new(),
        }
    }

    /// Add a node to the graph. Returns false if the node already exists.
    pub fn add_node(&mut self, node: DependencyNode) -> bool {
        self.nodes.insert(node.id.clone(), node).is_none()
    }

    /// Get a node by ID.
    pub fn get_node(&self, id: &str) -> Option<&DependencyNode> {
        self.nodes.get(id)
    }

    /// Get a mutable reference to a node by ID.
    pub fn get_node_mut(&mut self, id: &str) -> Option<&mut DependencyNode> {
        self.nodes.get_mut(id)
    }

    /// Add an edge to the graph.
    pub fn add_edge(&mut self, edge: DependencyEdge) {
        let from = edge.from.clone();
        let to = edge.to.clone();

        self.edges_from.entry(from).or_default().push(edge.clone());
        self.edges_to.entry(to).or_default().push(edge);
    }

    /// Get all outgoing edges from a node.
    pub fn outgoing_edges(&self, node_id: &str) -> &[DependencyEdge] {
        self.edges_from
            .get(node_id)
            .map(|v| v.as_ref())
            .unwrap_or(&[])
    }

    /// Get all incoming edges to a node.
    pub fn incoming_edges(&self, node_id: &str) -> &[DependencyEdge] {
        self.edges_to
            .get(node_id)
            .map(|v| v.as_ref())
            .unwrap_or(&[])
    }

    /// Register an entry point.
    pub fn add_entry_point(&mut self, id: impl Into<String>) {
        self.entry_points.push(id.into());
    }

    /// Get all entry points.
    pub fn entry_points(&self) -> &[String] {
        &self.entry_points
    }

    /// Get all nodes.
    pub fn all_nodes(&self) -> impl Iterator<Item = (&str, &DependencyNode)> {
        self.nodes.iter().map(|(id, node)| (id.as_str(), node))
    }

    /// Get all edges.
    pub fn all_edges(&self) -> impl Iterator<Item = &DependencyEdge> {
        self.edges_from.values().flatten()
    }

    /// Number of nodes.
    pub fn node_count(&self) -> usize {
        self.nodes.len()
    }

    /// Number of edges.
    pub fn edge_count(&self) -> usize {
        self.edges_from.values().map(|v| v.len()).sum()
    }

    /// Set graph-level metadata.
    pub fn set_metadata<K: Into<String>, V: Into<String>>(&mut self, key: K, value: V) {
        self.metadata.insert(key.into(), value.into());
    }

    /// Get graph-level metadata.
    pub fn get_metadata(&self, key: &str) -> Option<&String> {
        self.metadata.get(key)
    }

    /// Get an iterator over all metadata entries.
    pub fn get_metadata_entries(&self) -> impl Iterator<Item = (&String, &String)> {
        self.metadata.iter()
    }

    /// Find all reachable nodes from entry points using BFS.
    pub fn find_reachable(&self) -> HashSet<String> {
        let mut visited = HashSet::new();
        let mut queue = VecDeque::new();

        for entry in &self.entry_points {
            if visited.insert(entry.clone()) {
                queue.push_back(entry.clone());
            }
        }

        while let Some(node_id) = queue.pop_front() {
            for edge in self.outgoing_edges(&node_id) {
                if visited.insert(edge.to.clone()) {
                    queue.push_back(edge.to.clone());
                }
            }
        }

        visited
    }

    /// Mark all reachable nodes as required.
    pub fn mark_reachable_required(&mut self) {
        let reachable = self.find_reachable();
        for (id, node) in &mut self.nodes {
            node.required = reachable.contains(id);
        }
    }

    /// Get dependencies (direct children) of a node.
    pub fn dependencies_of(&self, node_id: &str) -> Vec<String> {
        self.outgoing_edges(node_id)
            .iter()
            .map(|e| e.to.clone())
            .collect()
    }

    /// Get dependents (reverse dependencies) of a node.
    pub fn dependents_of(&self, node_id: &str) -> Vec<String> {
        self.incoming_edges(node_id)
            .iter()
            .map(|e| e.from.clone())
            .collect()
    }

    /// Check if a node has any dependents (is a leaf).
    pub fn is_leaf(&self, node_id: &str) -> bool {
        self.outgoing_edges(node_id).is_empty()
    }

    /// Filter nodes by type.
    pub fn nodes_by_type(&self, node_type: NodeType) -> Vec<&DependencyNode> {
        self.nodes
            .values()
            .filter(|n| n.node_type == node_type)
            .collect()
    }

    /// Filter nodes by target platform.
    pub fn nodes_for_target(&self, target: &str) -> Vec<&DependencyNode> {
        self.nodes
            .values()
            .filter(|n| n.target.as_deref() == Some(target) || n.target.is_none())
            .collect()
    }

    /// Merge another graph into this one.
    pub fn merge(&mut self, other: DependencyGraph) {
        for (_, node) in other.nodes {
            self.add_node(node);
        }
        for edges in other.edges_from.into_values() {
            for edge in edges {
                self.add_edge(edge);
            }
        }
        for entry in other.entry_points {
            self.add_entry_point(entry);
        }
    }

    /// Remove unreachable nodes and their edges.
    pub fn prune_unreachable(&mut self) {
        let reachable = self.find_reachable();
        self.nodes.retain(|id, _| reachable.contains(id));
        self.edges_from
            .retain(|id, _| reachable.contains(id));
        self.edges_to.retain(|id, _| reachable.contains(id));
    }

    /// Generate diagnostic information for a node.
    pub fn node_diagnostic(&self, node_id: &str) -> Option<String> {
        let node = self.nodes.get(node_id)?;
        let mut diag = format!(
            "Node: {}\n  Type: {}\n  Required: {}\n  Conservative: {}",
            node.id, node.node_type, node.required, node.conservative
        );

        if let Some(ref path) = node.path {
            use std::fmt::Write;
            let _ = write!(diag, "\n  Path: {:?}", path);
        }

        let dependents = self.dependents_of(node_id);
        if !dependents.is_empty() {
            let _ = write!(diag, "\n  Dependents ({}):", dependents.len());
            for dep in &dependents[..dependents.len().min(5)] {
                let _ = write!(diag, "\n    - {}", dep);
            }
            if dependents.len() > 5 {
                let _ = write!(
                    diag,
                    "\n    ... and {} more",
                    dependents.len() - 5
                );
            }
        }

        Some(diag)
    }

    /// Validate graph integrity.
    pub fn validate(&self) -> ForgerResult<()> {
        // All edge endpoints must reference existing nodes
        for edge in self.all_edges() {
            if !self.nodes.contains_key(&edge.from) {
                return Err(ForgerError::DependencyGraph(format!(
                    "edge references non-existent source node: {}",
                    edge.from
                )));
            }
            if !self.nodes.contains_key(&edge.to) {
                return Err(ForgerError::DependencyGraph(format!(
                    "edge references non-existent target node: {}",
                    edge.to
                )));
            }
        }

        // All entry points must reference existing nodes
        for entry in &self.entry_points {
            if !self.nodes.contains_key(entry) {
                return Err(ForgerError::DependencyGraph(format!(
                    "entry point references non-existent node: {}",
                    entry
                )));
            }
        }

        Ok(())
    }
}

impl Default for DependencyGraph {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_provenance() -> EdgeProvenance {
        EdgeProvenance {
            source: Some(("main.py".into(), 10)),
            discovered_by: "static_analyzer".into(),
            description: Some("import statement".into()),
        }
    }

    #[test]
    fn test_add_node_and_retrieve() {
        let mut graph = DependencyGraph::new();
        let node = DependencyNode::new("app.main", NodeType::PythonModule);
        assert!(graph.add_node(node));

        assert!(graph.get_node("app.main").is_some());
        assert_eq!(graph.node_count(), 1);
    }

    #[test]
    fn test_add_edge_and_traverse() {
        let mut graph = DependencyGraph::new();
        graph.add_node(DependencyNode::new("app.main", NodeType::PythonModule));
        graph.add_node(DependencyNode::new("app.utils", NodeType::PythonModule));
        graph.add_edge(DependencyEdge::new(
            "app.main",
            "app.utils",
            EdgeType::Import,
            test_provenance(),
        ));

        assert_eq!(graph.edge_count(), 1);
        assert_eq!(graph.dependencies_of("app.main"), vec!["app.utils"]);
        assert_eq!(graph.dependents_of("app.utils"), vec!["app.main"]);
    }

    #[test]
    fn test_reachable_from_entry_points() {
        let mut graph = DependencyGraph::new();
        graph.add_node(DependencyNode::new("entry", NodeType::EntryPoint));
        graph.add_node(DependencyNode::new("mod_a", NodeType::PythonModule));
        graph.add_node(DependencyNode::new("mod_b", NodeType::PythonModule));
        graph.add_node(DependencyNode::new("orphan", NodeType::PythonModule));

        graph.add_edge(DependencyEdge::new(
            "entry",
            "mod_a",
            EdgeType::Import,
            test_provenance(),
        ));
        graph.add_edge(DependencyEdge::new(
            "mod_a",
            "mod_b",
            EdgeType::Import,
            test_provenance(),
        ));

        graph.add_entry_point("entry");

        let reachable = graph.find_reachable();
        assert!(reachable.contains("entry"));
        assert!(reachable.contains("mod_a"));
        assert!(reachable.contains("mod_b"));
        assert!(!reachable.contains("orphan"));
    }

    #[test]
    fn test_prune_unreachable() {
        let mut graph = DependencyGraph::new();
        graph.add_node(DependencyNode::new("entry", NodeType::EntryPoint));
        graph.add_node(DependencyNode::new("mod_a", NodeType::PythonModule));
        graph.add_node(DependencyNode::new("orphan", NodeType::PythonModule));

        graph.add_edge(DependencyEdge::new(
            "entry",
            "mod_a",
            EdgeType::Import,
            test_provenance(),
        ));

        graph.add_entry_point("entry");
        graph.prune_unreachable();

        assert!(graph.get_node("entry").is_some());
        assert!(graph.get_node("mod_a").is_some());
        assert!(graph.get_node("orphan").is_none());
    }

    #[test]
    fn test_merge_graphs() {
        let mut graph1 = DependencyGraph::new();
        graph1.add_node(DependencyNode::new("a", NodeType::PythonModule));

        let mut graph2 = DependencyGraph::new();
        graph2.add_node(DependencyNode::new("b", NodeType::PythonModule));

        graph1.merge(graph2);

        assert_eq!(graph1.node_count(), 2);
        assert!(graph1.get_node("a").is_some());
        assert!(graph1.get_node("b").is_some());
    }

    #[test]
    fn test_validate_catches_bad_edges() {
        let mut graph = DependencyGraph::new();
        graph.add_node(DependencyNode::new("a", NodeType::PythonModule));
        graph.add_edge(DependencyEdge::new(
            "a",
            "nonexistent",
            EdgeType::Import,
            test_provenance(),
        ));

        assert!(graph.validate().is_err());
    }

    #[test]
    fn test_node_diagnostic() {
        let mut graph = DependencyGraph::new();
        graph.add_node(DependencyNode::new("entry", NodeType::EntryPoint));
        graph.add_node(DependencyNode::new("mod", NodeType::PythonModule));
        graph.add_edge(DependencyEdge::new(
            "entry",
            "mod",
            EdgeType::Import,
            test_provenance(),
        ));

        let diag = graph.node_diagnostic("mod").unwrap();
        assert!(diag.contains("mod"));
        assert!(diag.contains("python_module"));
    }
}

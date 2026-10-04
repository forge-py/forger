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
use pyo3::prelude::*;
use serde::{Serialize, Deserialize};

use crate::filesystem::FileType;
use crate::result::{ForgerError, ForgerResult};

/// Types of nodes in the dependency graph.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[pyclass(eq, eq_int)]
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
#[pyclass(eq)]
pub enum EdgeType {
    Import(),
    FromImport(),
    RelativeImport(),
    DynamicImport(),
    ResourceDependency(),
    NativeDependency(),
    ConfigDependency(),
    PluginDependency(),
    EntryPointDependency(),
    StdlibDependency(),
    Indirect(),
    UnoptimizedDependency(),
    Custom(String),
}

impl fmt::Display for EdgeType {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            EdgeType::Import() => write!(f, "import"),
            EdgeType::FromImport() => write!(f, "from_import"),
            EdgeType::RelativeImport() => write!(f, "relative_import"),
            EdgeType::DynamicImport() => write!(f, "dynamic_import"),
            EdgeType::ResourceDependency() => write!(f, "resource"),
            EdgeType::NativeDependency() => write!(f, "native"),
            EdgeType::ConfigDependency() => write!(f, "config"),
            EdgeType::PluginDependency() => write!(f, "plugin"),
            EdgeType::EntryPointDependency() => write!(f, "entry_point"),
            EdgeType::StdlibDependency() => write!(f, "stdlib"),
            EdgeType::Indirect() => write!(f, "indirect"),
            EdgeType::UnoptimizedDependency() => write!(f, "unoptimized"),
            EdgeType::Custom(label) => write!(f, "custom:{label}"),
        }
    }
}

/// Provenance information for an edge — why this dependency exists.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[pyclass]
pub struct EdgeProvenance {
    /// Source file and line number where the dependency was discovered.
    #[pyo3(get, set)]
    pub source: Option<(String, usize)>,
    /// Analyzer or optimizer that discovered this dependency.
    #[pyo3(get, set)]
    pub discovered_by: String,
    /// Additional context/description.
    #[pyo3(get, set)]
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
#[pyclass]
pub struct DependencyNode {
    /// Unique identifier (e.g., module name, file path, resource key).
    #[pyo3(get, set)]
    pub id: String,
    /// Type of this node.
    #[pyo3(get, set)]
    pub node_type: NodeType,
    /// File system path (if applicable).
    #[pyo3(get, set)]
    pub path: Option<PathBuf>,
    /// Content hash (for incremental builds).
    #[pyo3(get, set)]
    pub hash: Option<String>,
    /// Size in bytes (if applicable).
    #[pyo3(get, set)]
    pub size: Option<u64>,
    /// File type classification (not exposed to Python).
    pub file_type: Option<FileType>,
    /// Whether this node is required (proven reachable).
    #[pyo3(get, set)]
    pub required: bool,
    /// Whether this node is retained conservatively (unknown reachability).
    #[pyo3(get, set)]
    pub conservative: bool,
    /// Target platform specificity (e.g., "windows-x64", "linux-arm64", None = universal).
    #[pyo3(get, set)]
    pub target: Option<String>,
    /// Whether this node is unoptimized (bypasses tree-shaking).
    #[pyo3(get, set)]
    pub unoptimized: bool,
    /// Metadata key-value pairs for extended information.
    #[pyo3(get, set)]
    pub metadata: HashMap<String, String>,
    /// Optional content stored with this node.
    #[pyo3(get, set)]
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
            unoptimized: false,
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
#[pyclass]
pub struct DependencyEdge {
    /// Source node ID.
    pub from: String,
    /// Target node ID.
    pub to: String,
    /// Type of dependency.
    #[pyo3(get, set)]
    pub edge_type: EdgeType,
    /// Provenance information.
    #[pyo3(get, set)]
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
/// Once frozen, no new nodes or edges can be added — only existing
/// nodes can be mutated (content, metadata, required flag, etc.).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(crate = "serde")]
#[pyclass]
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
    /// Whether the graph is frozen (no new nodes/edges allowed).
    #[serde(skip)]
    frozen: bool,
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
            frozen: false,
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
            frozen: false,
        }
    }

    /// Check if the graph is frozen.
    pub fn is_frozen(&self) -> bool {
        self.frozen
    }

    /// Freeze the graph. No new nodes or edges can be added after this call.
    /// Existing nodes remain mutable (content, metadata, required flag, etc.).
    pub fn freeze(&mut self) {
        self.frozen = true;
    }

    /// Add a node to the graph. Returns false if the node already exists
    /// or if the graph is frozen.
    pub fn add_node(&mut self, node: DependencyNode) -> bool {
        if self.frozen {
            return false;
        }
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

    /// Add an edge to the graph. No-op if the graph is frozen.
    pub fn add_edge(&mut self, edge: DependencyEdge) {
        if self.frozen {
            return;
        }
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

    /// Register an entry point. No-op if the graph is frozen.
    pub fn add_entry_point(&mut self, id: impl Into<String>) {
        if self.frozen {
            return;
        }
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
    /// Unoptimized nodes are always marked required (they bypass tree-shaking).
    pub fn mark_reachable_required(&mut self) {
        let mut reachable = self.find_reachable();
        for (id, node) in &mut self.nodes {
            if node.unoptimized {
                reachable.insert(id.clone());
            }
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

    /// Return every edge from ``src`` to ``dst`` (PHILOSOPHY.md §34).
    pub fn edges_between(&self, src: &str, dst: &str) -> Vec<&DependencyEdge> {
        self.outgoing_edges(src)
            .iter()
            .filter(|e| e.to == dst)
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

    /// Check if a node is dead (not reachable and not unoptimized).
    pub fn is_dead(&self, node_id: &str) -> bool {
        let node = self.nodes.get(node_id);
        if node.is_none() {
            return true;
        }
        if node.unwrap().unoptimized {
            return false;
        }
        !self.find_reachable().contains(node_id)
    }

    /// Return all dead (unreachable and not unoptimized) node IDs.
    pub fn find_dead_nodes(&self) -> Vec<String> {
        let reachable = self.find_reachable();
        let mut dead = Vec::new();
        for (id, node) in &self.nodes {
            if node.unoptimized {
                continue;
            }
            if !reachable.contains(id) {
                dead.push(id.clone());
            }
        }
        dead
    }

    /// Remove a node from the graph. Returns true if the node existed.
    pub fn delete_node(&mut self, node_id: &str) -> bool {
        if self.nodes.remove(node_id).is_none() {
            return false;
        }
        self.edges_from.remove(node_id);
        self.edges_to.remove(node_id);
        self.entry_points.retain(|ep| ep != node_id);
        true
    }

    /// Return all nodes marked as unoptimized.
    pub fn get_unoptimized_nodes(&self) -> Vec<&DependencyNode> {
        self.nodes.values().filter(|n| n.unoptimized).collect()
    }

    /// Check if any nodes are marked unoptimized.
    pub fn has_unoptimized_nodes(&self) -> bool {
        self.nodes.values().any(|n| n.unoptimized)
    }

    /// Return owned nodes filtered by type.
    pub fn nodes_by_type_owned(&self, node_type: NodeType) -> Vec<DependencyNode> {
        self.nodes
            .values()
            .filter(|n| n.node_type == node_type)
            .cloned()
            .collect()
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

// --- PyO3 bindings ---

#[pymethods]
impl NodeType {
    fn __str__(&self) -> String {
        self.to_string()
    }

    fn __repr__(&self) -> String {
        format!("NodeType::{}", self)
    }
}

#[pymethods]
impl EdgeType {
    fn __str__(&self) -> String {
        self.to_string()
    }

    fn __repr__(&self) -> String {
        format!("EdgeType::{}", self)
    }
}

#[pymethods]
impl EdgeProvenance {
    #[new]
    #[pyo3(signature = (discovered_by, source = None, description = None))]
    fn py_new(
        discovered_by: String,
        source: Option<(String, usize)>,
        description: Option<String>,
    ) -> Self {
        Self {
            source,
            discovered_by,
            description,
        }
    }

    fn __str__(&self) -> String {
        self.to_string()
    }
}

#[pymethods]
impl DependencyNode {
    #[new]
    fn py_new(id: String, node_type: NodeType) -> Self {
        Self::new(id, node_type)
    }

    #[getter]
    fn path(&self) -> Option<String> {
        self.path.as_ref().map(|p| p.to_string_lossy().into_owned())
    }

    #[setter]
    fn set_path(&mut self, path: Option<String>) {
        self.path = path.map(PathBuf::from);
    }

    /// Mark this node as unoptimized (bypasses tree-shaking).
    fn mark_unoptimized(&mut self) {
        self.unoptimized = true;
    }

    fn __repr__(&self) -> String {
        format!("DependencyNode(id={}, type={})", self.id, self.node_type)
    }
}

#[pymethods]
impl DependencyEdge {
    #[new]
    fn py_new(from_node: String, to_node: String, edge_type: EdgeType, provenance: EdgeProvenance) -> Self {
        Self::new(from_node, to_node, edge_type, provenance)
    }

    #[getter]
    fn from_node(&self) -> String {
        self.from.clone()
    }

    #[getter]
    fn to_node(&self) -> String {
        self.to.clone()
    }

    fn __repr__(&self) -> String {
        format!("DependencyEdge(from={}, to={}, type={})", self.from, self.to, self.edge_type)
    }
}

#[pymethods]
impl DependencyGraph {
    #[new]
    fn py_new() -> Self {
        Self::new()
    }

    /// Add a node to the graph. Returns false if the node already exists.
    #[pyo3(name = "add_node")]
    fn py_add_node(&mut self, node: DependencyNode) -> bool {
        DependencyGraph::add_node(self, node)
    }

    /// Get a node by ID.
    #[pyo3(name = "get_node")]
    fn py_get_node(&self, node_id: &str) -> Option<DependencyNode> {
        DependencyGraph::get_node(self, node_id).cloned()
    }

    /// Add an edge to the graph.
    #[pyo3(name = "add_edge")]
    fn py_add_edge(&mut self, edge: DependencyEdge) {
        DependencyGraph::add_edge(self, edge);
    }

    /// Set the content of a node in place, by ID.
    ///
    /// pyo3 getters return clones of nodes, so mutations on fetched node
    /// objects never persist. This writes through to the stored node.
    #[pyo3(name = "set_node_content")]
    fn py_set_node_content(&mut self, node_id: &str, content: String) -> bool {
        match DependencyGraph::get_node_mut(self, node_id) {
            Some(node) => {
                DependencyNode::set_content(node, content);
                true
            }
            None => false,
        }
    }

    /// Mark a node required/unrequired in place, by ID.
    #[pyo3(name = "set_node_required")]
    fn py_set_node_required(&mut self, node_id: &str, required: bool) -> bool {
        match DependencyGraph::get_node_mut(self, node_id) {
            Some(node) => {
                node.required = required;
                true
            }
            None => false,
        }
    }

    /// Register an entry point.
    #[pyo3(name = "add_entry_point")]
    fn py_add_entry_point(&mut self, node_id: String) {
        DependencyGraph::add_entry_point(self, node_id);
    }

    /// Get all entry points.
    #[pyo3(name = "entry_points")]
    fn py_entry_points(&self) -> Vec<String> {
        DependencyGraph::entry_points(self).to_vec()
    }

    /// Number of nodes.
    #[pyo3(name = "node_count")]
    fn py_node_count(&self) -> usize {
        DependencyGraph::node_count(self)
    }

    /// Number of edges.
    #[pyo3(name = "edge_count")]
    fn py_edge_count(&self) -> usize {
        DependencyGraph::edge_count(self)
    }

    /// Get dependencies (direct children) of a node.
    #[pyo3(name = "dependencies_of")]
    fn py_dependencies_of(&self, node_id: &str) -> Vec<String> {
        DependencyGraph::dependencies_of(self, node_id)
    }

    /// Get dependents (reverse dependencies) of a node.
    #[pyo3(name = "dependents_of")]
    fn py_dependents_of(&self, node_id: &str) -> Vec<String> {
        DependencyGraph::dependents_of(self, node_id)
    }

    /// Return every edge from ``src`` to ``dst`` (PHILOSOPHY.md §34).
    ///
    /// Used by the compiler's diagnostic to attribute "why was this
    /// module included?" — without this, the Python fallback's
    /// private ``_edges_from`` dict is the only way to get the same
    /// information.
    #[pyo3(name = "edges_between")]
    fn py_edges_between(&self, src: &str, dst: &str) -> Vec<DependencyEdge> {
        DependencyGraph::edges_between(self, src, dst)
            .into_iter().cloned().collect()
    }

    /// Filter nodes by type.
    #[pyo3(name = "nodes_by_type")]
    fn py_nodes_by_type(&self, node_type: NodeType) -> Vec<DependencyNode> {
        DependencyGraph::nodes_by_type(self, node_type)
            .into_iter().map(|n| n.clone()).collect()
    }

    /// BFS from entry points to find all reachable nodes.
    #[pyo3(name = "find_reachable")]
    fn py_find_reachable(&self) -> Vec<String> {
        DependencyGraph::find_reachable(self).into_iter().collect()
    }

    /// Mark all reachable nodes as required.
    #[pyo3(name = "mark_reachable_required")]
    fn py_mark_reachable_required(&mut self) {
        DependencyGraph::mark_reachable_required(self);
    }

    /// Remove unreachable nodes and their edges.
    #[pyo3(name = "prune_unreachable")]
    fn py_prune_unreachable(&mut self) {
        DependencyGraph::prune_unreachable(self);
    }

    /// Merge another graph into this one.
    #[pyo3(name = "merge")]
    fn py_merge(&mut self, other: DependencyGraph) {
        DependencyGraph::merge(self, other);
    }

    /// Validate graph integrity.
    #[pyo3(name = "validate")]
    fn py_validate(&self) -> PyResult<()> {
        DependencyGraph::validate(self)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
    }

    /// Check if a node is dead (not reachable and not unoptimized).
    #[pyo3(name = "is_dead")]
    fn py_is_dead(&self, node_id: &str) -> bool {
        DependencyGraph::is_dead(self, node_id)
    }

    /// Return all dead (unreachable) node IDs.
    #[pyo3(name = "find_dead_nodes")]
    fn py_find_dead_nodes(&self) -> Vec<String> {
        DependencyGraph::find_dead_nodes(self)
    }

    /// Remove a node from the graph. Returns true if the node existed.
    #[pyo3(name = "delete_node")]
    fn py_delete_node(&mut self, node_id: &str) -> bool {
        DependencyGraph::delete_node(self, node_id)
    }

    /// Return all nodes marked as unoptimized.
    #[pyo3(name = "get_unoptimized_nodes")]
    fn py_get_unoptimized_nodes(&self) -> Vec<DependencyNode> {
        DependencyGraph::get_unoptimized_nodes(self)
            .into_iter().map(|n| n.clone()).collect()
    }

    /// Check if any nodes are marked unoptimized.
    #[pyo3(name = "has_unoptimized_nodes")]
    fn py_has_unoptimized_nodes(&self) -> bool {
        DependencyGraph::has_unoptimized_nodes(self)
    }

    /// Set graph-level metadata.
    #[pyo3(name = "set_metadata")]
    fn py_set_metadata(&mut self, key: String, value: String) {
        DependencyGraph::set_metadata(self, key, value);
    }

    /// Get graph-level metadata.
    #[pyo3(name = "get_metadata")]
    fn py_get_metadata(&self, key: &str) -> Option<String> {
        DependencyGraph::get_metadata(self, key).cloned()
    }

    /// Freeze the graph. No new nodes or edges can be added.
    /// Existing nodes remain mutable and can still be deleted.
    #[pyo3(name = "freeze")]
    fn py_freeze(&mut self) {
        DependencyGraph::freeze(self);
    }

    /// Check if the graph is frozen.
    #[pyo3(name = "is_frozen")]
    fn py_is_frozen(&self) -> bool {
        DependencyGraph::is_frozen(self)
    }

    fn __repr__(&self) -> String {
        format!(
            "DependencyGraph(nodes={}, edges={}, entry_points={})",
            DependencyGraph::node_count(self),
            DependencyGraph::edge_count(self),
            DependencyGraph::entry_points(self).len()
        )
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
            EdgeType::Import(),
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
            EdgeType::Import(),
            test_provenance(),
        ));
        graph.add_edge(DependencyEdge::new(
            "mod_a",
            "mod_b",
            EdgeType::Import(),
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
            EdgeType::Import(),
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
            EdgeType::Import(),
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
            EdgeType::Import(),
            test_provenance(),
        ));

        let diag = graph.node_diagnostic("mod").unwrap();
        assert!(diag.contains("mod"));
        assert!(diag.contains("python_module"));
    }

    #[test]
    fn test_frozen_graph_rejects_new_nodes() {
        let mut graph = DependencyGraph::new();
        graph.add_node(DependencyNode::new("a", NodeType::PythonModule));
        graph.freeze();

        // Adding new nodes is rejected
        assert!(!graph.add_node(DependencyNode::new("b", NodeType::PythonModule)));
        assert_eq!(graph.node_count(), 1);

        // Adding new edges is a no-op
        graph.add_edge(DependencyEdge::new(
            "a",
            "b",
            EdgeType::Import(),
            test_provenance(),
        ));
        assert_eq!(graph.edge_count(), 0);

        // Adding entry points is a no-op
        graph.add_entry_point("a");
        assert_eq!(graph.entry_points().len(), 0);

        // Existing nodes can still be deleted
        assert!(graph.delete_node("a"));
        assert_eq!(graph.node_count(), 0);
    }

    #[test]
    fn test_frozen_graph_allows_mutation() {
        let mut graph = DependencyGraph::new();
        graph.add_node(DependencyNode::new("a", NodeType::PythonModule));
        graph.freeze();

        // Mutation of existing nodes still works
        let node = graph.get_node_mut("a").unwrap();
        node.required = true;
        node.content = Some("x".into());
        assert!(graph.get_node("a").unwrap().required);
    }
}

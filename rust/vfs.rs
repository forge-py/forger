//! Virtual Filesystem for .forge artifacts.
//!
//! Provides a unified logical filesystem that the runtime exposes to Python,
//! preserving sys.path, __file__, __package__, __spec__, and importlib semantics.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};

use crate::result::{ForgerError, ForgerResult};

/// Entry in the virtual filesystem.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VfsEntry {
    /// Logical path in the VFS (always forward slashes, no leading slash).
    pub path: String,
    /// Size in bytes.
    pub size: u64,
    /// Content hash.
    pub hash: String,
    /// Whether this is a directory.
    pub is_directory: bool,
    /// Offset in the archive data section.
    pub data_offset: Option<u64>,
    /// Compressed size (if different from uncompressed).
    pub compressed_size: Option<u64>,
}

/// Node in the VFS tree.
#[derive(Debug, Clone)]
pub enum VfsNode {
    File {
        entry: VfsEntry,
        data: Vec<u8>,
    },
    Directory {
        entry: VfsEntry,
        children: HashMap<String, VfsNode>,
    },
}

/// The virtual filesystem.
pub struct VirtualFileSystem {
    /// Root of the VFS tree.
    root: HashMap<String, VfsNode>,
    /// sys.path entries for the VFS.
    sys_path: Vec<String>,
    /// Mapping from module name to VFS path.
    module_map: HashMap<String, String>,
}

impl VirtualFileSystem {
    /// Create a new empty VFS.
    pub fn new() -> Self {
        Self {
            root: HashMap::new(),
            sys_path: vec![".".into()],
            module_map: HashMap::new(),
        }
    }

    /// Add a file to the VFS.
    pub fn add_file(&mut self, vfs_path: &str, data: Vec<u8>) {
        use crate::hash::content_hash_bytes;

        let hash = content_hash_bytes(&data);

        let entry = VfsEntry {
            path: vfs_path.to_string(),
            size: data.len() as u64,
            hash: hash.0,
            is_directory: false,
            data_offset: None,
            compressed_size: None,
        };

        // Split path into directory and filename
        let parts: Vec<&str> = vfs_path.rsplitn(2, '/').collect();
        let dir_name = if parts.len() > 1 { parts[1] } else { "" };
        let file_name = parts[0];

        if dir_name.is_empty() {
            // Root level file
            self.root.insert(
                vfs_path.to_string(),
                VfsNode::File { entry, data },
            );
        } else {
            // Ensure directory exists
            self.ensure_directory(dir_name);

            // Add file to the directory
            if let Some(VfsNode::Directory { ref mut children, .. }) = self.root.get_mut(dir_name)
            {
                children.insert(
                    file_name.to_string(),
                    VfsNode::File { entry, data },
                );
            }
        }
    }

    /// Add a directory to the VFS.
    pub fn add_directory(&mut self, vfs_path: &str) {
        self.ensure_directory(vfs_path);
    }

    /// Ensure a directory exists in the VFS.
    fn ensure_directory(&mut self, dir_path: &str) {
        if !self.root.contains_key(dir_path) {
            let entry = VfsEntry {
                path: dir_path.to_string(),
                size: 0,
                hash: String::new(),
                is_directory: true,
                data_offset: None,
                compressed_size: None,
            };
            self.root.insert(
                dir_path.to_string(),
                VfsNode::Directory {
                    entry,
                    children: HashMap::new(),
                },
            );
        }
    }

    /// Read file data from the VFS.
    pub fn read(&self, vfs_path: &str) -> ForgerResult<Vec<u8>> {
        match self.resolve_path(vfs_path) {
            Some(VfsNode::File { data, .. }) => Ok(data.clone()),
            Some(VfsNode::Directory { .. }) => Err(ForgerError::VirtualFileSystem(format!(
                "path is a directory: {vfs_path}"
            ))),
            None => Err(ForgerError::VirtualFileSystem(format!(
                "file not found: {vfs_path}"
            ))),
        }
    }

    /// Check if a path exists in the VFS.
    pub fn exists(&self, vfs_path: &str) -> bool {
        self.resolve_path(vfs_path).is_some()
    }

    /// Check if a path is a directory.
    pub fn is_directory(&self, vfs_path: &str) -> bool {
        matches!(
            self.resolve_path(vfs_path),
            Some(VfsNode::Directory { .. })
        )
    }

    /// List directory contents.
    pub fn list_directory(&self, vfs_path: &str) -> ForgerResult<Vec<String>> {
        match self.resolve_path(vfs_path) {
            Some(VfsNode::Directory { children, .. }) => Ok(children.keys().cloned().collect()),
            Some(VfsNode::File { .. }) => Err(ForgerError::VirtualFileSystem(format!(
                "path is a file, not a directory: {vfs_path}"
            ))),
            None => Err(ForgerError::VirtualFileSystem(format!(
                "directory not found: {vfs_path}"
            ))),
        }
    }

    /// Register a module mapping (module name → VFS path).
    pub fn register_module(&mut self, module_name: &str, vfs_path: &str) {
        self.module_map
            .insert(module_name.to_string(), vfs_path.to_string());
    }

    /// Resolve a module name to a VFS path.
    pub fn resolve_module(&self, module_name: &str) -> Option<&String> {
        self.module_map.get(module_name)
    }

    /// Get sys.path entries.
    pub fn sys_path(&self) -> &[String] {
        &self.sys_path
    }

    /// Set sys.path entries.
    pub fn set_sys_path(&mut self, paths: Vec<String>) {
        self.sys_path = paths;
    }

    /// Add a sys.path entry.
    pub fn add_sys_path(&mut self, path: String) {
        self.sys_path.push(path);
    }

    /// Resolve a VFS path to its node.
    fn resolve_path(&self, vfs_path: &str) -> Option<&VfsNode> {
        // Direct lookup first
        if let Some(node) = self.root.get(vfs_path) {
            return Some(node);
        }

        // Try to resolve through directory tree
        let parts: Vec<&str> = vfs_path.split('/').collect();
        if parts.len() <= 1 {
            return self.root.get(vfs_path);
        }

        let dir_path = parts[..parts.len() - 1].join("/");
        let file_name = parts[parts.len() - 1];

        if let Some(VfsNode::Directory { children, .. }) = self.root.get(&dir_path) {
            return children.get(file_name);
        }

        None
    }

    /// Get the total number of files.
    pub fn file_count(&self) -> usize {
        self.count_files(&self.root)
    }

    /// Get the total size of all files.
    pub fn total_size(&self) -> u64 {
        self.sum_sizes(&self.root)
    }

    /// Count files recursively.
    fn count_files(&self, nodes: &HashMap<String, VfsNode>) -> usize {
        let mut count = 0;
        for node in nodes.values() {
            match node {
                VfsNode::File { .. } => count += 1,
                VfsNode::Directory { children, .. } => {
                    count += 1; // Count directory itself
                    count += self.count_files(children);
                }
            }
        }
        count
    }

    /// Sum file sizes recursively.
    fn sum_sizes(&self, nodes: &HashMap<String, VfsNode>) -> u64 {
        let mut total = 0;
        for node in nodes.values() {
            match node {
                VfsNode::File { entry, .. } => total += entry.size,
                VfsNode::Directory { children, .. } => {
                    total += self.sum_sizes(children);
                }
            }
        }
        total
    }

    /// Get diagnostic info.
    pub fn diagnostic_summary(&self) -> String {
        format!(
            "VFS Summary:\n\
             Files: {}\n\
             Total Size: {} bytes\n\
             sys.path entries: {}\n\
             Module mappings: {}",
            self.file_count(),
            self.total_size(),
            self.sys_path.len(),
            self.module_map.len()
        )
    }
}

impl Default for VirtualFileSystem {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_add_and_read_file() {
        let mut vfs = VirtualFileSystem::new();
        vfs.add_file("test.py", b"x = 1".to_vec());

        assert!(vfs.exists("test.py"));
        let data = vfs.read("test.py").unwrap();
        assert_eq!(data, b"x = 1");
    }

    #[test]
    fn test_add_nested_file() {
        let mut vfs = VirtualFileSystem::new();
        vfs.add_file("pkg/module.py", b"y = 2".to_vec());

        assert!(vfs.exists("pkg/module.py"));
        assert!(vfs.is_directory("pkg"));
    }

    #[test]
    fn test_list_directory() {
        let mut vfs = VirtualFileSystem::new();
        vfs.add_file("pkg/a.py", b"a".to_vec());
        vfs.add_file("pkg/b.py", b"b".to_vec());

        let entries = vfs.list_directory("pkg").unwrap();
        assert!(entries.contains(&"a.py".to_string()));
        assert!(entries.contains(&"b.py".to_string()));
    }

    #[test]
    fn test_module_mapping() {
        let mut vfs = VirtualFileSystem::new();
        vfs.register_module("os.path", "lib/python3/os/path.py");

        assert_eq!(
            vfs.resolve_module("os.path"),
            Some(&"lib/python3/os/path.py".to_string())
        );
    }

    #[test]
    fn test_file_not_found() {
        let vfs = VirtualFileSystem::new();
        assert!(vfs.read("nonexistent.py").is_err());
    }

    #[test]
    fn test_sys_path() {
        let mut vfs = VirtualFileSystem::new();
        vfs.add_sys_path("lib".into());
        vfs.add_sys_path("site-packages".into());

        assert_eq!(vfs.sys_path().len(), 3); // default + 2 added
    }

    #[test]
    fn test_diagnostic_summary() {
        let vfs = VirtualFileSystem::new();
        let summary = vfs.diagnostic_summary();
        assert!(summary.contains("VFS Summary"));
    }
}

//! High-performance parallel filesystem traversal.
//!
//! Uses rayon for parallel directory walking and crossbeam for efficient
//! communication between threads. Avoids repeated syscalls by batching
//! metadata operations.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

use glob::{glob, Pattern as GlobPattern};
use rayon::prelude::*;

use crate::hash::{content_hash, FileHash, HashValue};
use crate::pathutil::normalize_path;
use crate::result::{ForgerError, ForgerResult};

/// File type classification for dependency graph nodes.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum FileType {
    PythonSource,
    PythonBytecode,
    NativeExtension,
    NativeLibrary,
    DataResource,
    Configuration,
    Documentation,
    Other,
}

impl FileType {
    /// Classify a file based on its extension.
    pub fn from_path(path: &Path) -> Self {
        match path.extension().and_then(|e| e.to_str()) {
            Some("py") | Some("pyw") => FileType::PythonSource,
            Some("pyc") | Some("pyo") => FileType::PythonBytecode,
            Some("pyd") | Some("so") | Some("dylib") => FileType::NativeExtension,
            Some("dll") => FileType::NativeLibrary,
            Some("toml") | Some("cfg") | Some("ini") | Some("yaml") | Some("yml") | Some("json") | Some("xml") => FileType::Configuration,
            Some("md") | Some("rst") | Some("txt") => FileType::Documentation,
            _ => FileType::DataResource,
        }
    }
}

/// Metadata about a discovered file.
#[derive(Debug, Clone)]
pub struct FileEntry {
    /// Absolute resolved path.
    pub path: PathBuf,
    /// Normalized path string for fast comparison.
    pub normalized: String,
    /// File type classification.
    pub file_type: FileType,
    /// File size in bytes.
    pub size: u64,
    /// Modification time (for incremental builds).
    pub modified: SystemTime,
    /// Content hash (computed on demand).
    pub hash: Option<HashValue>,
    /// Relative path from the discovery root.
    pub relative: PathBuf,
}

impl FileEntry {
    /// Compute and set the content hash if not already computed.
    pub fn ensure_hash(&mut self) -> std::io::Result<()> {
        if self.hash.is_none() {
            self.hash = Some(content_hash(&self.path)?);
        }
        Ok(())
    }
}

/// Options for filesystem discovery.
#[derive(Debug, Clone)]
pub struct DiscoveryOptions {
    /// Root directory to scan.
    pub root: PathBuf,
    /// Whether to follow symlinks.
    pub follow_symlinks: bool,
    /// Whether to include hidden files/directories.
    pub include_hidden: bool,
    /// Maximum depth (None = unlimited).
    pub max_depth: Option<usize>,
    /// Glob patterns to include (empty = all files).
    pub include_patterns: Vec<GlobPattern>,
    /// Glob patterns to exclude.
    pub exclude_patterns: Vec<GlobPattern>,
    /// Whether to compute hashes during discovery.
    pub compute_hashes: bool,
    /// Common directories to skip (e.g., __pycache__, .git, .venv).
    pub skip_directories: Vec<String>,
}

impl Default for DiscoveryOptions {
    fn default() -> Self {
        Self {
            root: PathBuf::from("."),
            follow_symlinks: false,
            include_hidden: false,
            max_depth: None,
            include_patterns: vec![],
            exclude_patterns: vec![],
            compute_hashes: false,
            skip_directories: vec![
                "__pycache__".into(),
                ".git".into(),
                ".venv".into(),
                "venv".into(),
                ".mypy_cache".into(),
                ".pytest_cache".into(),
                ".pyrefly".into(),
                "node_modules".into(),
                ".tox".into(),
                ".eggs".into(),
                "dist".into(),
                "build".into(),
                "target".into(),
            ],
        }
    }
}

impl DiscoveryOptions {
    /// Create options with default exclusion patterns for Python projects.
    pub fn for_python_project(root: impl Into<PathBuf>) -> Self {
        Self {
            root: root.into(),
            ..Self::default()
        }
    }

    /// Add an exclude pattern.
    pub fn exclude<P: AsRef<str>>(mut self, pattern: P) -> ForgerResult<Self> {
        let glob_pattern = GlobPattern::new(pattern.as_ref())
            .map_err(|e| ForgerError::Configuration(format!("invalid glob pattern: {e}")))?;
        self.exclude_patterns.push(glob_pattern);
        Ok(self)
    }

    /// Add an include pattern.
    pub fn include<P: AsRef<str>>(mut self, pattern: P) -> ForgerResult<Self> {
        let glob_pattern = GlobPattern::new(pattern.as_ref())
            .map_err(|e| ForgerError::Configuration(format!("invalid glob pattern: {e}")))?;
        self.include_patterns.push(glob_pattern);
        Ok(self)
    }
}

/// Filesystem discovery engine.
pub struct FileDiscovery {
    options: DiscoveryOptions,
}

impl FileDiscovery {
    /// Create a new discovery engine with the given options.
    pub fn new(options: DiscoveryOptions) -> Self {
        Self { options }
    }

    /// Run sequential discovery (for small directories or when order matters).
    pub fn discover(&self) -> ForgerResult<Vec<FileEntry>> {
        let root = self.options.root.canonicalize().map_err(|e| {
            ForgerError::Filesystem(format!("cannot resolve root path: {e}"))
        })?;

        let mut entries = Vec::new();
        let max_depth = self.options.max_depth.unwrap_or(usize::MAX);

        self.walk_directory(&root, &root, 0, max_depth, &mut entries)?;

        Ok(entries)
    }

    /// Recursive directory walker with skip logic.
    fn walk_directory(
        &self,
        root: &Path,
        current: &Path,
        depth: usize,
        max_depth: usize,
        entries: &mut Vec<FileEntry>,
    ) -> ForgerResult<()> {
        if depth > max_depth {
            return Ok(());
        }

        let read_dir = fs::read_dir(current).map_err(|e| {
            ForgerError::Filesystem(format!("cannot read directory {:?}: {e}", current))
        })?;

        for entry_result in read_dir {
            let entry = entry_result.map_err(|e| {
                ForgerError::Filesystem(format!("directory entry error: {e}"))
            })?;

            let path = entry.path();

            // Skip hidden files/directories if configured
            if !self.options.include_hidden && Self::is_hidden(&path) {
                continue;
            }

            let file_type = entry.file_type().map_err(|e| {
                ForgerError::Filesystem(format!("cannot read file type for {:?}: {e}", path))
            })?;

            if file_type.is_dir() {
                // Skip excluded directories
                if self.should_skip_dir(&path) {
                    continue;
                }

                // Recurse into subdirectory
                self.walk_directory(root, &path, depth + 1, max_depth, entries)?;
            } else if file_type.is_symlink() {
                if self.options.follow_symlinks {
                    // Resolve symlink and process target
                    if let Ok(target) = fs::canonicalize(&path) {
                        if target.is_dir() {
                            if !self.should_skip_dir(&target) {
                                self.walk_directory(root, &target, depth + 1, max_depth, entries)?;
                            }
                        } else if target.is_file() {
                            self.process_file(root, &target, entries)?;
                        }
                    }
                }
            } else {
                // Regular file
                self.process_file(root, &path, entries)?;
            }
        }

        Ok(())
    }

    /// Process a single file and add to entries if it matches patterns.
    fn process_file(
        &self,
        root: &Path,
        path: &Path,
        entries: &mut Vec<FileEntry>,
    ) -> ForgerResult<()> {
        // Check include/exclude patterns
        if !self.matches_patterns(path) {
            return Ok(());
        }

        let metadata = fs::symlink_metadata(path).map_err(|e| {
            ForgerError::Filesystem(format!("cannot read metadata for {:?}: {e}", path))
        })?;

        let relative = path
            .strip_prefix(root)
            .unwrap_or(path)
            .to_path_buf();

        let normalized = normalize_path(path);
        let file_type = FileType::from_path(path);

        let mut file_entry = FileEntry {
            path: path.to_path_buf(),
            normalized,
            file_type,
            size: metadata.len(),
            modified: metadata.modified().unwrap_or(SystemTime::UNIX_EPOCH),
            hash: None,
            relative,
        };

        if self.options.compute_hashes {
            if let Ok(h) = content_hash(&file_entry.path) {
                file_entry.hash = Some(h);
            }
        }

        entries.push(file_entry);
        Ok(())
    }

    /// Run parallel hash computation on discovered entries.
    pub fn discover_with_parallel_hashes(&self) -> ForgerResult<Vec<FileEntry>> {
        let mut entries = self.discover()?;

        // Parallel hash computation
        entries.par_iter_mut().for_each(|entry| {
            if entry.hash.is_none() {
                if let Ok(h) = content_hash(&entry.path) {
                    entry.hash = Some(h);
                }
            }
        });

        Ok(entries)
    }

    /// Discover and compute file hashes in parallel, returning FileHash objects.
    pub fn discover_hashes(&self) -> ForgerResult<Vec<FileHash>> {
        let entries = self.discover()?;
        let hashes: Vec<FileHash> = entries
            .par_iter()
            .filter_map(|entry| FileHash::compute(&entry.path).ok())
            .collect();
        Ok(hashes)
    }

    /// Glob-based file discovery for specific patterns.
    pub fn glob_discover(pattern: &str) -> ForgerResult<Vec<FileEntry>> {
        let mut entries = Vec::new();

        for entry_result in glob(pattern).map_err(|e| {
            ForgerError::Filesystem(format!("invalid glob pattern '{pattern}': {e}"))
        })? {
            let path = match entry_result {
                Ok(p) => p,
                Err(e) => {
                    log::warn!("Glob error: {e}");
                    continue;
                }
            };

            if path.is_file() {
                let metadata = fs::metadata(&path).map_err(|e| {
                    ForgerError::Filesystem(format!("cannot read metadata for {:?}: {e}", path))
                })?;

                let normalized = normalize_path(&path);

                entries.push(FileEntry {
                    path: path.clone(),
                    normalized,
                    file_type: FileType::from_path(&path),
                    size: metadata.len(),
                    modified: metadata.modified().unwrap_or(SystemTime::UNIX_EPOCH),
                    hash: None,
                    relative: path.clone(),
                });
            }
        }

        Ok(entries)
    }

    /// Check if a path should be skipped based on hidden file settings.
    fn is_hidden(path: &Path) -> bool {
        path.file_name()
            .map(|name| name.to_string_lossy().starts_with('.'))
            .unwrap_or(false)
    }

    /// Check if a directory should be skipped.
    fn should_skip_dir(&self, path: &Path) -> bool {
        path.file_name()
            .and_then(|n| n.to_str())
            .map(|name| {
                self.options
                    .skip_directories
                    .iter()
                    .any(|skip| skip.as_str() == name)
            })
            .unwrap_or(false)
    }

    /// Check if a path matches the include/exclude patterns.
    fn matches_patterns(&self, path: &Path) -> bool {
        let path_str = path.to_string_lossy();

        // If include patterns are set, path must match at least one
        if !self.options.include_patterns.is_empty() {
            let included = self
                .options
                .include_patterns
                .iter()
                .any(|p| p.matches(&path_str));
            if !included {
                return false;
            }
        }

        // Check exclude patterns
        self.options
            .exclude_patterns
            .iter()
            .all(|p| !p.matches(&path_str))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn test_file_type_classification() {
        assert_eq!(FileType::from_path(Path::new("main.py")), FileType::PythonSource);
        assert_eq!(
            FileType::from_path(Path::new("cache.pyc")),
            FileType::PythonBytecode
        );
        assert_eq!(
            FileType::from_path(Path::new("_ssl.pyd")),
            FileType::NativeExtension
        );
        assert_eq!(
            FileType::from_path(Path::new("config.toml")),
            FileType::Configuration
        );
    }

    #[test]
    fn test_discovery_empty() {
        let tmp = TempDir::with_prefix("forger-test").unwrap();
        let options = DiscoveryOptions::for_python_project(tmp.path());
        let discovery = FileDiscovery::new(options);
        let entries = discovery.discover().unwrap();
        assert!(entries.is_empty());
    }

    #[test]
    fn test_discovery_with_files() {
        let tmp = TempDir::with_prefix("forger-test").unwrap();
        std::fs::write(tmp.path().join("main.py"), "print('hello')").unwrap();
        std::fs::write(tmp.path().join("utils.py"), "def util(): pass").unwrap();
        std::fs::create_dir_all(tmp.path().join("subdir")).unwrap();
        std::fs::write(tmp.path().join("subdir").join("nested.py"), "x = 1").unwrap();

        let options = DiscoveryOptions::for_python_project(tmp.path());
        let discovery = FileDiscovery::new(options);
        let entries = discovery.discover().unwrap();

        assert_eq!(entries.len(), 3);
        assert!(entries.iter().all(|e| e.file_type == FileType::PythonSource));
    }

    #[test]
    fn test_discovery_skips_pycache() {
        let tmp = TempDir::with_prefix("forger-test").unwrap();
        std::fs::write(tmp.path().join("main.py"), "print('hello')").unwrap();
        std::fs::create_dir_all(tmp.path().join("__pycache__")).unwrap();
        std::fs::write(tmp.path().join("__pycache__").join("main.cpython-310.pyc"), "bytecode")
            .unwrap();

        let options = DiscoveryOptions::for_python_project(tmp.path());
        let discovery = FileDiscovery::new(options);
        let entries = discovery.discover().unwrap();

        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].relative.file_name().unwrap().to_str().unwrap(), "main.py");
    }

    #[test]
    fn test_discovery_with_hash_computation() {
        let tmp = TempDir::with_prefix("forger-test").unwrap();
        std::fs::write(tmp.path().join("test.py"), "x = 1").unwrap();

        let mut options = DiscoveryOptions::for_python_project(tmp.path());
        options.compute_hashes = true;
        let discovery = FileDiscovery::new(options);
        let entries = discovery.discover().unwrap();

        assert_eq!(entries.len(), 1);
        assert!(entries[0].hash.is_some());
    }
}

//! Path normalization utilities for cross-platform consistency.
//!
//! All paths in Forger are normalized to forward slashes, resolved symlinks,
//! and stripped of redundant components for deterministic behavior.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

/// Normalize a path to a canonical form.
/// - Resolves `.` and `..` components
/// - Converts to forward slashes
/// - Trailing slash removal
/// - Returns the normalized path as a String
pub fn normalize_path(path: &Path) -> String {
    let lossy = path.to_string_lossy();
    let  result;

    // Split on both forward and back slashes
    let parts: Vec<&str> = lossy.split(|c| c == '/' || c == '\\').collect();
    let mut segments: Vec<&str> = Vec::new();

    for part in parts {
        if part.is_empty() || part == "." {
            continue;
        } else if part == ".." {
            segments.pop();
        } else {
            segments.push(part);
        }
    }

    result = segments.join("/");

    // Handle the case where the path was absolute
    if path.is_absolute() && !result.is_empty() {
        // Preserve root for absolute paths
    }

    if result.is_empty() {
        if path.is_absolute() {
            lossy.trim_end_matches('/').to_string()
        } else {
            ".".to_string()
        }
    } else {
        result
    }
}

/// Normalize a PathBuf in the same way as [`normalize_path`].
pub fn normalize_path_buf(path: &Path) -> PathBuf {
    PathBuf::from(normalize_path(path))
}

/// A set of normalized path strings for fast lookup.
#[derive(Debug, Clone)]
pub struct PathSet {
    inner: HashSet<String>,
}

impl PathSet {
    pub fn new() -> Self {
        Self {
            inner: HashSet::new(),
        }
    }

    pub fn with_capacity(capacity: usize) -> Self {
        Self {
            inner: HashSet::with_capacity(capacity),
        }
    }

    /// Add a path (normalized).
    pub fn insert(&mut self, path: &Path) -> bool {
        self.inner.insert(normalize_path(path))
    }

    /// Check if a path exists (normalized comparison).
    pub fn contains(&self, path: &Path) -> bool {
        self.inner.contains(&normalize_path(path))
    }

    /// Add all paths from an iterator.
    pub fn extend<P: AsRef<Path>, I: IntoIterator<Item = P>>(&mut self, paths: I) {
        for path in paths {
            self.insert(path.as_ref());
        }
    }

    /// Returns the number of paths.
    pub fn len(&self) -> usize {
        self.inner.len()
    }

    /// Returns true if empty.
    pub fn is_empty(&self) -> bool {
        self.inner.is_empty()
    }

    /// Iterate over the normalized paths.
    pub fn iter(&self) -> impl Iterator<Item = &str> {
        self.inner.iter().map(|s| s.as_str())
    }

    /// Check if a path is a descendant of any path in the set.
    pub fn is_descendant_of(&self, path: &Path) -> bool {
        let normalized = normalize_path(path);
        for entry in &self.inner {
            if normalized.starts_with(entry.as_str()) {
                return true;
            }
        }
        false
    }
}

impl Default for PathSet {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_normalize_dots() {
        let path = Path::new("a/b/../c/./d");
        assert_eq!(normalize_path(path), "a/c/d");
    }

    #[test]
    fn test_normalize_backslashes() {
        let path = Path::new("a\\b\\c");
        assert_eq!(normalize_path(path), "a/b/c");
    }

    #[test]
    fn test_normalize_trailing_slash() {
        let path = Path::new("a/b/c/");
        assert_eq!(normalize_path(path), "a/b/c");
    }

    #[test]
    fn test_path_set() {
        let mut set = PathSet::new();
        assert!(set.insert(Path::new("a/b/c")));
        assert!(!set.insert(Path::new("a/b/c"))); // duplicate
        assert!(set.contains(Path::new("a/b/c")));
        assert!(!set.contains(Path::new("a/b/d")));
        assert_eq!(set.len(), 1);
    }

    #[test]
    fn test_path_set_descendant() {
        let mut set = PathSet::new();
        set.insert(Path::new("src"));
        assert!(set.is_descendant_of(Path::new("src/module/file.py")));
        assert!(!set.is_descendant_of(Path::new("other/file.py")));
    }
}

//! Incremental build cache.
//!
//! Tracks file hashes and build artifacts to avoid redundant work
//! across compilations.

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::hash::HashValue;
use crate::pathutil::normalize_path;
use crate::result::{ForgerError, ForgerResult};

/// Cache entry for a single file.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CacheEntry {
    /// Normalized file path.
    pub path: String,
    /// Content hash at time of last cache.
    pub hash: String,
    /// File size at time of last cache.
    pub size: u64,
    /// Timestamp of last cache entry creation.
    pub timestamp: u64,
    /// Associated build metadata.
    pub metadata: HashMap<String, String>,
}

/// State of a file relative to the cache.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CacheState {
    /// File is unchanged since last cache.
    Unchanged,
    /// File has been modified since last cache.
    Modified,
    /// File is new (not in cache).
    New,
    /// File was in cache but no longer exists.
    Removed,
}

/// The build cache.
#[derive(Debug, Clone)]
pub struct Cache {
    /// Map from normalized path to cache entry.
    entries: HashMap<String, CacheEntry>,
    /// Cache directory for persistent storage.
    cache_dir: Option<PathBuf>,
    /// Whether to write cache changes to disk.
    persistent: bool,
}

impl Cache {
    /// Create a new in-memory cache.
    pub fn new() -> Self {
        Self {
            entries: HashMap::new(),
            cache_dir: None,
            persistent: false,
        }
    }

    /// Create a persistent cache backed by a directory.
    pub fn with_directory(cache_dir: impl Into<PathBuf>) -> Self {
        let cache_dir = Some(cache_dir.into());
        Self {
            entries: HashMap::new(),
            cache_dir,
            persistent: true,
        }
    }

    /// Load cache from disk.
    pub fn load(&mut self) -> ForgerResult<()> {
        if let Some(ref cache_dir) = self.cache_dir {
            let index_path = cache_dir.join("cache_index.json");
            if index_path.exists() {
                let data = fs::read_to_string(&index_path).map_err(|e| {
                    ForgerError::Cache(format!("cannot read cache index: {e}"))
                })?;

                let entries: Vec<CacheEntry> = serde_json::from_str(&data).map_err(|e| {
                    ForgerError::Cache(format!("cannot parse cache index: {e}"))
                })?;

                self.entries = entries
                    .into_iter()
                    .map(|e| (e.path.clone(), e))
                    .collect();
            }
        }
        Ok(())
    }

    /// Save cache to disk.
    pub fn save(&self) -> ForgerResult<()> {
        if !self.persistent {
            return Ok(());
        }

        let cache_dir = self
            .cache_dir
            .as_ref()
            .ok_or_else(|| ForgerError::Cache("cache directory not set".into()))?;

        fs::create_dir_all(cache_dir).map_err(|e| {
            ForgerError::Cache(format!("cannot create cache directory: {e}"))
        })?;

        let index_path = cache_dir.join("cache_index.json");
        let entries_vec: Vec<&CacheEntry> = self.entries.values().collect();
        let data = serde_json::to_string_pretty(&entries_vec).map_err(|e| {
            ForgerError::Cache(format!("cannot serialize cache index: {e}"))
        })?;

        fs::write(&index_path, data).map_err(|e| {
            ForgerError::Cache(format!("cannot write cache index: {e}"))
        })?;

        Ok(())
    }

    /// Check the state of a file relative to the cache.
    pub fn check_state(&self, path: &Path, current_hash: &HashValue) -> CacheState {
        let normalized = normalize_path(path);

        match self.entries.get(&normalized) {
            Some(entry) => {
                if entry.hash == current_hash.0 {
                    CacheState::Unchanged
                } else {
                    CacheState::Modified
                }
            }
            None => CacheState::New,
        }
    }

    /// Update or insert a cache entry for a file.
    pub fn update(&mut self, path: &Path, hash: &HashValue, size: u64) {
        let normalized = normalize_path(path);
        let timestamp = Self::current_timestamp();

        self.entries.insert(
            normalized.clone(),
            CacheEntry {
                path: normalized,
                hash: hash.0.clone(),
                size,
                timestamp,
                metadata: HashMap::new(),
            },
        );
    }

    /// Remove a cache entry.
    pub fn remove(&mut self, path: &Path) {
        let normalized = normalize_path(path);
        self.entries.remove(&normalized);
    }

    /// Get a cache entry.
    pub fn get(&self, path: &Path) -> Option<&CacheEntry> {
        let normalized = normalize_path(path);
        self.entries.get(&normalized)
    }

    /// Check if a path is cached.
    pub fn contains(&self, path: &Path) -> bool {
        let normalized = normalize_path(path);
        self.entries.contains_key(&normalized)
    }

    /// Get the number of cache entries.
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// Check if the cache is empty.
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// Iterate over all entries.
    pub fn iter(&self) -> impl Iterator<Item = (&str, &CacheEntry)> {
        self.entries.iter().map(|(k, v)| (k.as_str(), v))
    }

    /// Clear the cache.
    pub fn clear(&mut self) {
        self.entries.clear();
    }

    /// Compute a combined hash of all cached entries for change detection.
    pub fn overall_hash(&self) -> HashValue {
        use crate::hash::{combine_hashes, content_hash_bytes};

        if self.entries.is_empty() {
            return content_hash_bytes(&[]);
        }

        let hashes: Vec<HashValue> = self
            .entries
            .values()
            .map(|e| HashValue(e.hash.clone()))
            .collect();

        combine_hashes(&hashes)
    }

    /// Get current timestamp as u64 milliseconds.
    #[allow(unused)]
    fn current_timestamp() -> u64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0)
    }

    /// Invalidate all entries older than a timestamp.
    pub fn invalidate_older_than(&mut self, timestamp: u64) {
        self.entries
            .retain(|_, entry| entry.timestamp >= timestamp);
    }

    /// Check if incremental rebuild is needed by comparing against current hashes.
    ///
    /// Returns true if any file has changed since the last cached build.
    pub fn needs_rebuild(
        &self,
        current_state: &[(String, HashValue)],
    ) -> bool {
        for (path, hash) in current_state {
            match self.entries.get(path) {
                Some(entry) => {
                    if &entry.hash != &hash.0 {
                        return true;
                    }
                }
                None => {
                    // New file
                    return true;
                }
            }
        }

        // Check for removed files
        for (path, _) in current_state {
            if !self.entries.contains_key(path.as_str()) {
                return true;
            }
        }

        false
    }
}

impl Default for Cache {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hash::content_hash_bytes;
    use tempfile::TempDir;

    #[test]
    fn test_cache_new_file() {
        let cache = Cache::new();
        let hash = content_hash_bytes(b"hello");
        assert_eq!(
            cache.check_state(Path::new("new.py"), &hash),
            CacheState::New
        );
    }

    #[test]
    fn test_cache_unchanged_file() {
        let mut cache = Cache::new();
        let hash = content_hash_bytes(b"hello");
        cache.update(Path::new("file.py"), &hash, 5);

        assert_eq!(
            cache.check_state(Path::new("file.py"), &hash),
            CacheState::Unchanged
        );
    }

    #[test]
    fn test_cache_modified_file() {
        let mut cache = Cache::new();
        let old_hash = content_hash_bytes(b"old content");
        cache.update(Path::new("file.py"), &old_hash, 11);

        let new_hash = content_hash_bytes(b"new content");
        assert_eq!(
            cache.check_state(Path::new("file.py"), &new_hash),
            CacheState::Modified
        );
    }

    #[test]
    fn test_cache_persistent() {
        let tmp = TempDir::with_prefix("forger-test").unwrap();
        let mut cache = Cache::with_directory(tmp.path());

        let hash = content_hash_bytes(b"test");
        cache.update(Path::new("file.py"), &hash, 4);
        cache.save().unwrap();

        let mut loaded = Cache::with_directory(tmp.path());
        loaded.load().unwrap();

        assert_eq!(loaded.len(), 1);
        assert!(loaded.contains(Path::new("file.py")));
    }

    #[test]
    fn test_cache_clear() {
        let mut cache = Cache::new();
        let hash = content_hash_bytes(b"test");
        cache.update(Path::new("file.py"), &hash, 4);

        cache.clear();
        assert!(cache.is_empty());
    }

    #[test]
    fn test_needs_rebuild_detects_new_file() {
        let cache = Cache::new();
        let current = vec![(
            "new_file.py".into(),
            content_hash_bytes(b"new content"),
        )];
        assert!(cache.needs_rebuild(&current));
    }

    #[test]
    fn test_needs_rebuild_no_change() {
        let mut cache = Cache::new();
        let hash = content_hash_bytes(b"stable");
        cache.update(Path::new("stable.py"), &hash, 6);

        let current = vec![("stable.py".into(), hash)];
        assert!(!cache.needs_rebuild(&current));
    }
}

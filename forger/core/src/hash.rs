//! Content hashing using BLAKE3 for fast, deterministic file hashing.
//!
//! BLAKE3 is chosen for its speed, parallelism, and cryptographic properties.
//! Hashes are used for incremental build detection and cache invalidation.

use std::fs::File;
use std::io::{self, Read};
use std::path::{Path, PathBuf};


/// A content hash value, represented as a hex string.
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct HashValue(pub String);

impl std::fmt::Display for HashValue {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl HashValue {
    /// Returns the first 8 characters as a short identifier.
    pub fn short(&self) -> &str {
        &self.0[..8.min(self.0.len())]
    }
}

/// Compute the BLAKE3 content hash of a file.
pub fn content_hash(path: &Path) -> io::Result<HashValue> {
    let mut hasher = blake3::Hasher::new();
    let mut file = File::open(path)?;
    let mut buffer = [0u8; 65536];

    loop {
        let bytes_read = file.read(&mut buffer)?;
        if bytes_read == 0 {
            break;
        }
        hasher.update(&buffer[..bytes_read]);
    }

    Ok(HashValue(
        hasher.finalize().to_hex().to_string(),
    ))
}

/// Compute the BLAKE3 hash from any reader.
pub fn content_hash_reader<R: Read>(reader: &mut R) -> io::Result<HashValue> {
    let mut hasher = blake3::Hasher::new();
    let mut buffer = [0u8; 65536];

    loop {
        let bytes_read = reader.read(&mut buffer)?;
        if bytes_read == 0 {
            break;
        }
        hasher.update(&buffer[..bytes_read]);
    }

    Ok(HashValue(
        hasher.finalize().to_hex().to_string(),
    ))
}

/// Compute the BLAKE3 hash of in-memory data.
pub fn content_hash_bytes(data: &[u8]) -> HashValue {
    let hash = blake3::hash(data);
    HashValue(hash.to_hex().to_string())
}

/// Combined hash for multiple hash values (e.g., directory tree hash).
pub fn combine_hashes(hashes: &[HashValue]) -> HashValue {
    if hashes.is_empty() {
        return content_hash_bytes(&[]);
    }

    let mut hasher = blake3::Hasher::new();
    for hash in hashes {
        hasher.update(hash.0.as_bytes());
    }
    HashValue(hasher.finalize().to_hex().to_string())
}

/// File metadata with hash for cache tracking.
#[derive(Debug, Clone)]
pub struct FileHash {
    pub path: PathBuf,
    pub hash: HashValue,
    pub size: u64,
}

impl FileHash {
    /// Compute hash and size for a file.
    pub fn compute(path: &Path) -> io::Result<Self> {
        let metadata = std::fs::metadata(path)?;
        let hash = content_hash(path)?;
        Ok(Self {
            path: path.to_path_buf(),
            hash,
            size: metadata.len(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;
    use tempfile::NamedTempFile;

    #[test]
    fn test_content_hash_bytes() {
        let hash1 = content_hash_bytes(b"hello world");
        let hash2 = content_hash_bytes(b"hello world");
        let hash3 = content_hash_bytes(b"different content");

        assert_eq!(hash1, hash2);
        assert_ne!(hash1, hash3);
        assert_eq!(hash1.0.len(), 64); // BLAKE3 hex is 64 chars
    }

    #[test]
    fn test_content_hash_file() {
        let mut file = NamedTempFile::new().unwrap();
        use std::io::Write;
        writeln!(file, "test content").unwrap();
        let hash = content_hash(file.path()).unwrap();
        assert_eq!(hash.0.len(), 64);
    }

    #[test]
    fn test_content_hash_reader() {
        let data = b"test data for reader";
        let mut cursor = Cursor::new(data);
        let hash = content_hash_reader(&mut cursor).unwrap();
        assert_eq!(hash, content_hash_bytes(data));
    }

    #[test]
    fn test_combine_hashes() {
        let hashes = vec![
            content_hash_bytes(b"a"),
            content_hash_bytes(b"b"),
            content_hash_bytes(b"c"),
        ];
        let combined = combine_hashes(&hashes);
        assert_eq!(combined.0.len(), 64);

        // Order matters
        let reversed = vec![
            content_hash_bytes(b"c"),
            content_hash_bytes(b"b"),
            content_hash_bytes(b"a"),
        ];
        let combined_rev = combine_hashes(&reversed);
        assert_ne!(combined, combined_rev);
    }

    #[test]
    fn test_hash_short() {
        let hash = content_hash_bytes(b"test");
        assert_eq!(hash.short().len(), 8);
    }
}

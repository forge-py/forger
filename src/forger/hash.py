"""Content hashing using BLAKE3.

Wraps the Rust core hashing functionality for use in Python code.
"""

import hashlib
from pathlib import Path

from forger.core import HAS_RUST_CORE

if HAS_RUST_CORE:
    from forger.forger_core import content_hash_bytes_py


def _hash_bytes(data: bytes) -> str:
    if HAS_RUST_CORE:
        return content_hash_bytes_py(data)
    return hashlib.blake2b(data, digest_size=32).hexdigest()


def content_hash(data) -> str:
    """Hash bytes or a file path using BLAKE3 and return the hex digest."""
    if isinstance(data, Path):
        data = data.read_bytes()
    return _hash_bytes(data)


def content_hash_bytes(data: bytes) -> str:
    """Hash bytes using BLAKE3 and return the hex digest."""
    return _hash_bytes(data)


class HashValue:
    """A content hash value with short identifier."""

    def __init__(self, hex_digest: str) -> None:
        self.hex_digest = hex_digest

    @property
    def short(self) -> str:
        return self.hex_digest[:8]

    def __str__(self) -> str:
        return self.hex_digest

    def __repr__(self) -> str:
        return f"HashValue({self.hex_digest!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, HashValue):
            return self.hex_digest == other.hex_digest
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.hex_digest)


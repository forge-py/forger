"""Cache management for incremental builds."""

import json
import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

logger = logging.getLogger(__name__)


class CacheState(Enum):
    """Cache state for a file."""

    Unchanged = auto()
    Modified = auto()
    New = auto()


@dataclass
class CacheEntry:
    """A single cache entry."""

    path: str
    hash: str
    size: int


class Cache:
    """Content-addressed cache for incremental builds."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir
        self._entries: dict[str, CacheEntry] = {}

    @classmethod
    def new(cls) -> "Cache":
        return cls()

    @classmethod
    def with_directory(cls, cache_dir: Path) -> "Cache":
        cache = cls(cache_dir=cache_dir)
        cache.load()
        return cache

    def _state_file(self) -> Path:
        assert self.cache_dir is not None
        return self.cache_dir / "cache_index.json"

    def load(self) -> None:
        if not self.cache_dir:
            return
        state_file = self._state_file()
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text())
                for path, info in data.get("entries", {}).items():
                    self._entries[path] = CacheEntry(
                        path=path,
                        hash=info["hash"],
                        size=info["size"],
                    )
            except (json.JSONDecodeError, KeyError):
                logger.warning("Invalid cache index, starting fresh")

    def save(self) -> None:
        if not self.cache_dir:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        entries = {p: {"hash": e.hash, "size": e.size} for p, e in self._entries.items()}
        self._state_file().write_text(json.dumps({"entries": entries}))

    def update(self, path: Path, hash_value: str, size: int) -> None:
        self._entries[str(path)] = CacheEntry(path=str(path), hash=hash_value, size=size)

    def contains(self, path: Path) -> bool:
        return str(path) in self._entries

    def len(self) -> int:
        return len(self._entries)

    def check_state(self, path: Path, hash_value: str) -> CacheState:
        old = self._entries.get(str(path))
        if old is None:
            return CacheState.New
        if old.hash != hash_value:
            return CacheState.Modified
        return CacheState.Unchanged

    def needs_rebuild(self, current: list[tuple[str, str]]) -> bool:
        """Check if a rebuild is needed given current file hashes.

        Args:
            current: List of (path, hash) tuples representing current state.
        """
        current_set = set(current)
        cached_set = set((e.path, e.hash) for e in self._entries.values())
        return current_set != cached_set

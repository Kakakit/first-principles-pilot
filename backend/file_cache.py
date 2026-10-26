"""Bounded process caches; filesystem metadata changes invalidate every entry."""
import copy
import threading
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path


def identity(path):
    path = Path(path).resolve()
    stat = path.stat()
    return (str(path), stat.st_dev, stat.st_ino, stat.st_size,
            stat.st_mtime_ns, stat.st_ctime_ns)


class ByteCache:
    """LRU bounded by retained bytes as well as entries; oversized values bypass it."""
    def __init__(self, max_bytes, max_entries=48):
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self.entries = OrderedDict()
        self.size = 0
        self.lock = threading.RLock()

    def get(self, key):
        with self.lock:
            item = self.entries.get(key)
            if item is None:return None
            self.entries.move_to_end(key)
            return item[0]

    def put(self, key, value, size):
        with self.lock:
            old = self.entries.pop(key, None)
            if old:self.size -= old[1]
            if size > self.max_bytes:return
            self.entries[key] = (value, size);self.size += size
            while self.size > self.max_bytes or len(self.entries) > self.max_entries:
                _, (_, removed) = self.entries.popitem(last=False);self.size -= removed


_byte_cache = ByteCache(32 * 1024 * 1024)


def _bytes(key):
    value = _byte_cache.get(key)
    if value is None:
        value = Path(key[0]).read_bytes()
        _byte_cache.put(key, value, len(value))
    return value


def read_bytes(path):
    return _bytes(identity(path))


@lru_cache(maxsize=48)
def _parsed(key, species, parser):
    return parser(Path(key[0]), list(species))


def potcar(path, species, parser):
    return copy.deepcopy(_parsed(identity(path), tuple(species), parser))

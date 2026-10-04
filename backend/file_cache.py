"""Bounded process caches; filesystem metadata changes invalidate every entry."""
import copy
from functools import lru_cache
from pathlib import Path


def identity(path):
    path = Path(path).resolve()
    stat = path.stat()
    return (str(path), stat.st_dev, stat.st_ino, stat.st_size,
            stat.st_mtime_ns, stat.st_ctime_ns)


@lru_cache(maxsize=48)
def _bytes(key):
    return Path(key[0]).read_bytes()


def read_bytes(path):
    return _bytes(identity(path))


@lru_cache(maxsize=48)
def _parsed(key, species, parser):
    return parser(Path(key[0]), list(species))


def potcar(path, species, parser):
    return copy.deepcopy(_parsed(identity(path), tuple(species), parser))

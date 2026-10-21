"""Read-only captions from frozen input metadata; never inspect calculation outputs."""
import json
import re
import zipfile
from functools import lru_cache
from pathlib import Path


def manifest_note(manifest):
    for key in ('input_reason', 'reason'):
        value = manifest.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


@lru_cache(maxsize=256)
def _read(path, version, wannier):
    if wannier:
        if version[1] > 1024 * 1024:
            raise ValueError('frozen input too large')
        data = json.loads(Path(path).read_text())
    else:
        with zipfile.ZipFile(path) as archive:
            if archive.getinfo('manifest.json').file_size > 1024 * 1024:
                raise ValueError('frozen manifest too large')
            data = json.loads(archive.read('manifest.json'))
    return manifest_note(data)


def frozen_note(record, root=None):
    from .execution import ROOT
    key = record.get('bundle_id')
    if not isinstance(key, str) or not re.fullmatch('[a-f0-9]{64}', key):
        return None
    wannier = record.get('kind') == 'wannier90'
    path = (root or ROOT) / ('wannier-bundles' if wannier else 'inputs') / (key + ('.json' if wannier else '.zip'))
    try:
        stat = path.stat()
        return _read(str(path.resolve()), (stat.st_mtime_ns, stat.st_size), wannier)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile, UnicodeError):
        return None

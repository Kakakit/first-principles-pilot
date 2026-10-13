from .calculation_nodes import owner as execution_owner, case_info
"""Read-only, attempt-bound remote artifact inspection and small-file transfer."""
import base64
import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from . import execution, remote_paths
from .storage import ROOT

MAX_PULL_BYTES = 10 * 1024 * 1024
MAX_PREVIEW_BYTES = 256 * 1024
MAX_ENTRIES = 2000
SCHOOL_HOSTS = {*(f'jutai-{index}' for index in range(1, 6)), 'gengzi-187'}


def attempts(project_id, node_id):
    rows = [item for item in execution.history(project_id, limit=None)
            if item.get('project_id') == project_id and execution_owner(item) == node_id]
    rows = [{**r, **case_info(r)} for r in rows]
    return sorted(rows, key=lambda item: item.get('created_at', ''), reverse=True)


def _root(record):
    path = execution.attempt_path(record)['shell']
    if not isinstance(path, str) or not path.startswith('/'):
        raise ValueError('该历史任务没有可验证的远程绝对路径')
    return path


def _relative(value):
    if not isinstance(value, str) or not value or len(value) > 4096 or '\x00' in value or '\n' in value:
        raise ValueError('文件相对路径无效')
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ('', '.', '..') for part in path.parts):
        raise ValueError('文件必须位于当前 attempt 下')
    return str(path)


def _category(name):
    base = PurePosixPath(name).name
    if name.startswith('_fpilot/') or base.startswith('.') or base in ('input-manifest.sha256',):
        return 'evidence'
    if base in ('INCAR', 'POSCAR', 'KPOINTS', 'POTCAR', 'run.pbs', 'run.slurm'):
        return 'input'
    if base in ('WAVECAR', 'CHGCAR'):
        return 'checkpoint'
    if base in ('OUTCAR', 'CONTCAR', 'OSZICAR', 'vasprun.xml', 'vasp.out', 'vasp.err', 'pbs.out', 'pbs.err', 'vasp.slurm.out', 'vasp.slurm.err'):
        return 'output'
    return 'other'


def refresh(key):
    record = execution.get(key)
    root = _root(record)
    root_q = remote_paths.shell_path(root)
    command = r'''set -e
base=%s
test -d "$base"
cd "$base"
count=0
while IFS= read -r -d '' file; do
  count=$((count+1)); test "$count" -le %d || { printf 'FPILOT_TRUNCATED=1\n'; break; }
  rel=${file#./}
  size=$(stat -c '%%s' -- "$file" 2>/dev/null || stat -f '%%z' -- "$file")
  mtime=$(stat -c '%%Y' -- "$file" 2>/dev/null || stat -f '%%m' -- "$file")
  printf 'FPILOT_FILE=%%s|%%s|' "$size" "$mtime"
  printf '%%s' "$rel" | base64 | tr -d '\n'
  printf '\n'
done < <(find . -xdev -type f -print0)
''' % (root_q, MAX_ENTRIES)
    receipt = execution.ssh(record['config']['alias'], command, timeout=45)
    if receipt.get('exit_code') != 0:
        raise ValueError('远程 attempt 文件列表读取失败：' + str(receipt.get('stderr', ''))[-400:])
    files = []
    for line in receipt.get('stdout', '').splitlines():
        match = re.fullmatch(r'FPILOT_FILE=(\d+)\|(\d+)\|([A-Za-z0-9+/=]+)', line)
        if not match:
            continue
        try:
            name = base64.b64decode(match.group(3), validate=True).decode('utf-8')
            name = _relative(name)
        except (ValueError, UnicodeError):
            continue
        files.append({'path': name, 'name': PurePosixPath(name).name,
                      'size_bytes': int(match.group(1)),
                      'modified_at': datetime.fromtimestamp(int(match.group(2)), timezone.utc).isoformat(),
                      'category': _category(name),
                      'previewable': int(match.group(1)) <= MAX_PULL_BYTES})
    files.sort(key=lambda item: (item['category'], item['path']))
    result = {'attempt_id': key, 'host': record['config']['alias'], 'root': root,
            'files': files, 'truncated': 'FPILOT_TRUNCATED=1' in receipt.get('stdout', ''),
            'limit': MAX_ENTRIES, 'observed_state': record['state'], 'source_identity': _identity(record), 'refreshed_at': datetime.now(timezone.utc).isoformat(),
            'remote_mutation': False}
    cache = ROOT / 'artifact-cache' / (key + '.json')
    cache.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=cache.parent, delete=False) as handle:
        json.dump(result, handle, ensure_ascii=False)
        temporary = handle.name
    os.replace(temporary, cache)
    return result


def _identity(record):
    return {'alias': record['config']['alias'], 'root': _root(record),
            'bundle_id': record.get('bundle_id'), 'job_id': record.get('job_id')}


def cached_listing(record):
    """Only a complete observation of this terminal run proves missing files."""
    try:
        result = json.loads((ROOT / 'artifact-cache' / (record['id'] + '.json')).read_text())
        if (result.get('attempt_id') == record['id'] and not result.get('truncated')
                and record['state'] in ('COMPLETED', 'FAILED', 'CANCELLED')
                and result.get('observed_state') == record['state']
                and result.get('source_identity') == _identity(record)
                and result.get('refreshed_at')):
            return result
    except (OSError, ValueError, KeyError):
        pass
    return None


def listing(key):
    record = execution.get(key)
    result = cached_listing(record)
    if result:
        return {**result, 'cached': True, 'verification_status': 'VERIFIED'}
    return refresh(key)


def _small_bytes(record, relative, mode='tail', limit=MAX_PREVIEW_BYTES):
    relative = _relative(relative)
    limit = max(1, min(int(limit), MAX_PREVIEW_BYTES))
    root = _root(record)
    root_q, rel_q = remote_paths.shell_path(root), shlex.quote(relative)
    reader = 'head -c "$limit" -- "$target"' if mode == 'head' else 'tail -c "$limit" -- "$target"'
    command = r'''set -e
base=%s
rel=%s
base_abs=$(cd "$base" && pwd -P)
target=$(realpath "$base/$rel")
case "$target" in "$base_abs"/*) ;; *) exit 42;; esac
test -f "$target"; test ! -L "$target"
size=$(stat -c '%%s' -- "$target" 2>/dev/null || stat -f '%%z' -- "$target")
test "$size" -le %d
limit=%d
printf 'FPILOT_SIZE=%%s\n' "$size"
printf 'FPILOT_DATA_BEGIN\n'
%s | base64
''' % (root_q, rel_q, MAX_PULL_BYTES, limit, reader)
    receipt = execution.ssh(record['config']['alias'], command, timeout=45)
    if receipt.get('exit_code') != 0:
        raise ValueError('文件不可预览：可能不存在、已越界或超过 10 MiB')
    match = re.search(r'^FPILOT_SIZE=(\d+)\nFPILOT_DATA_BEGIN\n([A-Za-z0-9+/=\s]*)$', receipt['stdout'], re.M)
    if not match:
        raise ValueError('远程文件预览回执无法解析')
    return base64.b64decode(''.join(match.group(2).split()), validate=True), int(match.group(1))


def _oszicar(text):
    points = []
    for line in text.splitlines():
        ionic = re.search(r'^\s*(\d+)\s+F=\s*([-+0-9.Ee]+)\s+E0=\s*([-+0-9.Ee]+)\s+d E\s*=\s*([-+0-9.Ee]+)(?:.*?mag=\s*([-+0-9.Ee]+))?', line)
        electronic = re.search(r'^\s*(DAV|RMM):\s*(\d+)\s+([-+0-9.Ee]+)\s+([-+0-9.Ee]+)', line)
        if ionic:
            points.append({'kind': 'ionic', 'step': int(ionic.group(1)), 'free_energy_eV': float(ionic.group(2)),
                           'energy_eV': float(ionic.group(3)), 'delta_eV': float(ionic.group(4)),
                           'magnetization_muB': float(ionic.group(5)) if ionic.group(5) else None})
        elif electronic:
            points.append({'kind': 'electronic', 'algorithm': electronic.group(1), 'step': int(electronic.group(2)),
                           'energy_eV': float(electronic.group(3)), 'delta_eV': float(electronic.group(4))})
    return points[-2000:]


def preview(key, relative, mode='tail', limit=MAX_PREVIEW_BYTES, force=False):
    record = execution.get(key)
    relative = _relative(relative)
    limit = max(1, min(int(limit), MAX_PREVIEW_BYTES))
    cache = None
    if record['state'] in ('COMPLETED', 'FAILED', 'CANCELLED'):
        snapshot = refresh(key) if force else listing(key)
        identity = f"{key}|{relative}|{mode}|{limit}|{snapshot['refreshed_at']}"
        cache = ROOT / 'artifact-cache' / 'previews' / (hashlib.sha256(identity.encode()).hexdigest() + '.json')
        if not force and cache.is_file():
            try:
                return {**json.loads(cache.read_text(encoding='utf-8')), 'cached': True}
            except (OSError, ValueError):
                pass
    raw, size = _small_bytes(record, relative, mode, limit)
    name = PurePosixPath(relative).name
    text = raw.decode('utf-8', errors='replace')
    result = {'attempt_id': key, 'path': _relative(relative), 'size_bytes': size,
              'partial': size > len(raw), 'mode': mode, 'text': text}
    if name in ('POSCAR', 'CONTCAR') and size <= 256 * 1024:
        from .structures import parse_structure
        result['structure'] = parse_structure(name, 'poscar', base64.b64encode(raw).decode())
    elif name == 'OSZICAR':
        result['oszicar'] = _oszicar(text)
    elif name == 'OUTCAR':
        result['summary'] = {
            'vasp_timing': 'General timing and accounting informations' in text,
            'ediff_reached': 'aborting loop because EDIFF is reached' in text,
            'fatal_markers': [marker for marker in ('VERY BAD NEWS', 'ZBRENT: fatal error', 'internal error in subroutine PRICEL') if marker in text],
        }
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=cache.parent, delete=False) as handle:
            json.dump(result, handle, ensure_ascii=False)
            temporary = handle.name
        os.replace(temporary, cache)
    return result


def pull(key, relative):
    record = execution.get(key)
    relative = _relative(relative)
    remote = _root(record).rstrip('/') + '/' + relative
    alias = record['config']['alias']
    if alias not in SCHOOL_HOSTS:
        raise ValueError('该 SSH 别名不在 Web App 允许的学校集群清单中')
    root_q = remote_paths.shell_path(_root(record))
    rel_q = shlex.quote(relative)
    command = r'''set -e
base=%s
rel=%s
base_abs=$(cd "$base" && pwd -P)
target=$(realpath "$base/$rel")
case "$target" in "$base_abs"/*) ;; *) exit 42;; esac
test -f "$target"; test ! -L "$target"
LC_ALL=C stat -c '%%F|%%s' -- "$target" 2>/dev/null || LC_ALL=C stat -f '%%HT|%%z' -- "$target"
''' % (root_q, rel_q)
    from . import safe_files,recycle
    recycle.assert_available('attempts',key,record)
    with tempfile.TemporaryDirectory(prefix='fpilot-pull-') as temp:
        target=Path(temp)/PurePosixPath(relative).name
        safe_files.pull_many(alias,[{'remote_path':remote,'destination':str(target)}],companion_command=command)
        return target.read_bytes(), target.name


def technical_evidence(key):
    record = execution.get(key)
    try:
        from .execution_control import events
        event_rows = events(key)
    except (ValueError, OSError):
        event_rows = []
    return {'schema': 'fpilot.technical-evidence.v1', 'exported_at': datetime.now(timezone.utc).isoformat(),
            'attempt': record, 'events': event_rows}

"""Portable, project-scoped records and content-addressed local attachments.

Never copy an open SQLite file or infer execution evidence from directory names.
"""
import base64
import copy
import hashlib
import json
import os
import re
import sqlite3
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from .storage import WorkspaceStore, validate_workspace

SCHEMA = 'fpilot.project-snapshot.v1'
TABLES = {
    'attempts': 'id TEXT PRIMARY KEY,state TEXT NOT NULL,body TEXT NOT NULL',
    'chain_runs': 'id TEXT PRIMARY KEY,project_id TEXT NOT NULL,state TEXT NOT NULL,body TEXT NOT NULL',
    'scan_batches': 'id TEXT PRIMARY KEY,project_id TEXT NOT NULL,state TEXT NOT NULL,body TEXT NOT NULL',
    'branch_hosts': 'project_id TEXT NOT NULL,scope TEXT NOT NULL,alias TEXT NOT NULL,state TEXT NOT NULL,attempt_id TEXT NOT NULL,PRIMARY KEY(project_id,scope)',
    'legacy_branch_owners': 'attempt_id TEXT PRIMARY KEY,scope TEXT NOT NULL',
    'execution_events': 'id INTEGER PRIMARY KEY,attempt_id TEXT NOT NULL,at TEXT NOT NULL,event TEXT NOT NULL,details TEXT NOT NULL',
}
DIRS = ('analyses', 'analyses-v2', 'plots', 'plot-datasets', 'wannier-comparisons', 'analysis-datasets')
SIDE = ('failure-notes', 'failure-snapshots', 'result-summaries', 'window-summaries')
ALLOWED = set(DIRS + SIDE + ('inputs', 'wannier-bundles', 'job-templates', 'recycle', 'artifact-deletions', 'task-recovery'))
MAX_FILE = 256 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def path_at(root, relative):
    parts = PurePosixPath(relative).parts
    if not parts or parts[0] not in ALLOWED or any(p in ('..', '.') for p in parts) or '\\' in relative or '\x00' in relative:
        raise ValueError('快照附件路径越界')
    if PurePosixPath(relative).is_absolute():
        raise ValueError('快照附件路径必须为相对路径')
    target = Path(root).joinpath(*parts)
    if any(p.is_symlink() for p in [target, *target.parents] if p != Path(root).parent):
        raise ValueError('快照附件不能经过符号链接')
    return target


def canonical_file(relative, raw, root):
    if mappable(relative):
        try:return packed(migrate(json.loads(raw), str(root), '<WORKSPACE>'))
        except ValueError:pass
    return raw


def mappable(relative):
    parts = PurePosixPath(relative).parts
    # Raw evidence, frozen bundles and published output files keep their bytes
    # and hashes. Only operational local metadata contains migrated paths.
    return relative.endswith('.json') and parts[0] != 'wannier-bundles' and not set(parts[2:-1]).intersection({'files', 'outputs', 'package'})


def read_tables(root, filename):
    """SQLite online backup yields a consistent database, then scope its rows."""
    path = Path(root) / filename
    if not path.exists():
        return {}
    with closing(sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(':memory:')) as snapshot:
        source.backup(snapshot)
        result = {}
        for (name,) in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            if name not in TABLES and name != 'problems':
                continue
            columns = [row[1] for row in snapshot.execute('PRAGMA table_info(' + name + ')')]
            result[name] = [dict(zip(columns, row)) for row in snapshot.execute('SELECT * FROM ' + name)]
        return result


def associated(value, project_id, attempts):
    if isinstance(value, dict):
        if value.get('project_id') == project_id or (isinstance(value.get('project'), dict) and value['project'].get('id') == project_id):
            return True
        if any(value.get(k) in attempts for k in ('attempt_id', 'source_id', 'source_attempt_id', 'parent_attempt_id') if isinstance(value.get(k), str)):
            return True
        return any(associated(v, project_id, attempts) for v in value.values())
    if isinstance(value, list):
        return any(associated(v, project_id, attempts) for v in value)
    return False


def capture(root, project_id):
    root = Path(root).resolve()
    current = WorkspaceStore(root).read()
    project = next((p for p in (current['workspace'] or {}).get('projects', []) if p['id'] == project_id), None)
    if project is None:
        raise ValueError('本机项目不存在；请先保存草稿')
    tables = read_tables(root, 'execution.sqlite3')
    attempts = {row['id'] for row in tables.get('attempts', []) if json.loads(row['body']).get('project_id') == project_id}
    selected = {}
    for table, rows in tables.items():
        if table in ('execution_events', 'legacy_branch_owners'):
            selected[table] = [r for r in rows if r['attempt_id'] in attempts]
            if table == 'execution_events':
                for row in selected[table]:
                    row.pop('id')  # Local event row IDs belong to the destination DB.
        elif table == 'branch_hosts':
            selected[table] = [r for r in rows if r['project_id'] == project_id]
        else:
            selected[table] = [r for r in rows if json.loads(r['body']).get('project_id') == project_id]
    problems = [r for r in read_tables(root, 'problem-records.sqlite3').get('problems', []) if json.loads(r['body']).get('project_id') == project_id]
    files, objects, missing = {}, {}, []

    def add(path):
        relative = path.relative_to(root).as_posix()
        target = path_at(root, relative)
        if not target.is_file():
            missing.append({'path': relative, 'available': False, 'reason': '原设备未保存本机附件'})
            return
        before = target.stat()
        if before.st_size > MAX_FILE:
            raise ValueError('本机附件超过 256 MiB，请先整理：' + relative)
        raw = target.read_bytes()
        after = target.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('附件正在变化，请稍后重新上传：' + relative)
        sha = digest(raw)
        objects[sha] = raw
        files[relative] = {'sha256': sha, 'size': len(raw)}
        if mappable(relative):
            try:
                files[relative]['portable_sha256'] = digest(packed(migrate(json.loads(raw), str(root), '<WORKSPACE>')))
            except ValueError:
                pass
        if sum(len(v) for v in objects.values()) > MAX_TOTAL:
            raise ValueError('项目本机附件超过 2 GiB，尚未上传')

    # Include unused frozen inputs of this project as well as those used by tasks.
    for path in sorted((root / 'inputs').glob('*.zip')):
        with zipfile.ZipFile(path) as archive:
            if json.loads(archive.read('project.json')).get('id') == project_id:
                add(path)
    for path in sorted((root / 'wannier-bundles').glob('*.json')):
        if json.loads(path.read_bytes()).get('project_id') == project_id:
            add(path)
    analyses = 0
    for folder in DIRS:
        for directory in sorted((root / folder).glob('*')):
            metadata = directory / ('record.json' if folder in ('plot-datasets', 'wannier-comparisons', 'analysis-datasets') else 'analysis.json')
            if not metadata.is_file() or not associated(json.loads(metadata.read_bytes()), project_id, attempts):
                continue
            metadata_value = json.loads(metadata.read_bytes())
            analyses += 1
            for item in metadata_value.get('outputs', []):
                if not isinstance(item, dict) or not item.get('available') or not isinstance(item.get('path'), str):continue
                output = directory / 'outputs' / item['path']
                if output.is_relative_to(directory) and '..' not in output.parts and not output.exists():
                    missing.append({'path': output.relative_to(root).as_posix(), 'available': False, 'reason': '原设备记录可用，但本机附件已缺失'})
            for item in metadata_value.get('files', []):
                if not isinstance(item, dict) or not item.get('available') or not isinstance(item.get('path'), str):continue
                output = directory / 'files' / item['path']
                if '..' not in output.parts and not output.exists():
                    missing.append({'path': output.relative_to(root).as_posix(), 'available': False, 'reason': '原设备登记附件已缺失'})
            for path in sorted(directory.rglob('*')):
                if path.is_symlink():
                    raise ValueError('分析附件含符号链接：' + str(path))
                if path.is_file() and not path.name.endswith(('.lock', '.tmp')):
                    add(path)
    for aid in sorted(attempts):
        for folder in SIDE:
            path = root / folder / (aid + '.json')
            if path.exists():
                add(path)
    for path in sorted((root / 'recycle').rglob('*')):
        if path.is_file() and 'local-cleanup' not in path.parts:
            try:
                value = json.loads(path.read_bytes())
            except (ValueError, OSError):
                continue
            if associated(value, project_id, attempts):
                add(path)
    for path in sorted((root / 'artifact-deletions').glob('*.json')):
        if associated(json.loads(path.read_bytes()), project_id, attempts):
            add(path)
    recovery_operations=[]
    for path in sorted((root/'task-recovery').glob('*.json')):
        operation=json.loads(path.read_bytes())
        if operation.get('project_id')==project_id:add(path);recovery_operations.append(operation)
    # User templates referenced in a frozen record or draft are portable scripts.
    text = packed([project, selected,recovery_operations]).decode()
    for path in sorted((root / 'job-templates' / 'metadata').glob('*.json')):
        value = json.loads(path.read_bytes())
        if value.get('id') and value['id'] in text:
            add(path)
            add(root / 'job-templates' / 'scripts' / value['script_hash'])
    manifest = {'schema': SCHEMA, 'project': project, 'origin_root': str(root), 'tables': selected,
                'problems': problems, 'files': files, 'missing': missing,
                'counts': {'attempts': len(attempts), 'analyses': analyses, 'chains': len(selected.get('chain_runs', [])), 'scans': len(selected.get('scan_batches', []))}}
    return manifest, objects


def fingerprint(manifest):
    value = copy.deepcopy(manifest)
    value.pop('origin_root', None)
    value.pop('servers', None)
    value.pop('snapshot_id', None)
    value.pop('snapshot_created_at', None)
    value['tables'] = {table: rows for table, rows in value['tables'].items() if rows}
    for entry in value['files'].values():
        if entry.get('portable_sha256'):
            entry['sha256'] = entry.pop('portable_sha256')
            entry.pop('size', None)
    # Paths in evidence remain original; portable comparisons normalize local root.
    value = migrate(value, manifest['origin_root'], '<WORKSPACE>')
    return digest(packed(value))


def migrate(value, old_root, new_root):
    if isinstance(value, str):
        if value.startswith(old_root + os.sep):
            return new_root + '/' + value[len(old_root) + 1:].replace(os.sep, '/')
        # Bodies are JSON strings. Keep the DB format while mapping local paths.
        if value.startswith('{') or value.startswith('['):
            try:
                parsed=json.loads(value)
                changed=migrate(parsed,old_root,new_root)
                return value if changed==parsed else json.dumps(changed,ensure_ascii=False,sort_keys=True)
            except ValueError:
                pass
        return value
    if isinstance(value, dict):
        return {k: migrate(v, old_root, new_root) for k, v in value.items()}
    if isinstance(value, list):
        return [migrate(v, old_root, new_root) for v in value]
    return value


def validate(manifest, objects):
    if manifest.get('schema') not in (SCHEMA,'fpilot.project-snapshot.v2'):
        raise ValueError('快照版本不支持')
    project = manifest['project']
    validate_workspace({'projects': [project], 'activeId': project['id']})
    if set(manifest['tables']) - set(TABLES):
        raise ValueError('快照数据库表不支持')
    for table, rows in manifest['tables'].items():
        if table in ('attempts', 'chain_runs', 'scan_batches'):
            if any(json.loads(r['body']).get('project_id') != project['id'] for r in rows):
                raise ValueError('快照含其他项目的执行记录')
    total = 0
    attempts = {r['id'] for r in manifest['tables'].get('attempts', [])}
    for table in ('execution_events', 'legacy_branch_owners'):
        if any(r['attempt_id'] not in attempts for r in manifest['tables'].get(table, [])):
            raise ValueError('快照事件或归属含其他项目的任务')
    if any(r['project_id'] != project['id'] for r in manifest['tables'].get('branch_hosts', [])):
        raise ValueError('快照含其他项目的分支')
    if any(json.loads(r['body']).get('project_id') != project['id'] for r in manifest['problems']):
        raise ValueError('快照含其他项目的问题记录')
    for relative, entry in manifest['files'].items():
        path_at(Path('/private/tmp/fpilot-snapshot-validation'), relative)
        raw = objects[entry['sha256']]
        if len(raw) != entry['size'] or digest(raw) != entry['sha256'] or len(raw) > MAX_FILE:
            raise ValueError('快照附件身份校验失败：' + relative)
        total += len(raw)
    if total > MAX_TOTAL:
        raise ValueError('快照附件超过大小上限')


def restore(root, manifest, objects, expected_revision, pause_automatic=False):
    """Caller holds the project gate and saves an immutable local backup first."""
    root = Path(root).resolve()
    validate(manifest, objects)
    store = WorkspaceStore(root)
    current = store.read()
    if current['revision'] != expected_revision:
        raise ValueError('本机工作区已变化，请重新预览')
    pid = manifest['project']['id']
    workspace = copy.deepcopy(current['workspace'] or {'projects': [], 'activeId': pid})
    workspace['projects'] = [p for p in workspace['projects'] if p['id'] != pid] + [manifest['project']]
    workspace['activeId'] = pid
    if any(p['id'] != pid and p['remoteSlug'] == manifest['project']['remoteSlug'] for p in workspace['projects']):
        raise ValueError('其他项目已使用该远端目录名，不能接续')
    validate_workspace(workspace)
    local = migrate(manifest, manifest['origin_root'], str(root))
    previous_files = {}
    if any(p['id'] == pid for p in (current['workspace'] or {}).get('projects', [])):
        previous_files = capture(root, pid)[0]['files']
    for relative, entry in manifest['files'].items():
        target = path_at(root, relative)
        if target.exists() and relative not in previous_files:
            incoming = objects[entry['sha256']]
            if canonical_file(relative, target.read_bytes(), root) != canonical_file(relative, incoming, manifest['origin_root']):
                raise ValueError('附件与本机其他记录冲突，未覆盖：' + relative)
    for saved in (root/'task-recovery').glob('*.json'):
        operation=json.loads(saved.read_bytes())
        if operation.get('project_id')==pid and operation.get('state')=='CONFIRMED':
            operation.update(state='PAUSED',paused_at=datetime.now(timezone.utc).isoformat(),snapshot_pause_reason='项目快照还原')
            from .execution_control import write_json
            write_json(saved,operation)
    with store.snapshot_lock, closing(sqlite3.connect(root / 'execution.sqlite3', timeout=10)) as db:
        # Problems and workspace revision change in the same SQLite transaction.
        db.execute('ATTACH DATABASE ? AS notebook', (str(root / 'problem-records.sqlite3'),))
        db.execute('ATTACH DATABASE ? AS workspace_index', (str(root / 'index.sqlite3'),))
        db.execute('BEGIN IMMEDIATE')
        revision_row = db.execute('SELECT revision FROM workspace_index.current WHERE id=1').fetchone()
        if (revision_row[0] if revision_row else 0) != expected_revision:
            raise ValueError('本机工作区已变化，请重新预览')
        old_ids = set()
        db.execute('CREATE TABLE IF NOT EXISTS attempts(' + TABLES['attempts'] + ')')
        for aid, body in db.execute('SELECT id,body FROM attempts'):
            if json.loads(body).get('project_id') == pid:
                old_ids.add(aid)
        for table, definition in TABLES.items():
            db.execute('CREATE TABLE IF NOT EXISTS ' + table + '(' + definition + ')')
            rows = local['tables'].get(table, [])
            if table == 'attempts':
                for row in rows:
                    existing = db.execute('SELECT body FROM attempts WHERE id=?', (row['id'],)).fetchone()
                    if existing and json.loads(existing[0]).get('project_id') != pid:
                        raise ValueError('任务 ID 与其他项目冲突；未接续')
                for aid in old_ids:
                    db.execute('DELETE FROM attempts WHERE id=?', (aid,))
            elif table in ('legacy_branch_owners', 'execution_events'):
                for aid in old_ids | {r['id'] for r in local['tables'].get('attempts', [])}:
                    db.execute('DELETE FROM ' + table + ' WHERE attempt_id=?', (aid,))
            else:
                if table in ('chain_runs', 'scan_batches'):
                    for row in rows:
                        existing = db.execute('SELECT project_id FROM ' + table + ' WHERE id=?', (row['id'],)).fetchone()
                        if existing and existing[0] != pid:
                            raise ValueError('链或批次 ID 与其他项目冲突')
                db.execute('DELETE FROM ' + table + ' WHERE project_id=?', (pid,))
            for row in rows:
                columns = list(row)
                allowed = {r[1] for r in db.execute('PRAGMA table_info(' + table + ')')}
                if set(columns) - allowed:
                    raise ValueError('记录字段不支持')
                db.execute('INSERT INTO ' + table + '(' + ','.join(columns) + ') VALUES(' + ','.join('?' for _ in columns) + ')', tuple(row[c] for c in columns))
        db.execute('CREATE TABLE IF NOT EXISTS notebook.problems(id TEXT PRIMARY KEY,source_kind TEXT NOT NULL,source_id TEXT NOT NULL,body TEXT NOT NULL,UNIQUE(source_kind,source_id))')
        for aid, body in db.execute('SELECT id,body FROM notebook.problems').fetchall():
            if json.loads(body).get('project_id') == pid:
                db.execute('DELETE FROM notebook.problems WHERE id=?', (aid,))
        for row in local['problems']:
            db.execute('INSERT INTO notebook.problems VALUES(?,?,?,?)', (row['id'], row['source_kind'], row['source_id'], row['body']))
        # Files are validated before publication. Copies are atomic per file;
        # any interrupted import keeps the execution gate stopped for recovery.
        for relative, entry in manifest['files'].items():
            target = path_at(root, relative)
            raw = objects[entry['sha256']]
            if mappable(relative):
                try:
                    value = migrate(json.loads(raw), manifest['origin_root'], str(root))
                    if relative.startswith(('analyses/', 'analyses-v2/', 'plots/')) and Path(relative).name == 'analysis.json':
                        state = value.get('state')
                        if pause_automatic and state in ('QUEUED','WAIT_RESOURCE'):
                            value.update(state='PAUSED',snapshot_origin_state=state,snapshot_pause_reason='跨设备快照传输')
                        elif state in ('QUEUED', 'WAIT_RESOURCE') or (value.get('location') == 'local' and state in ('RUNNING', 'DISPATCHING')):
                            value.update(state='UNKNOWN', sync_origin_state=state, error='原设备分析尚未完成；接续只保留证据，不自动重新执行')
                    if relative.startswith('task-recovery/') and value.get('state')=='CONFIRMED':
                        value.update(state='PAUSED',paused_at=datetime.now(timezone.utc).isoformat(),snapshot_pause_reason='跨设备还原后需要明确继续恢复范围')
                    if Path(relative).name in ('analysis.json', 'record.json'):
                        missing = {v['path']: v['reason'] for v in manifest['missing']}
                        for field, folder in (('outputs', 'outputs'), ('files', 'files')):
                            for item in value.get(field, []):
                                if isinstance(item, dict) and isinstance(item.get('path'), str):
                                    name = str(PurePosixPath(relative).parent / folder / item['path'])
                                    if name in missing:
                                        item.update(available=False, unavailable_reason=missing[name], sync_original_available=item.get('available'))
                    raw = packed(value)
                except ValueError:
                    pass
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + '.sync-tmp')
            temporary.write_bytes(raw)
            os.replace(temporary, target)
        # An omitted old analysis must not reappear as if it belonged to the new
        # snapshot. Preserve its original bytes outside the active record dirs.
        archive_root = root / 'project-sync' / 'retained-local-files' / digest(packed(manifest))
        for relative in set(previous_files) - set(manifest['files']):
            if relative.startswith('job-templates/'):
                continue  # Templates may be shared by unrelated projects.
            target = path_at(root, relative)
            if target.is_file():
                retained = archive_root / relative
                retained.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, retained)
        portable_workspace = copy.deepcopy(workspace)
        for project in portable_workspace['projects']:
            for node in project['nodes']:
                if node['data'].get('structure'):
                    from .structure_edit import source_bytes
                    store.put_file('originals', source_bytes(node['data']['structure']))
                    node['data']['structure']['source'].pop('base64')
        workspace_digest = store.put_file('snapshots', json.dumps(portable_workspace, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode())
        cursor = db.execute('INSERT INTO workspace_index.revisions(digest,created_at,reason) VALUES(?,?,?)',
                            (workspace_digest, datetime.now(timezone.utc).isoformat(), 'project-sync-continuation'))
        revision = cursor.lastrowid
        db.execute('INSERT OR REPLACE INTO workspace_index.current VALUES(1,?)', (revision,))
        db.execute('DELETE FROM workspace_index.projects')
        for project in workspace['projects']:
            db.execute('INSERT INTO workspace_index.projects VALUES(?,?,?)', (project['id'], project['name'], revision))
        db.commit()
        store.checkpoint(revision, '跨设备还原')
    return store.read()

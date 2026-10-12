"""Versioned local workspace snapshots and content-addressed originals."""
import hashlib
import copy
import base64
import json
import os
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from .structure_edit import source_bytes, input_geometry

MAX_WORKSPACE_BYTES = 32 * 1024 * 1024
ROOT = Path(os.environ.get('FPILOT_WORKSPACE', Path(__file__).resolve().parents[1] / 'workspace-data')).resolve()


class ConflictError(ValueError):
    pass


def validate_workspace(w):
    if 'version' in w or not isinstance(w.get('projects'), list) or not 1 <= len(w['projects']) <= 100:
        raise ValueError('工作区格式或项目数量无效。')
    ids = set()
    for p in w['projects']:
        if not isinstance(p.get('id'), str) or not p['id'] or p['id'] in ids or not str(p.get('name', '')).strip():
            raise ValueError('项目身份或名称无效。')
        ids.add(p['id'])
        if 'stagePolicy' in p and p['stagePolicy'] != 'kind-fixed-v1':
            raise ValueError('项目阶段编号规则无效。')
        remote_slug = p.get('remoteSlug')
        if not isinstance(remote_slug, str) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?', remote_slug):
            raise ValueError('远程项目标识无效。')
        shared_potcar = p.get('sharedPotcar')
        if shared_potcar is not None and (not isinstance(shared_potcar, dict) or
            not re.fullmatch(r'[a-f0-9]{64}', shared_potcar.get('id', '')) or
            (shared_potcar.get('provenance_id') is not None and
             not re.fullmatch(r'[a-f0-9]{64}', shared_potcar['provenance_id']))):
            raise ValueError('项目共享 POTCAR 无效。')
        if len(p['nodes']) > 500 or len(p['edges']) > 2000:
            raise ValueError('节点或连接数量超过上限。')
        nodes = set()
        for n in p['nodes']:
            if not n.get('id') or n['id'] in nodes or n['data']['kind'] not in ('structure','relax','scf','bands','dos','vasp-wannier','wannier90','scan'):
                raise ValueError('节点无效。')
            mode = n['data'].get('executionMode')
            sequence = n['data'].get('stageSequence')
            slug = n['data'].get('stageSlug')
            if mode not in ('vasp', 'wannier90', 'scan') or (n['data']['kind'] == 'scan') != (mode == 'scan') or (n['data']['kind'] == 'wannier90') != (mode == 'wannier90'):
                raise ValueError('节点执行类型无效。')
            if type(sequence) is not int or not 1 <= sequence <= 999 or not isinstance(slug, str) or len(slug) > 48 or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?', slug):
                raise ValueError('远程阶段标识无效。')
            if p.get('stagePolicy') == 'kind-fixed-v1':
                from .node_editability import STAGE_SEQUENCES
                if sequence != STAGE_SEQUENCES[n['data']['kind']]:
                    raise ValueError('新项目的阶段编号须与节点类型一致。')
            nodes.add(n['id'])
            if 'structure' in n['data']:
                if n['data']['kind'] != 'structure':
                    raise ValueError('结构只能绑定输入结构节点。')
                source_bytes(n['data']['structure'])
                if 'poscarText' in n['data']['structure']:
                    input_geometry(n['data']['structure'])
        if any(e['source'] not in nodes or e['target'] not in nodes for e in p['edges']):
            raise ValueError('连接引用不存在的节点。')
    if w.get('activeId') not in ids:
        raise ValueError('当前项目不存在。')
    raw = json.dumps(w, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
    if len(raw) > MAX_WORKSPACE_BYTES:
        raise ValueError('工作区超过 32 MiB；请拆分项目后保存。')
    return raw


class WorkspaceStore:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        for part in ('snapshots', 'originals'):
            (self.root / part).mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS revisions (
                    revision INTEGER PRIMARY KEY AUTOINCREMENT,
                    digest TEXT NOT NULL, created_at TEXT NOT NULL, reason TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS current (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, revision INTEGER NOT NULL);
            ''')
            db.commit()

    def connect(self):
        return sqlite3.connect(str(self.root / 'index.sqlite3'), timeout=10)

    def put_file(self, directory, raw):
        digest = hashlib.sha256(raw).hexdigest()
        path = self.root / directory / digest
        if path.exists():
            if path.read_bytes() != raw:
                raise ValueError('已存文件校验失败；未覆盖原记录。')
            return digest
        temp = path.with_name(digest + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temp.open('xb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            if temp.exists(): temp.unlink()
        return digest

    def read(self, revision=None):
        with closing(self.connect()) as db:
            if revision is None:
                row = db.execute('SELECT revision FROM current WHERE id=1').fetchone()
                revision = row[0] if row else 0
            row = db.execute('SELECT digest FROM revisions WHERE revision=?', (revision,)).fetchone()
        if revision and row is None:
            raise ValueError('快照不存在。')
        w = None
        if row:
            raw = (self.root / 'snapshots' / row[0]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != row[0]:
                raise ValueError('快照文件校验失败；请使用已有 JSON 备份恢复。')
            w = json.loads(raw)
            for project in w['projects']:
                for node in project['nodes']:
                    s = node['data'].get('structure')
                    if s and 'base64' not in s['source']:
                        digest = s['source']['sha256']
                        original = (self.root / 'originals' / digest).read_bytes()
                        if hashlib.sha256(original).hexdigest() != digest:
                            raise ValueError('原始结构文件校验失败。')
                        s['source']['base64'] = base64.b64encode(original).decode()
        return {'revision': revision, 'workspace': w, 'path': str(self.root)}

    def save(self, workspace, expected_revision, reason='autosave'):
        if (self.root / 'project-sync').exists():
            from .project_sync import draft_gate
            with draft_gate(self.root, workspace):
                return self._save(workspace, expected_revision, reason)
        return self._save(workspace, expected_revision, reason)

    def _save(self, workspace, expected_revision, reason):
        validate_workspace(workspace)
        if (self.root / 'project-sync').exists():
            from .project_sync import assert_draft_edits
            assert_draft_edits(self.root, workspace)
        packed = copy.deepcopy(workspace)
        for p in packed['projects']:
            for n in p['nodes']:
                if n['data'].get('structure'):
                    n['data']['structure']['source'].pop('base64')
        raw = json.dumps(packed, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT revision FROM current WHERE id=1').fetchone()
            current = current[0] if current else 0
            digest = hashlib.sha256(raw).hexdigest()
            previous = db.execute('SELECT digest FROM revisions WHERE revision=?', (current,)).fetchone()
            if previous and previous[0] == digest:
                return {'revision': current, 'path': str(self.root)}
            if expected_revision != current:
                raise ConflictError('磁盘版本已变化。请先导出当前编辑，再重新载入磁盘版本。')
            if previous:
                from .node_editability import assert_changes
                assert_changes(self.root, json.loads((self.root / 'snapshots' / previous[0]).read_text()), workspace)
            for p in workspace['projects']:
                for n in p['nodes']:
                    if n['data'].get('structure'):
                        self.put_file('originals', source_bytes(n['data']['structure']))
            self.put_file('snapshots', raw)
            cursor = db.execute('INSERT INTO revisions(digest,created_at,reason) VALUES (?,?,?)',
                                (digest, datetime.now(timezone.utc).isoformat(), reason))
            revision = cursor.lastrowid
            db.execute('INSERT OR REPLACE INTO current(id,revision) VALUES(1,?)', (revision,))
            db.execute('DELETE FROM projects')
            db.executemany('INSERT INTO projects(id,name,revision) VALUES(?,?,?)',
                           [(p['id'], p['name'], revision) for p in workspace['projects']])
            db.commit()
        return {'revision': revision, 'path': str(self.root)}

    def history(self):
        with closing(self.connect()) as db:
            rows = db.execute('SELECT revision,created_at,reason FROM revisions ORDER BY revision DESC LIMIT 100').fetchall()
        return [{'revision': r, 'created_at': t, 'reason': s} for r,t,s in rows]

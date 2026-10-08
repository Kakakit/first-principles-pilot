"""Independent, local problem notebook. No cluster reads or execution decisions."""
import hashlib
import json
import logging
import re
import sqlite3
import zipfile
from contextlib import closing
from . import execution as ex

NOTE_FIELDS = ('user_note', 'suspected_cause', 'confirmed_cause', 'confirmation_evidence')
SOURCE_DIRS = {'analyses': 'analyses-v2', 'local-analyses': 'analyses', 'plots': 'plots'}


def db():
    ex.ROOT.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(ex.ROOT / 'problem-records.sqlite3', timeout=10)
    connection.execute('CREATE TABLE IF NOT EXISTS problems(id TEXT PRIMARY KEY, source_kind TEXT NOT NULL, source_id TEXT NOT NULL, body TEXT NOT NULL, UNIQUE(source_kind,source_id))')
    connection.commit()
    return connection


def identity(kind, key):
    if kind not in ('attempts', *SOURCE_DIRS) or not isinstance(key, str) or not re.fullmatch(r'[a-f0-9]{32}', key):
        raise ValueError('问题来源身份无效')
    return hashlib.sha256((kind + ':' + key).encode()).hexdigest()[:32]


def raw(key):
    if not isinstance(key, str) or not re.fullmatch(r'[a-f0-9]{32}', key):
        raise ValueError('问题记录身份无效')
    with closing(db()) as connection:
        row = connection.execute('SELECT body FROM problems WHERE id=?', (key,)).fetchone()
    return json.loads(row[0]) if row else None


def source(kind, key):
    identity(kind, key)
    if kind == 'attempts':
        return ex.get(key)
    path = ex.ROOT / SOURCE_DIRS[kind] / key / 'analysis.json'
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('来源记录过大')
    return json.loads(path.read_text())


def workspace_projects():
    # Do not construct a new workspace or recover old snapshots implicitly.
    if not (ex.ROOT / 'index.sqlite3').exists():
        return {}
    from .storage import WorkspaceStore
    workspace = WorkspaceStore(ex.ROOT).read().get('workspace') or {}
    return {p['id']: p for p in workspace.get('projects', [])}


def input_evidence(record):
    from .result_overview import frozen_input
    result = frozen_input(record)
    text = record.get('final_incar')
    bundle = record.get('bundle_id', '')
    if re.fullmatch(r'[a-f0-9]{64}', bundle):
        try:
            with zipfile.ZipFile(ex.ROOT / 'inputs' / (bundle + '.zip')) as archive:
                if not text and archive.getinfo('INCAR').file_size <= 256 * 1024:
                    text = archive.read('INCAR').decode()
                if archive.getinfo('KPOINTS').file_size <= 256 * 1024:
                    result['kpoints'] = archive.read('KPOINTS').decode()
        except (OSError, ValueError, KeyError, zipfile.BadZipFile, UnicodeError):
            pass
    result['incar'] = str(text or '')[:262144]
    # A POTCAR identity is sufficient; licensed content and research artifacts stay in place.
    return result


def validate_notes(notes):
    if not isinstance(notes, dict) or set(notes) != set(NOTE_FIELDS) or any(not isinstance(v, str) or len(v) > 4000 for v in notes.values()):
        raise ValueError('备注字段必须为文本，每项不超过 4000 字符')
    if notes['confirmed_cause'].strip() and not notes['confirmation_evidence'].strip():
        raise ValueError('已确认原因必须同时填写确认依据；推测请放入疑似原因')
    return dict(notes)


def excerpt_value(value):
    from .execution_control import redact
    if not isinstance(value, dict) or set(value) - {'file', 'location', 'text'}:
        raise ValueError('日志片段格式无效')
    if any(not isinstance(v, str) for v in value.values()) or len(value.get('text', '')) > 8192 or len(value.get('file', '')) > 1000 or len(value.get('location', '')) > 1000:
        raise ValueError('日志片段最多 8192 字符，文件名与位置最多 1000 字符')
    if not value.get('text', '').strip():
        raise ValueError('请先选中需要记录的日志片段')
    return {k: redact(value.get(k, '')) for k in ('file', 'location', 'text')}


def capture(kind, key, record=None, *, manual=False, notes=None, excerpt=None, projects=None):
    problem_id = identity(kind, key)
    record = record or source(kind, key)
    if notes is not None:
        notes = validate_notes(notes)
    snippet = excerpt_value(excerpt) if excerpt is not None else None
    with closing(db()) as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT body FROM problems WHERE id=?', (problem_id,)).fetchone()
        problem = json.loads(row[0]) if row else None
        # A manually recorded warning may later become a confirmed task failure.
        # Refresh the failure facts once while retaining the user's annotations.
        retained = None
        if problem and record.get('state') == 'FAILED' and problem['state'] != 'FAILED':
            retained = {name: problem[name] for name in ('notes', 'incar_tags', 'excerpts', 'collected_at')}
            problem = None
        if problem is None:
            if record.get('state') != 'FAILED' and not manual:
                return None
            parent = record
            if kind != 'attempts':
                parent_id = record.get('attempt_id') or next((s.get('attempt_id') for s in record.get('sources', []) if s.get('attempt_id')), None)
                try:
                    parent = ex.get(parent_id) if parent_id else record
                except ValueError:
                    parent = record
            project_id = record.get('project_id') or parent.get('project_id')
            projects = workspace_projects() if projects is None else projects
            project = projects.get(project_id)
            if not project and parent.get('bundle_id'):
                try:
                    project = ex.frozen_project(parent['bundle_id'])
                except (ValueError, OSError, KeyError, zipfile.BadZipFile):
                    pass
            node = next((n for n in (project or {}).get('nodes', []) if n['id'] == parent.get('node_id')), {})
            data = node.get('data', {})
            from .execution_control import redact
            logs = {name: {k: redact((record.get(name) or {}).get(k)) for k in ('stdout', 'stderr')}
                    for name in ('dispatch_receipt', 'status_receipt')}
            error = record.get('error') or (record.get('evidence') or {}).get('detail')
            if not error:
                error = logs['status_receipt']['stderr'] or logs['dispatch_receipt']['stderr']
            if not error:
                error = ('执行记录为 FAILED；具体错误见失败事实和日志' if record.get('state') == 'FAILED'
                         else record.get('note') or '用户主动记录 · ' + str(record.get('state') or '状态未记录'))
            empty_notes = dict.fromkeys(NOTE_FIELDS, '')
            if kind == 'attempts':
                path = ex.ROOT / 'failure-notes' / (key + '.json')
                if path.exists():
                    empty_notes = validate_notes(json.loads(path.read_text()))
            manifest = record.get('manifest') or record.get('extension') or {}
            problem = {'schema': 'fpilot.problem-record.v1', 'id': problem_id, 'source_kind': kind, 'source_id': key,
                       'project_id': project_id, 'node_id': parent.get('node_id'),
                       'project_name': (project or {}).get('name') or parent.get('project_slug') or '历史项目',
                       'node_name': data.get('label') or parent.get('stage_slug') or '历史节点',
                       'program': ('Wannier90' if parent.get('kind') == 'wannier90' else 'VASP') if kind == 'attempts' else manifest.get('name') or record.get('mode') or '后处理',
                       'category': '计算任务' if kind == 'attempts' else '后处理', 'state': record.get('state'),
                       'server': (record.get('config') or (record.get('runtime') or {}).get('config') or parent.get('config') or {}).get('alias') or (record.get('runtime') or {}).get('alias') or '本机',
                       'occurred_at': record.get('created_at') or ex.now(), 'collected_at': ex.now(),
                       'job_id': record.get('job_id'), 'execution_scope': parent.get('execution_scope'),
                       'scan_case_id': parent.get('scan_case_id'), 'chain_step_id': parent.get('chain_step_id'),
                       'collection': 'failure' if record.get('state') == 'FAILED' else 'manual',
                       'error_summary': redact(error)[:2000],
                       'error_summary_explicit': bool(record.get('error') or (record.get('evidence') or {}).get('detail')),
                       'evidence': {'facts': record.get('evidence') or {}, 'logs': logs, 'input': input_evidence(parent)},
                       'notes': empty_notes, 'incar_tags': [], 'excerpts': [], 'includes_potcar': False}
            if retained:
                problem.update(retained)
        if notes is not None:
            problem['notes'] = notes
        if snippet and snippet not in problem['excerpts']:
            if len(problem['excerpts']) >= 20:
                raise ValueError('每条问题最多保留 20 个日志片段')
            problem['excerpts'].append(snippet)
        problem['updated_at'] = ex.now()
        connection.execute('INSERT INTO problems VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                           (problem_id, kind, key, json.dumps(problem, ensure_ascii=False)))
        connection.commit()
    return get(problem_id)


def try_capture(kind, key, record=None):
    # Recording trouble must never change execution state or interrupt dispatch.
    if not isinstance(key,str) or not re.fullmatch(r'[a-f0-9]{32}',key):return
    try:
        capture(kind, key, record)
    except Exception:
        logging.getLogger(__name__).exception('Problem notebook collection failed for %s/%s', kind, key)


def backfill(projects=None):
    projects = workspace_projects() if projects is None else projects
    if (ex.ROOT / 'execution.sqlite3').exists():
        with closing(ex.db()) as connection:
            records = [json.loads(row[0]) for row in connection.execute('SELECT body FROM attempts')]
        for record in records:
            path = ex.ROOT / 'failure-notes' / (record['id'] + '.json')
            notes = json.loads(path.read_text()) if path.exists() else None
            if record.get('state') == 'FAILED' or notes and any(v.strip() for v in notes.values()):
                existing = raw(identity('attempts', record['id']))
                if existing is None or record.get('state') == 'FAILED' and existing['state'] != 'FAILED':
                    capture('attempts', record['id'], record, manual=bool(notes), projects=projects)
    for kind, directory in SOURCE_DIRS.items():
        filename = 'analysis.json'
        for path in (ex.ROOT / directory).glob('*/' + filename):
            record = json.loads(path.read_text())
            if record.get('state') == 'FAILED' and raw(identity(kind, path.parent.name)) is None:
                capture(kind, path.parent.name, record, projects=projects)


def get(key):
    value = raw(key)
    if value is None:
        raise ValueError('问题记录不存在')
    from . import recycle
    try:
        original=source(value['source_kind'], value['source_id'])
        value['source_deleted'] = recycle.inactive(value['source_kind'], value['source_id'],original)
    except (OSError, ValueError):
        value['source_deleted'] = True
    value['has_notes'] = any(v.strip() for v in value['notes'].values())
    # Old archives may have used a preparation note as their error summary.
    # Derive this display text from retained receipts, without changing evidence.
    logs = value['evidence']['logs']
    stderr = logs['status_receipt']['stderr'] or logs['dispatch_receipt']['stderr']
    if value['collection'] == 'failure' and stderr and not value.get('error_summary_explicit'):
        value['error_summary'] = stderr[:2000]
    elif value['collection'] == 'manual' and value['notes']['user_note'].strip():
        value['error_summary'] = value['notes']['user_note'][:2000]
    return value


def listing():
    backfill()
    with closing(db()) as connection:
        keys = [row[0] for row in connection.execute('SELECT id FROM problems')]
    return sorted([get(key) for key in keys], key=lambda v: v['occurred_at'], reverse=True)


def save(key, notes, tags):
    notes = validate_notes(notes)
    if not isinstance(tags, list) or len(tags) > 30 or any(not isinstance(t, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,49}', t) for t in tags):
        raise ValueError('关联 INCAR 参数应为大写参数名，最多 30 个')
    with closing(db()) as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT body FROM problems WHERE id=?', (key,)).fetchone()
        if not row:
            raise ValueError('问题记录不存在')
        value = json.loads(row[0]); value.update(notes=notes, incar_tags=list(dict.fromkeys(tags)), updated_at=ex.now())
        connection.execute('UPDATE problems SET body=? WHERE id=?', (json.dumps(value, ensure_ascii=False), key))
        connection.commit()
    if value['source_kind'] == 'attempts':
        try:
            ex.get(value['source_id'])
        except ValueError:
            pass
        else:
            from .execution_control import write_json
            write_json(ex.ROOT / 'failure-notes' / (value['source_id'] + '.json'), notes)
    return get(key)


def export(keys, format):
    if not isinstance(keys, list) or not 1 <= len(keys) <= 1000 or format not in ('json', 'markdown'):
        raise ValueError('请选择 1–1000 条问题并选择导出格式')
    records = [get(key) for key in dict.fromkeys(keys)]
    if format == 'json':
        return json.dumps({'schema': 'fpilot.problem-export.v1', 'records': records}, ensure_ascii=False, indent=2)
    parts = ['# 计算问题记录\n']
    for r in records:
        parts.append(f"## {r['project_name']} · {r['node_name']}\n\n{r['program']} · {r['server']} · {r['occurred_at']} · {r['state']}\n\n{r['error_summary']}\n")
        for field in NOTE_FIELDS:
            parts.append(f"### {dict(user_note='用户备注',suspected_cause='疑似原因',confirmed_cause='已确认原因',confirmation_evidence='确认依据')[field]}\n\n{r['notes'][field]}\n")
        parts.append('关联参数：' + ', '.join(r['incar_tags']) + '\n')
        if r['source_deleted']:
            parts.append('原记录已删除；以下为独立保留的收录证据。\n')
        parts.append('### 收录时的失败事实与有限回执\n\n' + '\n'.join(
            '    ' + line for line in json.dumps({name: r['evidence'][name] for name in ('facts', 'logs')},
                                              ensure_ascii=False, indent=2).splitlines()) + '\n')
        for snippet in r['excerpts']:
            parts.append(f"日志：{snippet['file']} · {snippet['location']}\n\n" + '\n'.join('    ' + line for line in snippet['text'].splitlines()) + '\n')
        for name, text in [('INCAR', r['evidence']['input'].get('incar', '')), ('KPOINTS', r['evidence']['input'].get('kpoints', ''))]:
            if text:
                parts.append(f'### {name}\n\n' + '\n'.join('    ' + line for line in text.splitlines()) + '\n')
    return '\n'.join(parts)

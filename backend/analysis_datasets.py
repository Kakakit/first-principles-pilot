"""Local, independently retained snapshots of explicitly registered analyses."""
import copy
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from filelock import FileLock
from .storage import ROOT
from . import recycle
from .extension_protocol import atomic_json

MAX_BYTES = 10 * 1024 * 1024
SOURCES = {'v2': ('analyses-v2', 'fpilot.analysis.v2', 'analyses'),
           'v1': ('analyses', 'fpilot.analysis-record.v1', 'local-analyses'),
           'local': ('analyses', 'fpilot.local-analysis.v1', 'local-analyses'),
           'plot': ('plots', 'fpilot.local-plot.v1', 'plots'),
           'comparison': ('wannier-comparisons','fpilot.wannier-band-comparison.v1','comparisons')}


def now():
    return datetime.now(timezone.utc).isoformat()


def inside(base, name):
    if not isinstance(name, str) or not name or '\\' in name or '\x00' in name:
        raise ValueError('结果文件路径无效')
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('结果文件路径越界')
    target = base / relative
    if target == base or any(part.is_symlink() for part in [target, *target.parents] if part == ROOT or ROOT in part.parents):
        raise ValueError('结果文件不能是链接')
    if not target.resolve().is_relative_to(base.resolve()):
        raise ValueError('结果文件路径越界')
    return target


def directory(key):
    if not isinstance(key, str) or not re.fullmatch(r'[a-f0-9]{64}', key):
        raise ValueError('数据集标识无效')
    return inside(ROOT, 'analysis-datasets/' + key)


def source_path(kind, key):
    if kind not in SOURCES or not isinstance(key, str) or not re.fullmatch(r'[a-f0-9]{32}', key):
        raise ValueError('分析来源标识无效')
    return inside(ROOT, SOURCES[kind][0] + '/' + key + ('/record.json' if kind=='comparison' else '/analysis.json'))


def read_json(path, max_bytes=MAX_BYTES):
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError('结果记录不存在或超过 10 MiB')
    with path.open('rb') as stream:
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError('结果记录超过大小限制')
    value = json.loads(raw)
    json.dumps(value, allow_nan=False)
    return value


def identity(kind, key):
    source_path(kind, key)
    return hashlib.sha256((kind + ':' + key).encode()).hexdigest()


def registration_status(kind, key):
    dataset_id = identity(kind, key)
    exists = (directory(dataset_id) / 'record.json').is_file()
    return {'dataset_id': dataset_id if exists else None,
            'dataset_recycled': exists and recycle.is_recycled('analysis-datasets', dataset_id)}


def context():
    projects, attempts = {}, {}
    database = ROOT / 'index.sqlite3'
    if database.is_file():
        with sqlite3.connect('file:' + str(database.resolve()) + '?mode=ro', uri=True) as connection:
            row = connection.execute('SELECT digest FROM revisions JOIN current ON revisions.revision=current.revision WHERE current.id=1').fetchone()
            if row:
                projects = {item['id']: item for item in read_json(inside(ROOT, 'snapshots/' + row[0]), 32 * 1024 * 1024).get('projects', [])}
    database = ROOT / 'execution.sqlite3'
    if database.is_file():
        with sqlite3.connect('file:' + str(database.resolve()) + '?mode=ro', uri=True) as connection:
            attempts = {item['id']: item for item in (json.loads(row[0]) for row in connection.execute('SELECT body FROM attempts'))}
    return projects, attempts


def source_ids(record):
    sources = record.get('sources') or []
    if isinstance(sources, dict):
        sources = list(sources.values())
    return list(dict.fromkeys([record['attempt_id']] if record.get('attempt_id') else
                             [source['attempt_id'] for source in sources if source.get('attempt_id')]))


def summary(kind, record, projects, attempts):
    ids = source_ids(record)
    owner = record.get('project_id')
    if not owner and ids:
        owners = {attempts[key].get('project_id') for key in ids if key in attempts}
        if len(owners) == 1:
            owner = next(iter(owners))
    project = projects.get(owner) or {}
    names = []
    for key in ids:
        attempt = attempts.get(key) or {}
        node = next((node for node in project.get('nodes', []) if node['id'] == attempt.get('node_id')), {})
        names.append(node.get('data', {}).get('label') or attempt.get('stage_slug') or '来源节点已不存在')
    if not names:
        sources = record.get('sources') or []
        names = [source.get('name') or '结构来源' for source in sources] if isinstance(sources, list) else []
    manifest = record.get('manifest') or record.get('extension') or {}
    name = ('DFT–Wannier 能带叠图' if kind=='comparison' else manifest.get('name')) or ('极化比较' if record.get('kind') == 'polarization' else record.get('kind') or record.get('mode') or '历史分析')
    result = record.get('result') or {}
    return {'source_kind': kind, 'source_id': record['id'], 'project_id': owner,
            'project_name': project.get('name') or '未关联项目', 'source_names': names,
            'source_attempt_ids': ids, 'analysis_name': name, 'analysis_version': manifest.get('version') or '未记录',
            'display_title':record.get('title') or ' · '.join([name,'／'.join(names)]).strip(' ·'),
            'created_at': record.get('created_at', ''), 'state': 'COMPLETED' if kind=='comparison' else record.get('state'),
            'summary': result.get('summary') or record.get('summary') or '',
            'warnings': result.get('warnings') or [], 'scientific_validation': record.get('scientific_validation') or 'NOT_PERFORMED',
            'title': record.get('title') or ' · '.join([project.get('name') or '未关联项目', '／'.join(names), name, record.get('created_at', '')[:19].replace('T', ' ')])[:500]}


def source_record(kind, key):
    record = read_json(source_path(kind, key))
    if record.get('id') != key or record.get('schema') != SOURCES[kind][1] or (kind!='comparison' and record.get('state') != 'COMPLETED'):
        raise ValueError('只能登记明确完成的对应分析结果')
    if kind=='comparison' and (set(record.get('files',[]))!={'comparison.png','comparison.svg','curves.csv'} or not record.get('wannier_attempt_id') or not record.get('band_attempt_id')):raise ValueError('叠图缺少完整输出或任务来源记录')
    if recycle.inactive(SOURCES[kind][2], key, record):
        raise ValueError('原分析或项目已回收，请先恢复原记录')
    return record


def available(project_id=None):
    projects, attempts = context()
    rows = []
    for kind, (folder, schema, recycle_kind) in SOURCES.items():
        from .history_index import paths
        for path in paths(ROOT,folder,'record.json' if kind=='comparison' else 'analysis.json'):
            try:
                record = source_record(kind, path.parent.name)
                item = summary(kind, record, projects, attempts)
                if project_id is not None and item['project_id'] != project_id:
                    continue
                key = identity(kind, record['id'])
                item.update(dataset_id=key if (directory(key) / 'record.json').is_file() else None,
                            dataset_recycled=recycle.is_recycled('analysis-datasets', key))
                rows.append(item)
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return sorted(rows, key=lambda item: item['created_at'], reverse=True)


def history_detail(kind, key):
    record = source_record(kind, key)
    projects, attempts = context()
    item = summary(kind, record, projects, attempts)
    dest = source_path(kind, key).parent
    if kind == 'v2':
        current = inside(dest, 'presentations/current.json')
        record['presentation'] = read_json(current) if current.is_file() else None
        record['presentation_supported'] = False
        for output in record.get('outputs') or []:
            if output.get('available'):
                path = inside(dest / 'outputs', output['path'])
                reason = ('本机文件缺失' if not path.is_file() else
                          '超过 10 MiB，保留原位置' if path.stat().st_size > MAX_BYTES else '')
                if reason:
                    output.update(available=False, unavailable_reason=reason)
    item['analysis'] = record
    outputs = (record.get('files') if kind=='comparison' else record.get('outputs')) or []
    item['files'] = ([{'path': output['path'], 'media_type': output['media_type'], 'available': output.get('available', False),
                      'reason': output.get('unavailable_reason', '')} for output in outputs]
                     if kind == 'v2' else [{'path': name, 'media_type': mimetypes.guess_type(name)[0] or 'text/plain',
                                           'available': inside(dest, name).is_file()} for name in outputs])
    return item


def history_output(kind, key, name):
    record = source_record(kind, key)
    if name == 'analysis.json':
        return source_path(kind, key), 'application/json'
    if name not in (record.get('files',[]) if kind=='comparison' else record.get('outputs', [])):
        raise ValueError('文件未在分析输出清单中声明')
    path = inside(source_path(kind, key).parent, name)
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('本机文件缺失或超过 10 MiB')
    return path, mimetypes.guess_type(name)[0] or 'text/plain'


def metadata(title, note):
    if not isinstance(title, str) or not title.strip() or len(title) > 500:
        raise ValueError('名称不能为空且不能超过 500 字')
    if not isinstance(note, str) or len(note) > 10000:
        raise ValueError('备注不能超过 10000 字')
    return {'title': title.strip(), 'note': note}


def copy_file(source, target, name, media_type, files):
    source = inside(source.parent, source.name)
    if not source.is_file():
        files.append({'path': name, 'media_type': media_type, 'available': False, 'reason': '本机文件缺失'})
        return False
    if source.stat().st_size > MAX_BYTES:
        files.append({'path': name, 'media_type': media_type, 'available': False, 'reason': '超过 10 MiB，保留原位置'})
        return False
    before = source.stat()
    with source.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or len(raw) > MAX_BYTES:
        raise ValueError('登记期间结果文件变化，请刷新后重新登记')
    dest = inside(target / 'files', name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    files.append({'path': name, 'media_type': media_type, 'available': True,
                  'size_bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    return True


def register(kind, source_id, title=None, note=''):
    key = identity(kind, source_id)
    base = ROOT / 'analysis-datasets'
    base.mkdir(parents=True, exist_ok=True)
    with FileLock(str(base / (key + '.lock')), timeout=10):
        dest = directory(key)
        if (dest / 'record.json').is_file():
            recycle.reactivate('analysis-datasets', key)
            return {**get(key), 'duplicate': True}
        original = source_path(kind, source_id)
        record = source_record(kind, source_id)
        with original.open('rb') as stream:
            raw_before = stream.read(MAX_BYTES + 1)
        if len(raw_before) > MAX_BYTES or json.loads(raw_before) != record:
            raise ValueError('登记期间原分析记录变化，请刷新后重新登记')
        projects, attempts = context()
        item = summary(kind, record, projects, attempts)
        item.update(metadata(title if title is not None else item['title'], note))
        snapshot = copy.deepcopy(record)
        if kind=='comparison':
            attempt=attempts.get(record['wannier_attempt_id']) or {}
            bundle_id=attempt.get('bundle_id');bundle={}
            if isinstance(bundle_id,str) and re.fullmatch(r'[a-f0-9]{64}',bundle_id):
                path=ROOT/'wannier-bundles'/(bundle_id+'.json')
                if path.is_file() and path.stat().st_size<=MAX_BYTES:
                    raw=path.read_bytes()
                    if hashlib.sha256(raw).hexdigest()==bundle_id:bundle=json.loads(raw)
            item['comparison_context']={k:record.get(k,bundle.get(k)) for k in ('absolute_windows','relative_windows','energy_reference','input_reason','win_sha256')}
            if bundle.get('win') and bundle.get('energy_reference',{}).get('fermi_eV') is not None:
                from .wannier_execution import window_metadata
                item['comparison_context'].update(window_metadata(bundle))
            item['comparison_context']['window_record_available']=bool(item['comparison_context']['win_sha256'])
        temporary = Path(tempfile.mkdtemp(prefix=key + '.', dir=base))
        files = []
        try:
            copy_file(original, temporary, 'analysis.json', 'application/json', files)
            if kind == 'v2':
                if not isinstance(record.get('result'), dict) or record['result'].get('status') != 'COMPLETED':
                    raise ValueError('分析缺少完整的完成结果，无法登记')
                copied = copy_file(inside(original.parent / 'outputs', 'result.json'), temporary, 'result.json', 'application/json', files)
                if not copied:
                    raise ValueError('分析缺少本机结果 JSON，无法登记')
                if read_json(temporary / 'files/result.json') != record['result']:
                    raise ValueError('分析记录与本机结果 JSON 不一致，请核查原结果')
                for output in snapshot.get('outputs', []):
                    if output['path'] == 'result.json':
                        continue
                    if output.get('available'):
                        if output['path'] == 'analysis.json' or output['path'].startswith('presentation/'):
                            raise ValueError('结果文件与登记来源或展示文件重名')
                        output['available'] = copy_file(inside(original.parent / 'outputs', output['path']), temporary,
                                                        output['path'], output['media_type'], files)
                        if not output['available']:
                            output['unavailable_reason'] = files[-1]['reason']
                    else:
                        files.append({'path': output['path'], 'available': False, 'media_type': output['media_type'], 'reason': '保留在服务器'})
                current = original.parent / 'presentations/current.json'
                view = read_json(current) if current.is_file() else None
                snapshot.update(presentation=None, presentation_supported=False)
                if view and view.get('status') == 'READY':
                    version = view['id']
                    if not re.fullmatch(r'[a-f0-9]{32}', version):
                        raise ValueError('展示版本标识无效')
                    complete = True
                    for asset in view['manifest']['assets']:
                        complete = copy_file(inside(original.parent / 'presentations' / version / 'outputs', asset['path']),
                                             temporary, 'presentation/' + asset['path'], asset['media_type'], files) and complete
                    if complete:
                        snapshot['presentation'] = view
                        state_path = inside(original.parent, 'presentations/' + version + '/state.json')
                        state = read_json(state_path) if state_path.is_file() else {'revision': 0, 'value': {}}
                        atomic_json(temporary / 'state.json', state)
                    else:
                        item['warnings'] = [*item['warnings'], '交互展示文件不完整；已保留原始分析结果']
            else:
                for name in (record.get('files') if kind=='comparison' else record.get('outputs')) or []:
                    if name == 'analysis.json':
                        continue
                    copy_file(inside(original.parent, name), temporary, name, mimetypes.guess_type(name)[0] or 'text/plain', files)
                if not any(file['available'] and file['path'] != 'analysis.json' for file in files):
                    raise ValueError('原分析没有可登记的本机结果文件')
            if kind=='comparison' and any(not f['available'] for f in files):raise ValueError('叠图本机附件缺失或超限，无法完整登记；原记录保留')
            if original.read_bytes() != raw_before:
                raise ValueError('登记期间原分析记录变化，请刷新后重新登记')
            item.update(id=key, schema='fpilot.analysis-dataset.v1', registered_at=now(),
                        analysis=snapshot, files=files, source_record_sha256=hashlib.sha256(raw_before).hexdigest())
            atomic_json(temporary / 'record.json', item)
            os.replace(temporary, dest)
            recycle.reactivate('analysis-datasets', key)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    return {**get(key), 'duplicate': False}


def get(key):
    if recycle.is_recycled('analysis-datasets', key) or recycle.purged('analysis-datasets', key):
        raise ValueError('数据集已移入回收区或清理')
    item = read_json(directory(key) / 'record.json')
    if item.get('id') != key or identity(item['source_kind'], item['source_id']) != key:
        raise ValueError('数据集身份校验失败')
    original = source_path(item['source_kind'], item['source_id'])
    item['source_deleted'] = not original.is_file() or recycle.inactive(SOURCES[item['source_kind']][2], item['source_id'], item)
    item['source_deleted'] = item['source_deleted'] or any(recycle.inactive('attempts', source_id) for source_id in item.get('source_attempt_ids', []))
    return item


def list_records(project_id=None):
    rows = []
    for path in (ROOT / 'analysis-datasets').glob('*/record.json'):
        try:
            item = get(path.parent.name)
            if project_id is None or item.get('project_id') == project_id:
                rows.append({key: value for key, value in item.items() if key not in ('analysis', 'files')})
        except (OSError, ValueError, KeyError):
            continue
    return sorted(rows, key=lambda item: item['registered_at'], reverse=True)


def update(key, title, note):
    dest = directory(key)
    with FileLock(str(dest.parent / (key + '.lock')), timeout=10):
        item = get(key)
        item.pop('source_deleted', None)
        item.update(metadata(title, note))
        atomic_json(dest / 'record.json', item)
    return get(key)


def output(key, name):
    record = get(key)
    entry = next((file for file in record['files'] if file['path'] == name and file['available']), None)
    if not entry:
        raise ValueError('文件未登记或本机文件不可用')
    path = inside(directory(key) / 'files', name)
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != entry['sha256']:
        raise ValueError('登记文件身份已变化')
    return path, entry['media_type']


def presentation(key, version):
    view = get(key)['analysis'].get('presentation')
    if not view or view['id'] != version or view['status'] != 'READY':
        raise ValueError('登记展示版本不存在')
    return view


def view_state(key, version, value=None):
    presentation(key, version)
    dest = directory(key)
    with FileLock(str(dest / 'state.lock'), timeout=10):
        old = read_json(dest / 'state.json') if (dest / 'state.json').exists() else {'revision': 0, 'value': {}}
        if value is None:
            return old
        if not isinstance(value, dict) or len(json.dumps(value, allow_nan=False).encode()) > 65536:
            raise ValueError('展示设置须为 64 KiB 内的有限 JSON 对象')
        updated = {'revision': old['revision'] + 1, 'saved_at': now(), 'value': value}
        atomic_json(dest / 'states' / (str(updated['revision']) + '.json'), updated)
        atomic_json(dest / 'state.json', updated)
        return updated

"""Read-only logical references to recorded files. Resolution never contacts SSH."""
import hashlib
import json
import mimetypes
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath

from . import artifacts, execution as ex, extension_protocol, extension_runs, file_lifecycle, recycle

SCHEMA = 'fpilot.artifact-ref.v1'


def _producer(project_id, kind, key):
    if not project_id or not isinstance(key, str) or not re.fullmatch(r'[\w-]{1,128}', key):
        raise ValueError('项目或产物生产者 ID 无效')
    if kind == 'attempt':
        record = ex.get(key)
    elif kind == 'analysis':
        record = extension_runs.get(key)
    else:
        raise ValueError('产物生产者类型无效')
    if record.get('project_id') != project_id:
        raise ValueError('产物不属于当前项目')
    return record


def _item(record, kind, fact, origin, locations, verification):
    name = artifacts._relative(fact['path'])
    media = fact.get('media_type') or mimetypes.guess_type(name)[0] or 'application/octet-stream'
    data_type = 'vasp.structure' if PurePosixPath(name).name in ('POSCAR', 'CONTCAR') else media
    version = {k: fact.get(k) for k in ('size_bytes', 'modified_at', 'mtime_ns')}
    # Hash location metadata only, without reading or hashing the file's contents.
    version['origin'] = hashlib.sha256(json.dumps(origin, sort_keys=True).encode()).hexdigest()
    reference = {'schema': SCHEMA, 'project_id': record['project_id'],
                 'producer_kind': kind, 'producer_id': record['id'], 'artifact_id': name,
                 'batch_id': record.get('scan_batch_id'), 'case_id': record.get('scan_case_id'),
                 'data_type': data_type, 'format_version': None, 'version': version}
    return {'reference': reference, 'origin_kind': origin['kind'], 'label': fact.get('label') or name, 'media_type': media,
            'locations': locations, 'verification_status': verification,
            'requires_verification': True}


def catalog(project_id, kind, key):
    return catalog_record(_producer(project_id, kind, key), kind)


def catalog_record(record, kind):
    owner_kind = 'attempts' if kind == 'attempt' else 'analyses'
    result = {'schema': 'fpilot.artifact-catalog.v1', 'project_id': record['project_id'],
              'producer_kind': kind, 'producer_id': record['id'], 'generated_at': ex.now(),
              'remote_checked': False, 'items': [], 'observed_at': None,
              'verification_status': 'UNOBSERVED', 'warnings': []}
    if recycle.inactive(owner_kind, record['id'], record):
        result['warnings'].append('生产者已回收，产物引用暂不可用')
        return result
    cleaned = file_lifecycle.remote_is_cleaned(owner_kind, record['id'])
    if kind == 'attempt':
        if cleaned:
            result['warnings'].append('远端运行目录已清理')
            return result
        root = ex.attempt_path(record)['shell']
        alias = record['config']['alias']
        listing = artifacts.listing(record['id'])
        result.update(observed_at=listing.get('refreshed_at'),
                      verification_status=listing.get('verification_status', 'UNOBSERVED'))
        if listing.get('needs_refresh'):
            result['warnings'].append('文件清单尚未读取或已过期，可在结果页刷新文件列表')
        if listing.get('truncated'):
            result['warnings'].append('文件清单未完整读取，未列出的文件不能据此判为缺失')
        for fact in listing['files']:
            path = artifacts._relative(fact['path'])
            location = {'kind': 'remote', 'alias': alias, 'path': root.rstrip('/') + '/' + path}
            result['items'].append(_item(record, kind, fact, location, [location], result['verification_status']))
        return result
    result['verification_status'] = 'RECORDED'
    result['observed_at'] = record.get('last_checked_at') or record.get('updated_at')
    remote_root = record.get('remote_dir')
    for output in record.get('outputs', []):
        fact = dict(output)
        path = artifacts._relative(fact['path'])
        locations = []
        remote_fact = (record.get('remote_outputs') or {}).get(path)
        origin = None
        if remote_root and remote_fact:
            origin = {'kind': 'remote', 'alias': record['runtime']['alias'],
                      'path': remote_root.rstrip('/') + '/outputs/' + path}
            fact.update(size_bytes=remote_fact['size_bytes'], modified_at=remote_fact.get('modified_at'),mtime_ns=remote_fact.get('mtime_ns'))
            if not cleaned:
                locations.append(origin)
        try:
            local = extension_protocol.inside(extension_runs.directory(record['id']) / 'outputs', path)
            stamp = local.stat()
            if stamp.st_size == fact['size_bytes']:
                location = {'kind': 'local', 'path': str(local)}
                locations.append(location)
                if origin is None:
                    origin = location
                    fact['modified_at'] = datetime.fromtimestamp(stamp.st_mtime, timezone.utc).isoformat()
                    fact['mtime_ns'] = stamp.st_mtime_ns
        except (OSError, ValueError):
            pass
        if origin and locations:
            result['items'].append(_item(record, kind, fact, origin, locations, 'RECORDED'))
        else:
            result['warnings'].append(path + ' 的文件副本暂不可用')
    return result


def resolve(project_id, reference):
    if not isinstance(reference, dict) or reference.get('schema') != SCHEMA:
        raise ValueError('产物引用格式无效')
    if reference.get('project_id') != project_id:
        raise ValueError('产物引用不属于当前项目')
    name = artifacts._relative(reference.get('artifact_id'))
    result = catalog(project_id, reference.get('producer_kind'), reference.get('producer_id'))
    item = next((item for item in result['items'] if item['reference']['artifact_id'] == name), None)
    if item is None:
        raise ValueError('此产物当前没有可解析的副本，请核对生产者与文件清单')
    if item['reference'] != reference:
        raise ValueError('产物身份或已记录版本发生变化，请重新选择产物')
    return {**item, 'remote_checked': False, 'observed_at': result['observed_at'],
            'warnings': result['warnings']}

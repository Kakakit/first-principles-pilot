"""User-requested structure copies; provenance never becomes a scheduler edge."""
import re
import base64
import hashlib
from filelock import FileLock, Timeout
from datetime import datetime, timezone
from pathlib import PurePosixPath


def validate_origin(value, structure):
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('结构来源记录无效')
    required = ('project_id', 'node_id', 'attempt_id', 'alias', 'path', 'sha256', 'state', 'extracted_at')
    for key in required:
        if not isinstance(value.get(key), str) or not value[key] or len(value[key]) > 2000 or any(c in value[key] for c in '\0\r\n'):
            raise ValueError('结构来源字段无效：'+key)
    path = PurePosixPath(value['path'])
    if path.is_absolute() or '..' in path.parts or path.name not in ('POSCAR','CONTCAR'):
        raise ValueError('结构来源须为任务内的 POSCAR 或 CONTCAR')
    if not re.fullmatch('[a-f0-9]{64}',value['sha256']) or value['sha256'] != structure.get('source',{}).get('sha256'):
        raise ValueError('结构来源与原文件身份不一致')
    for key in ('job_id','input_note','observed_at'):
        if key in value and (not isinstance(value[key],str) or len(value[key]) > 2000):
            raise ValueError('结构来源字段无效：'+key)
    if type(value.get('cached')) is not bool or value.get('program_done') is not None and type(value['program_done']) is not bool:
        raise ValueError('结构来源观察状态无效')


def snapshot(project_id, key, path):
    from . import artifacts, execution, recycle, source_contracts
    from .branch_bindings import owner
    from .input_display import frozen_note
    record = execution.get(key)
    if record.get('project_id') != project_id:
        raise ValueError('任务不属于当前项目')
    recycle.assert_available('attempts',key,record)
    path = artifacts._relative(path)
    if PurePosixPath(path).name not in ('POSCAR','CONTCAR'):
        raise ValueError('只能将 POSCAR 或 CONTCAR 添加为结构节点')
    # Reuse the exact bounded preview/cache already used in the result file pane.
    # This does not refresh the file list, traverse directories, or submit work.
    locks=artifacts.ROOT/'artifact-cache'/'snapshot-locks';locks.mkdir(parents=True,exist_ok=True)
    try:
        with FileLock(str(locks/(hashlib.sha256((key+':'+path).encode()).hexdigest()+'.lock')),timeout=50):
            preview = artifacts.preview(key,path,'tail',262144)
    except Timeout as exc:
        raise ValueError('此结构仍在读取，请稍后重试') from exc
    if preview.get('partial') or not preview.get('structure'):
        raise ValueError('需要完整、可解析且不超过 256 KiB 的结构文件')
    structure = preview['structure']
    if preview.get('size_bytes')!=len(base64.b64decode(structure['source']['base64'],validate=True)):
        raise ValueError('读取期间文件长度发生变化，请重新读取完整结构')
    evidence = record.get('evidence') or {}
    origin = {'version':1,'project_id':project_id,'node_id':owner(record) or record['node_id'],
              'attempt_id':key,'job_id':record.get('job_id') or '', 'alias':record['config']['alias'],
              'path':path,'sha256':structure['source']['sha256'], 'input_note':frozen_note(record) or '',
              'state':record['state'],'program_done':evidence.get('vasp_timing'),
              'extracted_at':datetime.now(timezone.utc).isoformat(),
              'observed_at':artifacts.listing(key).get('refreshed_at') or '', 'cached':bool(preview.get('cached'))}
    validate_origin(origin,structure)
    warnings = []
    if record.get('state') not in ('COMPLETED','FAILED','CANCELLED'):
        warnings.append('提取时任务仍在运行或状态未确认；保存的是当前文件副本，后续文件变化不会同步到画布')
    elif not evidence.get('vasp_timing') or not source_contracts.electronic(evidence):
        warnings.append('来源任务的程序结束或电子收敛尚未证实；本次仅复制结构，原状态保留')
    return {'structure':structure,'origin':origin,'warnings':warnings,'remote_mutation':False}

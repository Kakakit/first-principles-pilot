"""Attempt-bound local analyses with explicit graph sources and local results."""
import hashlib
import json
import re
import shlex
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import artifacts, execution, remote_paths
from .recycle import is_recycled
from .storage import ROOT, validate_workspace

EXTENSION = Path(__file__).resolve().parents[1] / 'resources/extensions/lcalcpol-polarization-analysis/scripts/calc_lcalcpol.py'


def _source(attempt_id, project_id, node_id, directory, prefix):
    record = execution.get(attempt_id)
    if (record.get('project_id'), record.get('node_id'), record.get('state')) != (project_id, node_id, 'COMPLETED'):
        raise ValueError('请选择对应计算节点已完成的 attempt')
    if not (record.get('evidence') or {}).get('vasp_timing'):
        raise ValueError('来源缺少 VASP 程序完成证据')
    files = {item['path']: item for item in artifacts.listing(attempt_id)['files']}
    geometry = 'POSCAR' if 'POSCAR' in files else 'CONTCAR'
    if geometry not in files or 'OUTCAR' not in files:
        raise ValueError('来源缺少 POSCAR/CONTCAR 或 OUTCAR')
    poscar, _ = artifacts.pull(attempt_id, geometry)
    (directory / (prefix + '.POSCAR')).write_bytes(poscar)
    outcar_entry = files['OUTCAR']
    if outcar_entry['size_bytes'] <= artifacts.MAX_PULL_BYTES:
        outcar, _ = artifacts.pull(attempt_id, 'OUTCAR')
        original_hash = hashlib.sha256(outcar).hexdigest()
        extracted = False
    else:
        # Return only the few polarization records needed by the existing parser.
        base = remote_paths.shell_path(artifacts._root(record))
        command = ('set -e\ncd ' + base + '\ntest -f OUTCAR; test ! -L OUTCAR\n'
                   'printf "FPILOT_SHA="; sha256sum OUTCAR | cut -d " " -f1\n'
                   "grep -Ei 'Ionic dipole moment:|Total electronic dipole moment:' OUTCAR | tail -n 20")
        receipt = execution.ssh(record['config']['alias'], command, timeout=180)
        match = re.search(r'^FPILOT_SHA=([a-f0-9]{64})$', receipt['stdout'], re.M)
        if receipt['exit_code'] != 0 or not match:
            raise ValueError('远端 OUTCAR 极化记录提取失败')
        original_hash = match.group(1)
        outcar = receipt['stdout'][match.end():].strip().encode() + b'\n'
        if len(outcar) > artifacts.MAX_PULL_BYTES:
            raise ValueError('极化摘要超过回传上限')
        extracted = True
    (directory / (prefix + '.OUTCAR')).write_bytes(outcar)
    return {'attempt_id': attempt_id, 'host': record['config']['alias'],
            'remote_path': artifacts._root(record), 'geometry_file': geometry,
            'geometry_sha256': hashlib.sha256(poscar).hexdigest(),
            'outcar_sha256': original_hash, 'outcar_extracted': extracted,
            'local_excerpt_sha256': hashlib.sha256(outcar).hexdigest()}


def polarization(project, reference_attempt_id, target_attempt_id):
    validate_workspace({'activeId': project['id'], 'projects': [project]})
    first, second = execution.get(reference_attempt_id), execution.get(target_attempt_id)
    if reference_attempt_id == target_attempt_id or first.get('project_id') != project['id'] or second.get('project_id') != project['id']:
        raise ValueError('请选择当前项目两个不同的 attempt')
    analysis_id = uuid.uuid4().hex
    directory = ROOT / 'analyses' / analysis_id
    directory.mkdir(parents=True, exist_ok=False)
    record = {'schema':'fpilot.local-analysis.v1', 'id':analysis_id, 'project_id':project['id'],
              'node_id':None, 'kind':'polarization', 'created_at':datetime.now(timezone.utc).isoformat(),
              'state':'RUNNING', 'scientific_validation':'NOT_PERFORMED'}
    try:
        sources_receipt = {'reference': _source(reference_attempt_id, project['id'], first['node_id'], directory, 'reference'),
                           'target': _source(target_attempt_id, project['id'], second['node_id'], directory, 'target')}
        command = [sys.executable, str(EXTENSION), 'compare',
                   '--nosoc-outcar', 'reference.OUTCAR', '--nosoc-poscar', 'reference.POSCAR',
                   '--soc-outcar', 'target.OUTCAR', '--soc-poscar', 'target.POSCAR']
        result = subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=300)
        if result.returncode:
            raise ValueError('极化扩展失败：' + result.stdout[-700:] + result.stderr[-700:])
        report = json.loads(result.stdout)
        (directory / 'polarization.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        record.update(state='COMPLETED', sources=sources_receipt, outputs=['polarization.json'],
                      same_branch_status=report.get('comparison',{}).get('same_branch_delta_p',{}).get('status','INSUFFICIENT'))
    except Exception as exc:
        record.update(state='FAILED', error=str(exc)[:1000])
    (directory / 'analysis.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return record


def history(project_id, node_id=None):
    result=[]
    for path in sorted((ROOT / 'analyses').glob('*/analysis.json'), key=lambda p:p.stat().st_mtime, reverse=True):
        try:
            item=json.loads(path.read_text())
            if item.get('schema')=='fpilot.local-analysis.v1' and item.get('project_id')==project_id and (node_id is None or item.get('node_id')==node_id):
                if not is_recycled('local-analyses',item['id']):
                    result.append(item)
        except (OSError, ValueError):
            continue
    return result[:100]


def output(key, name):
    if not re.fullmatch(r'[a-f0-9]{32}', key) or name != 'polarization.json':
        raise ValueError('后处理文件标识无效')
    directory = ROOT / 'analyses' / key
    record = json.loads((directory / 'analysis.json').read_text())
    if record.get('schema') != 'fpilot.local-analysis.v1' or name not in record.get('outputs', []):
        raise ValueError('后处理文件不存在')
    return (directory / name).read_bytes()

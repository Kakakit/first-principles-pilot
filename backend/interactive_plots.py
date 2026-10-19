"""Local, attempt-bound VASP projection cache and saved interactive figures.

Remote files are read only.  The user's explicit Prepare action starts a local
download; all plotting and orbital selection after that uses local arrays.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
import uuid
import shlex
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import artifacts, execution, remote_paths
from .recycle import is_recycled
from .storage import ROOT

CACHE_ROOT = ROOT / 'projection-cache'
SAVED_ROOT = ROOT / 'plot-datasets'
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?'
ORBITAL_GROUPS = {'s': ('s',), 'p': ('px', 'py', 'pz'),
                  'd': ('dxy', 'dyz', 'dz2', 'dxz', 'dx2-y2')}
PBAND_COLUMNS = ['s', 'py', 'pz', 'px', 'dxy', 'dyz', 'dz2', 'dxz', 'dx2-y2']
VASPKIT_INPUTS = ('INCAR', 'POSCAR', 'KPOINTS', 'EIGENVAL', 'DOSCAR', 'PROCAR')


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _record(project_id: str, attempt_id: str, stage: str) -> dict:
    record = execution.get(attempt_id)
    if record.get('project_id') != project_id or record.get('state') != 'COMPLETED' or not (record.get('evidence') or {}).get('vasp_timing'):
        raise ValueError('绘图来源必须是当前项目已完成的 VASP attempt')
    manifest, _ = execution.bundle(record['bundle_id'], allow_dependencies=True)
    if manifest.get('stage') != stage:
        raise ValueError('所选 attempt 与绘图节点类型不符：需要 ' + stage)
    return record


def candidates(project_id: str) -> list[dict]:
    rows = []
    for row in execution.history(project_id):
        if row.get('state') != 'COMPLETED' or not (row.get('evidence') or {}).get('vasp_timing'):
            continue
        try:
            manifest, _ = execution.bundle(row['bundle_id'], allow_dependencies=True)
        except (KeyError, OSError, ValueError):
            continue
        if manifest.get('stage') in ('bands', 'dos'):
            rows.append({'id': row['id'], 'stage': manifest['stage'], 'host': row['config']['alias'],
                         'job_id': row.get('job_id'), 'path': row.get('remote_path_absolute'),
                         'node_id': row.get('node_id') or manifest.get('node_id'),
                         'execution_scope': row.get('execution_scope'),
                         'scf_attempt_id': (row.get('dependency_snapshot') or {}).get('upstream_attempt_id')})
    return rows


def _source(record: dict, filename: str, listing: dict | None = None) -> dict:
    listing = listing or artifacts.listing(record['id'])
    match = next((row for row in listing['files'] if row['path'] == filename and row['size_bytes'] > 0), None)
    if not match:
        raise ValueError(f'{record.get("stage_slug", "attempt")} 缺少非空 {filename}')
    return {'attempt_id': record['id'], 'host': record['config']['alias'], 'remote_dir': artifacts._root(record),
            'name': filename, 'size_bytes': match['size_bytes'], 'modified_at': match['modified_at']}


def _remote_elements(record: dict) -> list[str]:
    path = remote_paths.shell_path(artifacts._root(record)) + '/POSCAR'
    receipt = execution.ssh(record['config']['alias'], 'sed -n "6,7p" ' + path, timeout=30, stdout_limit=500)
    lines = receipt['stdout'].splitlines()
    if receipt['exit_code'] != 0 or len(lines) != 2:
        raise ValueError('无法读取远端 POSCAR 元素列表')
    symbols = lines[0].split()
    if not symbols or not all(re.fullmatch(r'[A-Z][a-z]?', item) for item in symbols):
        raise ValueError('远端 POSCAR 元素列表无效')
    return list(dict.fromkeys(symbols))


def _pband_names(elements: list[str], available: set[str]) -> tuple[str, list[str]] | None:
    patterns = (('soc', lambda element: [f'PBAND_{element}_SOC.dat']),
                ('spin2', lambda element: [f'PBAND_{element}_UP.dat', f'PBAND_{element}_DW.dat']),
                ('spin1', lambda element: [f'PBAND_{element}.dat']))
    for mode, names_for_element in patterns:
        names = [name for element in elements for name in names_for_element(element)]
        if all(name in available for name in names):
            return mode, names
    return None


def _remote_hashes(record: dict, names: tuple[str, ...]) -> dict[str, str]:
    base = remote_paths.shell_path(artifacts._root(record))
    commands = ['set -e', 'cd ' + base]
    commands += ['test -f ' + shlex.quote(name) + ' && test ! -L ' + shlex.quote(name) for name in names]
    commands += ['sha256sum -- ' + ' '.join(map(shlex.quote, names))]
    receipt = execution.ssh(record['config']['alias'], '\n'.join(commands), timeout=600, stdout_limit=4096)
    if receipt['exit_code'] != 0: raise ValueError('无法核对远端 VASPKIT 输入：' + receipt['stderr'][-300:])
    rows = receipt['stdout'].splitlines()
    if len(rows) != len(names): raise ValueError('远端 VASPKIT 输入哈希不完整')
    result = {}
    for name, row in zip(names, rows):
        match = re.fullmatch(r'([a-f0-9]{64})\s+\*?' + re.escape(name), row)
        if not match: raise ValueError('远端 VASPKIT 输入哈希格式异常')
        result[name] = match.group(1)
    return result


def _key(project_id: str, band_id: str | None, dos_id: str | None) -> str:
    return hashlib.sha256(json.dumps([project_id, band_id, dos_id], separators=(',', ':')).encode()).hexdigest()


def _metadata(path: Path) -> dict | None:
    try:
        value = json.loads((path / 'metadata.json').read_text())
        if value.get('state') == 'READY':
            return value
    except (OSError, ValueError):
        pass
    return None


def _available(meta):
    from . import recycle
    for identifier in {r['attempt_id'] for r in meta.get('sources',[]) if r.get('attempt_id')}:
        record=execution.get(identifier)
        recycle.assert_available('attempts',identifier,record)


def prepare(project_id: str, band_attempt_id: str | None = None, dos_attempt_id: str | None = None, force=False) -> dict:
    if not band_attempt_id and not dos_attempt_id:
        raise ValueError('至少选择一个已完成的能带或 DOS attempt')
    band = _record(project_id, band_attempt_id, 'bands') if band_attempt_id else None
    dos = _record(project_id, dos_attempt_id, 'dos') if dos_attempt_id else None
    for record in (band,dos):
        if record:
            from . import recycle
            recycle.assert_available('attempts',record['id'],record)
    key=_key(project_id,band_attempt_id,dos_attempt_id)
    directory=CACHE_ROOT/key
    with LOCK:
        old=JOBS.get(key)
        if old and old['state'] in ('GENERATING','DOWNLOADING','PARSING'):
            return dict(old)
        ready=_metadata(directory)
        if ready and not force:
            _available(ready)
            return {'id':key,'state':'READY','cached':True,'metadata':ready,'downloaded_bytes':0,'total_bytes':0}
        job={'id':key,'source_attempt_ids':[a['id'] for a in (band,dos) if a],
             'state':'GENERATING','downloaded_bytes':0,'total_bytes':0,'current_file':'服务器解析绘图数据','error':''}
        JOBS[key]=job
    from .analysis_queue import executor
    executor.submit(_compact_build,key,project_id,band,dos,force)
    return dict(job)


def _compact_build(key, project_id, band, dos, force):
    import time
    from . import compact_plots,remote_transport
    job=JOBS[key];directory=CACHE_ROOT/key;started=time.monotonic()
    try:
        with remote_transport.operation('projection-prepare') as scope:
            records=[(band,'band'),(dos,'dos')]
            values=remote_transport.parallel([lambda r=r,k=k:compact_plots.fetch(r,k,force) for r,k in records if r])
            result={'schema':'fpilot.interactive-plot-cache.v3','id':key,'project_id':project_id,
                    'band_attempt_id':band['id'] if band else None,'dos_attempt_id':dos['id'] if dos else None,
                    'sources':[],'state':'READY','created_at':datetime.now(timezone.utc).isoformat(),
                    'band':None,'dos':None,'generation':None}
            directory.mkdir(parents=True,exist_ok=True)
            for (record,kind),value in zip([r for r in records if r[0]],values):
                if job.get('discarded'):raise ValueError('本地绘图来源已由用户清理')
                compact_plots.install(value,directory)
                folder=directory/record['id'];folder.mkdir(exist_ok=True)
                (folder/'POSCAR').write_text(value['poscar_text'])
                for name in ('KPOINTS','EIGENVAL'):
                    if value.get(name.lower()+'_text'):(folder/name).write_text(value[name.lower()+'_text'])
                result[kind]=value['metadata'];result['sources'].extend(value['sources'])
                if value.get('remote_only'):
                    result.setdefault('remote_parts',{})[kind]={'attempt_id':record['id'],'remote_directory':value['remote_directory']}
                result['generation']=value.get('generation') or result['generation']
                job['downloaded_bytes']+=value['transferred_bytes'];job['total_bytes']+=value['transferred_bytes']
            if band and dos:
                bscf=(band.get('dependency_snapshot') or {}).get('upstream_attempt_id')
                dscf=(dos.get('dependency_snapshot') or {}).get('upstream_attempt_id')
                result['compatible']=bool(bscf and bscf==dscf)
                result['compatibility_note']='' if result['compatible'] else '能带与 DOS 未绑定同一 SCF；分别显示来源，不绘制同源合并图'
            result['prepare_seconds']=time.monotonic()-started
            result['transferred_bytes']=job['downloaded_bytes']
            result['remote_requests']=sum(e.get('ssh_requests',0) for e in scope['events'])
            result['timings']=scope['events']
            if job.get('discarded'):raise ValueError('本地绘图来源已由用户清理')
            temporary=directory/'metadata.part';temporary.write_text(json.dumps(result,ensure_ascii=False));temporary.replace(directory/'metadata.json')
            with LOCK:job.update(state='READY',metadata=result,current_file='')
    except Exception as exc:
        with LOCK:job.update(state='FAILED',error=str(exc)[:1500],current_file='')
    finally:
        if job.get('discarded'):shutil.rmtree(directory,ignore_errors=True)


def status(key: str) -> dict:
    if not re.fullmatch(r'[a-f0-9]{64}', key):
        raise ValueError('缓存 ID 无效')
    with LOCK:
        job = JOBS.get(key)
        if job:
            return dict(job)
    meta = _metadata(CACHE_ROOT / key)
    if not meta:
        raise ValueError('本机缓存不存在')
    return {'id': key, 'state': 'READY', 'metadata': meta,
            'cached':True,'downloaded_bytes':0,'total_bytes':0}


def cached(project_id: str, band_attempt_id: str | None = None, dos_attempt_id: str | None = None) -> dict:
    key = _key(project_id, band_attempt_id, dos_attempt_id)
    meta = _metadata(CACHE_ROOT / key)
    if not meta or meta.get('project_id') != project_id or meta.get('band_attempt_id') != band_attempt_id or meta.get('dos_attempt_id') != dos_attempt_id:
        return {'id': key, 'state': 'ABSENT'}
    _available(meta)
    return {'id': key, 'state': 'READY', 'metadata': meta,
            'cached':True,'downloaded_bytes':0,'total_bytes':0}


def _remote_digest(source: dict) -> str:
    path = remote_paths.shell_path(source['remote_dir']) + '/' + source['name']
    command = ('set -e\ntest -f ' + path + ' && test ! -L ' + path + '\n'
               'stat -c %s ' + path + '\nsha256sum ' + path + " | awk '{print $1}'\n")
    receipt = execution.ssh(source['host'], command, timeout=600, stdout_limit=1000)
    if receipt['exit_code'] != 0:
        detail = receipt.get('stderr', '').strip()[-300:]
        raise ValueError(f'{source["name"]} 远端连接或读取失败（exit {receipt["exit_code"]}）：{detail or "请重试连接"}')
    lines = receipt['stdout'].splitlines()
    if len(lines) != 2 or not lines[0].isdigit() or not re.fullmatch('[a-f0-9]{64}', lines[1]):
        raise ValueError(source['name'] + ' 远端哈希回执格式异常，请重新检查文件')
    if int(lines[0]) != source['size_bytes']:
        raise ValueError(f'{source["name"]} 远端大小已变化：清单 {source["size_bytes"]} 字节，当前 {lines[0]} 字节；请刷新来源')
    return lines[1]


def _download(source: dict, target: Path, job: dict) -> dict:
    if job.get('discarded'):raise ValueError('本地绘图来源已由用户清理')
    target.parent.mkdir(parents=True, exist_ok=True)
    expected = _remote_digest(source)
    if target.is_file() and target.stat().st_size == source['size_bytes'] and _sha(target) == expected:
        job['downloaded_bytes'] += source['size_bytes']
        return {**source, 'sha256': expected, 'local_path': str(target)}
    temporary = target.with_name(target.name + '.part')
    temporary.unlink(missing_ok=True)
    command = ['ssh', *execution.ssh_multiplex_options(source['host']), '-o', 'BatchMode=yes',
               '-o', 'StrictHostKeyChecking=yes', source['host'],
               'cat ' + remote_paths.shell_path(source['remote_dir']) + '/' + source['name']]
    digest = hashlib.sha256()
    count = 0
    try:
        from . import remote_transport
        with temporary.open('wb') as output:
            def receive(chunk):
                nonlocal count
                if job.get('discarded'):raise ValueError('本地绘图来源已由用户清理')
                if count+len(chunk)>source['size_bytes']:raise ValueError('远端文件超过已核对大小')
                output.write(chunk);digest.update(chunk);count+=len(chunk)
                with LOCK:job['downloaded_bytes']+=len(chunk)
            receipt=remote_transport.legacy(command,timeout=900,stdout_limit=0,lane='analysis',on_output=receive)
        if receipt['exit_code'] != 0 or count != source['size_bytes'] or digest.hexdigest() != expected:
            raise ValueError('下载或哈希核对失败：'+source['name']+' '+receipt['stderr'][-1000:])
        if _remote_digest(source) != expected:
            raise ValueError('下载期间远端文件发生变化：' + source['name'])
        if job.get('discarded'):raise ValueError('本地绘图来源已由用户清理')
        temporary.replace(target)
        return {**source, 'sha256': expected, 'local_path': str(target)}
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise



def _cleanup_remote_pbands(host: str, scratch: str) -> None:
    from . import servers
    prefix=servers.scratch_root(host,'pbands')+'/'
    if not scratch.startswith(prefix) or not re.fullmatch('[a-f0-9]{32}',scratch[len(prefix):]):
        raise ValueError('远端临时目录标识异常，拒绝清理')
    receipt = execution.ssh(host, 'rm -rf -- ' + remote_paths.shell_path(scratch), timeout=120)
    if receipt['exit_code'] != 0:
        raise ValueError('远端临时 PBAND 目录清理失败：' + receipt['stderr'][-300:])


def _generate_pbands(record: dict, elements: list[str]) -> tuple[str, str, list[dict], dict]:
    host = record['config']['alias']
    source = remote_paths.shell_path(artifacts._root(record))
    from . import servers
    scratch = servers.scratch_root(host,'pbands')+'/' + uuid.uuid4().hex
    target = remote_paths.shell_path(scratch)
    tool_receipt = execution.ssh(host, 'command -v vaspkit', timeout=30, stdout_limit=500)
    tool = tool_receipt['stdout'].strip().splitlines()[-1] if tool_receipt['stdout'].strip() else ''
    if tool_receipt['exit_code'] != 0 or not tool.startswith('/') or '\n' in tool:
        raise ValueError('目标服务器未找到可用的 VASPKIT；未下载 PROCAR')
    before = _remote_hashes(record, VASPKIT_INPUTS)
    command = '\n'.join([
        'set -eu', 'umask 077',
        'mkdir -p -- ' + remote_paths.shell_path(servers.scratch_root(host,'pbands')),
        'test ! -e ' + target,
        'mkdir -- ' + target,
        'source_dir=' + source, 'scratch_dir=' + target,
        'required=0',
        'for name in ' + ' '.join(VASPKIT_INPUTS) + '; do required=$((required + $(stat -c %s -- "$source_dir/$name"))); done',
        "free=$(df -Pk \"$scratch_dir\" | awk 'NR==2 {printf \"%.0f\", $4*1024}')",
        'test "$free" -gt $((required + 268435456)) || { echo "远端空间不足，无法隔离生成 PBAND" >&2; exit 1; }',
        'for name in ' + ' '.join(VASPKIT_INPUTS) + '; do cp -- "$source_dir/$name" "$scratch_dir/$name"; done',
        'cd "$scratch_dir"',
        'if ' + shlex.quote(tool) + ' -task 213 >vaspkit.log 2>&1; then :; else',
        "  grep -Eiq '(unknown|unrecognized|invalid|illegal) (option|argument).*[-]task' vaspkit.log || { tail -30 vaspkit.log >&2; exit 1; }",
        '  rm -f -- PBAND_*.dat KLABELS KLINES.dat',
        "  printf '213\\n' | " + shlex.quote(tool) + ' >vaspkit.log 2>&1 || { tail -30 vaspkit.log >&2; exit 1; }',
        'fi',
        "find . -maxdepth 1 -type f -name 'PBAND_*.dat' -printf '%f|%s|%T@\\n' | sort",
    ])
    try:
        receipt = execution.ssh(host, command, timeout=1200, stdout_limit=12000)
        if receipt['exit_code'] != 0:
            raise ValueError('远端 VASPKIT 213 失败：' + receipt['stderr'][-500:])
        entries = {}
        for line in receipt['stdout'].splitlines():
            match = re.fullmatch(r'(PBAND_[A-Za-z0-9_]+\.dat)\|(\d+)\|([\d.]+)', line)
            if match and int(match.group(2)) > 0:
                entries[match.group(1)] = (int(match.group(2)), match.group(3))
        found = _pband_names(elements, set(entries))
        if not found: raise ValueError('VASPKIT 213 未生成完整的逐元素 PBAND 文件组')
        mode, names = found
        after = _remote_hashes(record, VASPKIT_INPUTS)
        if after != before: raise ValueError('生成期间原始输入发生变化，丢弃 PBAND')
        sources = [{'attempt_id': record['id'], 'host': host, 'remote_dir': scratch,
                    'name': name, 'size_bytes': entries[name][0], 'modified_at': entries[name][1]}
                   for name in names]
        return scratch, mode, sources, {'tool': tool, 'task': 213, 'input_sha256': before}
    except BaseException:
        _cleanup_remote_pbands(host, scratch)
        raise


def _species(poscar: Path) -> list[str]:
    lines = poscar.read_text(errors='replace').splitlines()
    symbols = lines[5].split()
    counts = [int(x) for x in lines[6].split()]
    if len(symbols) != len(counts) or not all(re.fullmatch('[A-Z][a-z]?', s) for s in symbols):
        raise ValueError('POSCAR 缺少可用的元素与原子数行')
    return [symbol for symbol, count in zip(symbols, counts) for _ in range(count)]


def _reciprocal_lattice(poscar: Path) -> np.ndarray:
    lines = poscar.read_text(errors='replace').splitlines()
    scale = float(lines[1].split()[0])
    if scale <= 0: raise ValueError('POSCAR 晶格比例无效，无法计算能带路径距离')
    lattice = np.array([[float(value) for value in lines[index].split()[:3]] for index in (2,3,4)], dtype=np.float64) * scale
    return 2 * np.pi * np.linalg.inv(lattice).T


def _outcar_info(outcar: Path) -> dict:
    found={};noncollinear=False;digest=hashlib.sha256()
    patterns={'precise':r'Fermi energy:\s*('+NUMBER+')','short':r'E-fermi\s*:\s*('+NUMBER+')',
              'vbm_eV':r'val\. band max:\s*('+NUMBER+')','cbm_eV':r'cond\. band min:\s*('+NUMBER+')'}
    with outcar.open('rb') as stream:
        for raw in stream:
            digest.update(raw);line=raw.decode(errors='replace')
            noncollinear=noncollinear or bool(re.search(r'\b(?:LSORBIT|LNONCOLLINEAR)\s*=\s*T\b',line,re.I))
            for name,pattern in patterns.items():
                values=re.findall(pattern,line)
                if values:found[name]=float(values[-1])
    result=None
    if 'precise' in found or 'short' in found:
        result={'fermi_eV':found.get('precise',found.get('short')),
                'fermi_source':'Fermi energy' if 'precise' in found else 'E-fermi',
                'vbm_eV':found.get('vbm_eV'),'cbm_eV':found.get('cbm_eV')}
        if result['vbm_eV'] is None or result['cbm_eV'] is None or result['cbm_eV']<=result['vbm_eV']:
            result['vbm_eV']=result['cbm_eV']=None
    return {'reference':result,'noncollinear':noncollinear,'sha256':digest.hexdigest()}


def _fermi(outcar: Path) -> dict:
    result=_outcar_info(outcar)['reference']
    if result is None:raise ValueError('OUTCAR 未找到费米能')
    return result


def energy_reference(project_id: str, attempt_id: str, force=False) -> dict:
    record = _record(project_id, attempt_id, 'vasp-wannier')
    from .remote_energy import reference
    return reference(record,force)


def _canonical(name: str) -> str:
    low = name.lower().replace('x2-y2', 'dx2-y2')
    return {'dx2': 'dx2-y2', 'x2-y2': 'dx2-y2', 'z2': 'dz2'}.get(low, low)


def _doscar(path: Path, species: list[str], target: Path) -> dict:
    with path.open(errors='replace') as stream:
        for _ in range(5): next(stream)
        header = next(stream).split()
        nedos, fermi = int(float(header[2])), float(header[3])
        total = np.array([[float(x) for x in next(stream).split()] for _ in range(nedos)], dtype=np.float32)
        elements = list(dict.fromkeys(species))
        projected = None; labels = []; spin = total.shape[1] == 5; noncollinear = False
        first_header = next((line for line in stream if line.strip()), None)
        for atom, symbol in enumerate(species):
            line = first_header if atom == 0 else next((line for line in stream if line.strip()), None)
            if line is None:
                if atom == 0:
                    elements = []
                    projected = np.zeros((2 if spin else 1, 0, 0, nedos), dtype=np.float32)
                    break
                raise ValueError('DOSCAR 原子投影块不足')
            rows = np.array([[float(x) for x in next(stream).split()] for _ in range(nedos)], dtype=np.float32)
            if rows.shape[1] < 4: raise ValueError('DOSCAR 缺少轨道分辨投影')
            columns = rows.shape[1] - 1
            basis = columns // 4 if columns % 4 == 0 and columns // 4 in (3, 9, 16) else columns // 2 if spin else columns
            catalog = {3: ['s','p','d'], 9: ['s','py','pz','px','dxy','dyz','dz2','dxz','dx2-y2'],
                       16: ['s','py','pz','px','dxy','dyz','dz2','dxz','dx2-y2'] + ['f'+str(i) for i in range(1,8)]}
            if basis not in catalog: raise ValueError('DOSCAR 投影列数不受支持：' + str(columns))
            if projected is None:
                labels = catalog[basis]
                noncollinear = columns == basis * 4
                projected = np.zeros((2 if spin else 1, len(elements), basis, nedos), dtype=np.float32)
            if noncollinear:
                projected[0, elements.index(symbol)] += rows[:, 1::4].T[:basis]
            elif spin:
                projected[0, elements.index(symbol)] += rows[:, 1::2].T[:basis]
                projected[1, elements.index(symbol)] += rows[:, 2::2].T[:basis]
            else:
                projected[0, elements.index(symbol)] += rows[:, 1:].T[:basis]
    np.save(target / 'dos_energy.npy', total[:, 0])
    np.save(target / 'dos_total.npy', total[:, 1:3].T if spin else total[:, 1:2].T)
    np.save(target / 'dos_projected.npy', projected)
    return {'elements': elements, 'orbitals': labels, 'spins': 2 if spin else 1,
            'noncollinear': noncollinear, 'doscar_fermi_eV': fermi,
            'projection_note': '' if labels else '该 DOSCAR 只有总态密度，没有逐原子轨道投影；可看总 DOS，PDOS 需选择输出投影的 DOS attempt'}


def _procar(path: Path, species: list[str], target: Path, noncollinear: bool) -> dict:
    dimensions = re.compile(r'# of k-points:\s*(\d+)\s*# of bands:\s*(\d+)\s*# of ions:\s*(\d+)')
    kline = re.compile(r'k-point\s+(\d+)\s*:\s*(.*?)\s+weight\s*=', re.I)
    bandline = re.compile(r'band\s+(\d+).*energy\s+(' + NUMBER + r')', re.I)
    elements = list(dict.fromkeys(species)); labels = []
    nk = nb = 0; weights = energies = kcoords = None
    spin = 0; k = band = -1; table = 0; seen_k = 0; explicit_spin = False
    with path.open(errors='replace') as stream:
        for raw in stream:
            line = raw.strip()
            if not nk:
                match = dimensions.search(line)
                if match:
                    nk, nb, ni = map(int, match.groups())
                    if ni != len(species): raise ValueError('PROCAR 原子数与 POSCAR 不一致')
                    energies = np.lib.format.open_memmap(target / 'band_energy.npy', mode='w+', dtype='float32', shape=(2,nk,nb))
                    energies[:] = np.nan
                    kcoords = np.zeros((nk,3), dtype=np.float32)
                continue
            match = re.match(r'spin component\s+(\d+)', line, re.I)
            if match:
                spin = int(match.group(1)) - 1; explicit_spin = True; k = band = -1; continue
            match = kline.match(line)
            if match:
                next_k = int(match.group(1)) - 1
                coords = re.findall(NUMBER, match.group(2))
                if len(coords) != 3: raise ValueError('PROCAR k 点坐标格式无法识别：' + line[:100])
                if not explicit_spin and next_k == 0 and seen_k >= nk: spin += 1
                if spin > 1: break
                k = next_k; seen_k += 1; band = -1
                if spin == 0: kcoords[k] = [float(value) for value in coords]
                continue
            match = bandline.match(line)
            if match and k >= 0:
                band = int(match.group(1)) - 1; table = 0
                if 0 <= band < nb: energies[spin,k,band] = float(match.group(2))
                continue
            if line.startswith('ion ') and band >= 0:
                names = [_canonical(x) for x in line.split()[1:] if x.lower() != 'tot']
                if not labels:
                    labels = names
                    weights = np.lib.format.open_memmap(target / 'band_weight.npy', mode='w+', dtype='float32',
                                                        shape=(2,nk,nb,len(elements),len(labels)))
                    weights[:] = 0
                if names != labels: raise ValueError('PROCAR 轨道表头不一致')
                table += 1; continue
            fields = line.split()
            if weights is not None and table == 1 and fields and fields[0].isdigit() and 0 <= band < nb:
                ion = int(fields[0]) - 1
                if 0 <= ion < len(species):
                    weights[spin,k,band,elements.index(species[ion]),:] += [float(x) for x in fields[1:1+len(labels)]]
    if weights is None or not np.isfinite(energies[0]).all(): raise ValueError('PROCAR 能带投影不完整')
    found_spins = 2 if np.isfinite(energies[1]).any() and not noncollinear else 1
    if found_spins == 2 and not np.isfinite(energies[1]).all(): raise ValueError('PROCAR 第二自旋通道不完整')
    if noncollinear and found_spins != 1: raise ValueError('非共线 PROCAR 自旋识别异常')
    np.save(target / 'band_kcoords.npy', kcoords)
    del energies, weights
    return {'elements': elements, 'orbitals': labels, 'spins': found_spins, 'nk': nk, 'nb': nb,
            'projection_component': 'charge' if noncollinear else 'collinear-or-nonspin'}


def _eigenval(path: Path, target: Path) -> tuple[int, int, int]:
    with path.open(errors='replace') as stream:
        first = next(stream).split()
        if len(first) < 4 or first[3] not in ('1', '2'):
            raise ValueError('EIGENVAL 自旋通道标识无效')
        spins = int(first[3])
        for _ in range(4): next(stream)
        dimensions = next(stream).split()
        if len(dimensions) < 3: raise ValueError('EIGENVAL 缺少 k 点与能带数')
        nk, nb = int(dimensions[1]), int(dimensions[2])
        if nk <= 0 or nb <= 0: raise ValueError('EIGENVAL k 点或能带数无效')
        energies = np.lib.format.open_memmap(target/'band_energy.npy', mode='w+', dtype='float32', shape=(spins,nk,nb))
        coords = np.empty((nk,3),dtype=np.float32)
        for k in range(nk):
            line = next(stream).strip()
            while not line: line = next(stream).strip()
            fields = line.split()
            if len(fields) < 4: raise ValueError('EIGENVAL k 点行不完整')
            coords[k] = [float(value) for value in fields[:3]]
            for b in range(nb):
                fields = next(stream).split()
                if len(fields) < (3 if spins == 2 else 2) or int(fields[0]) != b+1:
                    raise ValueError('EIGENVAL 能带行不完整')
                energies[:,k,b] = [float(value) for value in fields[1:1+spins]]
    np.save(target/'band_kcoords.npy',coords)
    del energies
    return spins,nk,nb


def _pband(files: list[Path], elements: list[str], mode: str, eigenval: Path, target: Path) -> dict:
    spins,nk,nb = _eigenval(eigenval,target)
    if spins != (2 if mode == 'spin2' else 1):
        raise ValueError('PBAND 通道类型与 EIGENVAL 自旋数不一致')
    energies = np.load(target/'band_energy.npy',mmap_mode='r')
    weights = np.lib.format.open_memmap(target/'band_weight.npy',mode='w+',dtype='float32',
                                        shape=(spins,nk,nb,len(elements),len(PBAND_COLUMNS)))
    weights[:] = 0
    shifts = []
    expected_names = _pband_names(elements,{path.name for path in files})
    if not expected_names or expected_names[0] != mode or set(expected_names[1]) != {path.name for path in files}:
        raise ValueError('PBAND 文件组与元素或自旋通道不一致')
    for path in files:
        match = re.fullmatch(r'PBAND_([A-Z][a-z]?)(?:_(UP|DW|SOC))?\.dat',path.name)
        if not match or match.group(1) not in elements: raise ValueError('PBAND 文件名无效')
        element, suffix = match.groups()
        spin = 1 if suffix == 'DW' else 0
        with path.open(errors='replace') as stream:
            header = next(stream).split()
            names = [_canonical(name) for name in header[2:]]
            if names != PBAND_COLUMNS+['tot']:
                raise ValueError('PBAND 轨道列不完整或顺序异常：' + path.name)
            current_band = -1; count = 0; shift = None
            for raw in stream:
                line = raw.strip()
                if not line: continue
                dimensions = re.match(r'#\s*NKPTS\s*&\s*NBANDS:\s*(\d+)\s+(\d+)',line,re.I)
                if dimensions:
                    if (int(dimensions.group(1)),int(dimensions.group(2))) != (nk,nb):
                        raise ValueError('PBAND 与 EIGENVAL 维度不一致：' + path.name)
                    continue
                band = re.match(r'#\s*Band-Index\s+(\d+)',line,re.I)
                if band:
                    next_band = int(band.group(1))-1
                    if next_band != current_band+1 or (current_band >= 0 and count != nk) or next_band >= nb:
                        raise ValueError('PBAND 能带编号或 k 点不连续')
                    current_band = next_band; count = 0
                    continue
                if line.startswith('#'): continue
                fields = line.split()
                if current_band < 0 or count >= nk or len(fields) != 12:
                    raise ValueError('PBAND 数值行不完整：' + path.name)
                x,energy = float(fields[0]),float(fields[1])
                values = [float(value) for value in fields[2:11]]
                if not math.isfinite(x) or not math.isfinite(energy) or not all(math.isfinite(v) for v in values):
                    raise ValueError('PBAND 含无效数值')
                k_index = count if current_band % 2 == 0 else nk-1-count
                delta = float(energies[spin,k_index,current_band]) - energy
                if shift is None: shift = delta
                elif abs(delta-shift) > .002: raise ValueError('PBAND 能量与 EIGENVAL 无法对齐：' + path.name)
                weights[spin,k_index,current_band,elements.index(element),:] = values
                count += 1
            if current_band != nb-1 or count != nk:
                raise ValueError('PBAND 缺少完整能带或 k 点：' + path.name)
            shifts.append(shift)
    if max(shifts)-min(shifts) > .002: raise ValueError('PBAND 文件组使用了不同能量零点')
    del weights
    return {'elements':elements,'orbitals':PBAND_COLUMNS,'spins':spins,'nk':nk,'nb':nb,
            'projection_component':'charge' if mode == 'soc' else 'collinear-or-nonspin',
            'source_format':'vaspkit-pband','pband_energy_offset_eV':float(np.median(shifts))}


def _is_noncollinear(outcar: Path) -> bool:
    return _outcar_info(outcar)['noncollinear']


def _unsupported_vaspkit_cli(log: Path) -> bool:
    with log.open('rb') as stream:
        stream.seek(max(0,log.stat().st_size-8192));tail=stream.read().decode(errors='replace')
    return bool(re.search(r'(unknown|unrecognized|invalid|illegal)\s+(option|argument)[^\n]{0,80}-task',tail,re.I))


def _kpath_labels(kpoints: Path, nk: int) -> list[dict]:
    lines = kpoints.read_text(errors='replace').splitlines()
    if len(lines) < 6 or 'line' not in lines[2].lower():
        return []
    try: per_segment = int(lines[1].strip())
    except ValueError: return []
    endpoints = []
    for line in lines[4:]:
        if '!' not in line: continue
        label = line.split('!', 1)[1].strip().split()[0]
        if label: endpoints.append(label.replace('GAMMA', 'Γ').replace('Gamma', 'Γ'))
    if per_segment < 2 or len(endpoints) < 2 or len(endpoints) % 2 or nk != per_segment * (len(endpoints)//2):
        return []
    result=[]
    for segment in range(len(endpoints)//2):
        for index, label in ((segment*per_segment,endpoints[2*segment]),
                             ((segment+1)*per_segment-1,endpoints[2*segment+1])):
            if result and result[-1]['index'] == index: result[-1]['label'] += '|' + label
            else: result.append({'index':index,'label':label})
    return result


def _build(key: str, project_id: str, band: dict | None, dos: dict | None, sources: list[dict],
           generate: bool, band_mode: str | None, elements: list[str]) -> None:
    job = JOBS[key]; directory = CACHE_ROOT / key
    scratch = None
    try:
        if job.get('discarded'):return
        directory.mkdir(parents=True, exist_ok=True)
        generation = None
        if band and generate:
            with LOCK: job['current_file'] = '远端 VASPKIT 213 生成逐元素 PBAND'
            scratch, band_mode, generated, generation = _generate_pbands(band, elements)
            required_bytes = sum(item['size_bytes'] for item in sources + generated)
            available_bytes = shutil.disk_usage(CACHE_ROOT.parent).free
            if available_bytes < required_bytes * 1.25 + 256 * 1024 * 1024:
                raise ValueError(f'本机空间不足：绘图源文件 {required_bytes} 字节，可用 {available_bytes} 字节')
            sources.extend(generated)
            with LOCK:
                job['total_bytes'] += sum(item['size_bytes'] for item in generated)
                job['available_bytes'] = available_bytes
                job['state'] = 'DOWNLOADING'
        copied = []
        for item in sources:
            with LOCK: job['current_file'] = item['name'] + ' · ' + item['attempt_id'][:8]
            copied.append(_download(item, directory / item['attempt_id'] / item['name'], job))
        with LOCK: job['state'] = 'PARSING'; job['current_file'] = '解析本机轨道数据'
        result = {'schema': 'fpilot.interactive-plot-cache.v2', 'id': key, 'project_id': project_id,
                  'band_attempt_id': band['id'] if band else None, 'dos_attempt_id': dos['id'] if dos else None,
                  'sources': copied, 'state': 'READY', 'created_at': datetime.now(timezone.utc).isoformat(),
                  'band': None, 'dos': None, 'generation': generation}
        if band:
            folder = directory / band['id']
            if (band_mode == 'soc') != _is_noncollinear(folder/'OUTCAR'):
                raise ValueError('PBAND 通道与 OUTCAR 的 SOC/非共线模式不一致')
            files = [folder/item['name'] for item in sources if item['attempt_id'] == band['id'] and item['name'].startswith('PBAND_')]
            result['band'] = _pband(files, elements, band_mode, folder/'EIGENVAL', directory)
            scf = (band.get('dependency_snapshot') or {}).get('upstream_attempt_id')
            reference = _fermi(folder/'OUTCAR')
            if scf:
                scf_size = 0; before_bytes = job['downloaded_bytes']
                try:
                    scf_record = execution.get(scf)
                    scf_source = _source(scf_record, 'OUTCAR')
                    scf_path = directory / scf / 'OUTCAR'
                    scf_size = scf_source['size_bytes']
                    with LOCK: job['total_bytes'] += scf_size
                    scf_receipt = _download(scf_source, scf_path, job)
                    copied.append(scf_receipt)
                    reference = _fermi(scf_path)
                    reference['scf_attempt_id'] = scf
                except (KeyError, OSError, ValueError):
                    with LOCK:
                        job['total_bytes'] -= scf_size
                        job['downloaded_bytes'] = before_bytes
                    reference['warning'] = 'SCF 费米能不可读；当前使用能带 OUTCAR 费米能'
            result['band']['energy_reference'] = reference
            result['band']['kpath_labels'] = _kpath_labels(folder/'KPOINTS', result['band']['nk'])
            result['band']['kpath_breaks'] = [item['index'] for item in result['band']['kpath_labels'][2::2]]
        if dos:
            folder = directory / dos['id']; result['dos'] = _doscar(folder/'DOSCAR', _species(folder/'POSCAR'), directory)
            result['dos']['energy_reference'] = _fermi(folder/'OUTCAR')
        if band and dos:
            bscf = (band.get('dependency_snapshot') or {}).get('upstream_attempt_id')
            dscf = (dos.get('dependency_snapshot') or {}).get('upstream_attempt_id')
            result['compatible'] = bool(bscf and bscf == dscf)
            result['compatibility_note'] = '' if result['compatible'] else '能带与 DOS 未绑定同一 SCF；分别显示来源，不绘制同源合并图'
        if scratch:
            _cleanup_remote_pbands(band['config']['alias'], scratch)
            scratch = None
        if job.get('discarded'):raise ValueError('本地绘图来源已由用户清理')
        (directory / 'metadata.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        with LOCK: job.update(state='READY', metadata=result, current_file='')
    except BaseException as exc:
        with LOCK: job.update(state='FAILED', error=str(exc)[:1000], current_file='')
    finally:
        if job.get('discarded'):shutil.rmtree(directory,ignore_errors=True)
        if scratch:
            try: _cleanup_remote_pbands(band['config']['alias'], scratch)
            except ValueError as cleanup_error:
                with LOCK: job.update(state='FAILED', error=str(cleanup_error)[:1000], current_file='')


def _selection(meta: dict, selected: list[dict]) -> list[tuple[str,str]]:
    if len(selected) > 40: raise ValueError('一次最多显示 40 条投影曲线')
    available = set()
    for part in ('band', 'dos'):
        if meta.get(part):
            available.update((element, orbital) for element in meta[part]['elements'] for orbital in meta[part]['orbitals'])
            available.update((element, group) for element in meta[part]['elements'] for group in ORBITAL_GROUPS)
    result=[]
    for item in selected:
        pair=(item.get('element'), item.get('orbital'))
        if pair not in available: raise ValueError('所选元素或轨道在来源文件中不存在')
        if pair not in result: result.append(pair)
    return result


def _orbital_indexes(names: list[str], orbital: str) -> list[int]:
    if orbital in ORBITAL_GROUPS:
        return [i for i, name in enumerate(names) if name in ORBITAL_GROUPS[orbital] or name == orbital]
    return [names.index(orbital)] if orbital in names else []


def series(key: str, selected: list[dict], emin: float = -4, emax: float = 4) -> dict:
    meta = _metadata(CACHE_ROOT / key)
    if not meta: raise ValueError('绘图缓存尚未准备完成')
    _available(meta)
    if not all(math.isfinite(x) for x in (emin, emax)) or emin >= emax: raise ValueError('能量范围无效')
    if meta.get('remote_parts'):
        from . import compact_plots
        return compact_plots.selected_series(meta, selected, emin, emax, CACHE_ROOT/key)
    return _series_data(meta, CACHE_ROOT/key, selected, emin, emax)


def _series_data(meta, directory, selected, emin, emax):
    selection = _selection(meta, selected)
    response = {'id': meta.get('id', directory.name), 'band': None, 'dos': None, 'selection': selected, 'emin': emin, 'emax': emax,
                'compatible': meta.get('compatible', True), 'compatibility_note': meta.get('compatibility_note','')}
    if meta.get('band'):
        info=meta['band']; energies=np.load(directory/'band_energy.npy',mmap_mode='r')[:info['spins']]
        weights=np.load(directory/'band_weight.npy',mmap_mode='r')[:info['spins']]
        kcoords=np.load(directory/'band_kcoords.npy',mmap_mode='r')
        reciprocal = _reciprocal_lattice(directory/meta['band_attempt_id']/'POSCAR')
        steps = np.linalg.norm(np.diff(kcoords,axis=0) @ reciprocal,axis=1)
        breaks = info.get('kpath_breaks', [item['index'] for item in info.get('kpath_labels',[])[2::2]])
        for start in breaks:
            if 0 < start < len(kcoords): steps[start-1] = 0
        x=np.r_[0,np.cumsum(steps)].tolist()
        ticks=[]
        for item in info.get('kpath_labels',[]):
            coordinate=x[item['index']]
            if ticks and abs(ticks[-1]['x']-coordinate)<1e-7:
                if item['label'] not in ticks[-1]['label'].split('|'):
                    ticks[-1]['label'] += '|' + item['label']
            else: ticks.append({'x':coordinate,'label':item['label']})
        ref=info['energy_reference']['fermi_eV']
        band_indices=np.flatnonzero(np.any((energies-ref>=emin-.5)&(energies-ref<=emax+.5),axis=(0,1)))
        traces=[]
        for element, orbital in selection:
            if element not in info['elements']: continue
            indexes=_orbital_indexes(info['orbitals'],orbital)
            if not indexes: continue
            values=np.asarray(weights[:,:,band_indices,info['elements'].index(element),:][:,:,:,indexes].sum(axis=-1))
            traces.append({'element':element,'orbital':orbital,'weights':values.tolist()})
        response['band']={'spins':info['spins'],'x':x,'breaks':breaks,
                          'ticks':ticks,
                          'band_indices':[int(i)+1 for i in band_indices],
                          'energies':(np.asarray(energies[:,:,band_indices])-ref).tolist(),
                          'traces':traces,'reference':info['energy_reference'],'orbitals':info['orbitals'],
                          'elements':info['elements'],'nk':info['nk'],'nb':info['nb']}
    if meta.get('dos'):
        info=meta['dos']; energy=np.load(directory/'dos_energy.npy',mmap_mode='r')
        total=np.load(directory/'dos_total.npy',mmap_mode='r')
        projected=np.load(directory/'dos_projected.npy',mmap_mode='r')
        mask=(energy-info['energy_reference']['fermi_eV']>=emin)&(energy-info['energy_reference']['fermi_eV']<=emax)
        traces=[]
        for element, orbital in selection:
            if element not in info['elements']: continue
            indexes=_orbital_indexes(info['orbitals'],orbital)
            if not indexes: continue
            values=np.asarray(projected[:,info['elements'].index(element),indexes,:][:, :, mask].sum(axis=1))
            traces.append({'element':element,'orbital':orbital,'values':values.tolist()})
        response['dos']={'spins':info['spins'],'energy':(energy[mask]-info['energy_reference']['fermi_eV']).tolist(),
                         'total':np.asarray(total[:,mask]).tolist(),'traces':traces,
                         'reference':info['energy_reference'],'orbitals':info['orbitals'],'elements':info['elements']}
    return response


def save(key: str, project_id: str, selected: list[dict], emin: float, emax: float,
         png_data_url: str, svg: str, title: str = '', point_size_scale: float = 1, png_file=None) -> dict:
    meta=_metadata(CACHE_ROOT/key)
    if not meta or meta['project_id'] != project_id: raise ValueError('结果来源项目不匹配')
    if not math.isfinite(point_size_scale) or not 0.1 <= point_size_scale <= 1.5:
        raise ValueError('彩点大小超出可用范围')
    if png_file is None and (not png_data_url.startswith('data:image/png;base64,') or len(png_data_url)>20_000_000 or not svg.startswith('<svg') or len(svg)>22_000_000):
        raise ValueError('图片格式或大小无效')
    if re.search(r'<(?:script|foreignObject)\b|\bon\w+\s*=', svg, re.I):
        raise ValueError('SVG 不允许脚本或事件属性')
    if png_file is not None:
        with png_file.open('rb') as stream:image=stream.read(24)
    else:image=base64.b64decode(png_data_url.split(',',1)[1],validate=True)
    if not image.startswith(b'\x89PNG\r\n\x1a\n') or png_file is not None and (len(image)<24 or image[12:16]!=b'IHDR'):raise ValueError('PNG 数据无效')
    if png_file is not None:
        import struct
        width,height=struct.unpack('>II',image[16:24])
        if not width or not height:raise ValueError('PNG 尺寸无效')
    payload=series(key,selected,emin,emax)
    identifier=uuid.uuid4().hex; directory=SAVED_ROOT/identifier; directory.mkdir(parents=True,exist_ok=False)
    if png_file is not None:
        import shutil
        shutil.copyfile(png_file,directory/'figure.png')
        with png_file.open('rb') as source,(directory/'figure.svg').open('w') as target:
            target.write('<svg xmlns="http://www.w3.org/2000/svg" width="{}" height="{}"><image width="100%" height="100%" href="data:image/png;base64,'.format(width,height))
            while True:
                block=source.read(65535)
                if not block:break
                target.write(base64.b64encode(block).decode())
            target.write('"/></svg>')
    else:
        (directory/'figure.png').write_bytes(image)
        (directory/'figure.svg').write_text(svg)
    with (directory/'selected-curves.csv').open('w',newline='') as stream:
        writer=csv.writer(stream); writer.writerow(['kind','spin','element','orbital','k_index','band','x','energy_relative_eV','weight_or_dos'])
        band=payload.get('band')
        if band:
            for trace in band['traces']:
                for spin, table in enumerate(trace['weights'],1):
                    for k, row in enumerate(table):
                        for b, weight in enumerate(row):
                            energy=band['energies'][spin-1][k][b]
                            if math.isfinite(energy) and emin<=energy<=emax:
                                writer.writerow(['fatband',spin,trace['element'],trace['orbital'],k+1,band['band_indices'][b],band['x'][k],energy,weight])
        dos=payload.get('dos')
        if dos:
            for trace in dos['traces']:
                for spin,row in enumerate(trace['values'],1):
                    for i,value in enumerate(row):writer.writerow(['pdos',spin,trace['element'],trace['orbital'],'','', '',dos['energy'][i],value])
    receipt={'schema':'fpilot.saved-interactive-plot.v1','id':identifier,'project_id':project_id,
             'cache_id':key,'band_attempt_id':meta.get('band_attempt_id'),'dos_attempt_id':meta.get('dos_attempt_id'),
             'selected':selected,'energy_window_eV':[emin,emax],'title':title[:160],
             'point_size_scale':point_size_scale,'axis_upper_padding':0.25,
             'created_at':datetime.now(timezone.utc).isoformat(),'sources':meta['sources'],
             'cache_path':str(CACHE_ROOT/key),'files':['figure.png','figure.svg','selected-curves.csv']}
    (directory/'record.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    return receipt


def saved(project_id: str) -> list[dict]:
    rows=[]
    for path in SAVED_ROOT.glob('*/record.json'):
        try:
            row=json.loads(path.read_text())
            if row.get('project_id')==project_id and not is_recycled('saved-plots',row['id']):rows.append(row)
        except (OSError,ValueError):pass
    return sorted(rows,key=lambda row:row['created_at'],reverse=True)


def saved_file(identifier: str, name: str, project_id: str) -> bytes:
    if not re.fullmatch('[a-f0-9]{32}',identifier) or name not in ('figure.png','figure.svg','selected-curves.csv'):
        raise ValueError('结果文件标识无效')
    row=json.loads((SAVED_ROOT/identifier/'record.json').read_text())
    if row.get('project_id')!=project_id:raise ValueError('结果不属于当前项目')
    return (SAVED_ROOT/identifier/name).read_bytes()

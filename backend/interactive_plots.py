"""Attempt-bound VASP projection readers, cached arrays and saved figures.

Explicit preparation runs the shared reader at the source; bounded derived
arrays are cached locally, while oversized arrays remain on the server.
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
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import execution, remote_paths
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
    from .input_display import frozen_note
    from .calculation_nodes import case_info
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
                         'input_note':frozen_note(row), 'chain_run_id':row.get('chain_run_id'),
                         'config':{'name':row.get('config',{}).get('name')},
                         'created_at':row.get('created_at'), 'state':row['state'], **case_info(row),
                         'job_id': row.get('job_id'), 'path': row.get('remote_path_absolute'),
                         'node_id': row.get('node_id') or manifest.get('node_id'),
                         'execution_scope': row.get('execution_scope'),
                         'scf_attempt_id': (row.get('dependency_snapshot') or {}).get('upstream_attempt_id')})
    return rows


def _pband_names(elements: list[str], available: set[str]) -> tuple[str, list[str]] | None:
    patterns = (('soc', lambda element: [f'PBAND_{element}_SOC.dat']),
                ('spin2', lambda element: [f'PBAND_{element}_UP.dat', f'PBAND_{element}_DW.dat']),
                ('spin1', lambda element: [f'PBAND_{element}.dat']))
    for mode, names_for_element in patterns:
        names = [name for element in elements for name in names_for_element(element)]
        if all(name in available for name in names):
            return mode, names
    return None


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
    low = name.lower()
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


def _check_procar_eigenval(path: Path, target: Path, spins: int, nk: int, nb: int) -> None:
    """Check matching states without loading another full energy array."""
    energies = np.load(target / 'band_energy.npy', mmap_mode='r')
    coords = np.load(target / 'band_kcoords.npy', mmap_mode='r')
    try:
        with path.open(errors='strict') as stream:
            first = next(stream).split()
            if len(first) < 4 or first[3] not in ('1', '2') or int(first[3]) != spins:
                raise ValueError('PROCAR 与 EIGENVAL 自旋通道不一致')
            for _ in range(4): next(stream)
            dimensions = next(stream).split()
            if len(dimensions) != 3 or tuple(map(int, dimensions[1:])) != (nk, nb):
                raise ValueError('PROCAR 与 EIGENVAL 的 k 点或能带数不一致')
            for k in range(nk):
                line = next(stream).strip()
                while not line: line = next(stream).strip()
                fields = line.split()
                point = [float(value) for value in fields[:3]]
                if len(fields) != 4 or not np.isfinite(point).all() or not np.allclose(point, coords[k], atol=2e-6, rtol=0):
                    raise ValueError('PROCAR 与 EIGENVAL 的 k 点坐标不一致')
                for band in range(nb):
                    fields = next(stream).split()
                    if len(fields) != 1 + 2 * spins or int(fields[0]) != band + 1:
                        raise ValueError('EIGENVAL 能带行不完整或顺序无效')
                    values = [float(value) for value in fields[1:1 + spins]]
                    if not np.isfinite(values).all() or not np.allclose(values, energies[:spins, k, band], atol=0.002, rtol=0):
                        raise ValueError('PROCAR 与 EIGENVAL 的能带能量不一致（容差 0.002 eV）')
    except StopIteration as exc:
        raise ValueError('EIGENVAL 内容截断，无法核对 PROCAR') from exc


def _procar(path: Path, species: list[str], target: Path, noncollinear: bool,
            eigenval: Path | None = None) -> dict:
    """Stream charge projections into element arrays; never sum SOC magnetization."""
    dimensions = re.compile(r'# of k-points:\s*(\d+)\s*# of bands:\s*(\d+)\s*# of ions:\s*(\d+)')
    kline = re.compile(r'k-point\s+(\d+)\s*:\s*(.*?)\s+weight\s*=', re.I)
    bandline = re.compile(r'band\s+(\d+).*energy\s+(' + NUMBER + r')', re.I)
    elements = list(dict.fromkeys(species))
    atom_elements = [elements.index(symbol) for symbol in species]
    labels = []
    nk = nb = 0; energies = weights = kcoords = charge = None
    spin = 0; k = band = -1; next_k = next_band = completed = ion = 0
    header = False
    expected_tables = 4 if noncollinear else 1

    def finish_band():
        if band >= 0 and (completed != expected_tables or ion):
            raise ValueError('PROCAR 投影表不完整：spin %d / k %d / band %d' % (spin + 1, k + 1, band + 1))

    def finish_k():
        finish_band()
        if k >= 0 and next_band != nb:
            raise ValueError('PROCAR 能带块不完整')

    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for raw in stream:
            digest.update(raw)
            line = raw.decode('utf-8').strip()
            match = dimensions.search(line)
            if match:
                values = tuple(map(int, match.groups()))
                if not nk:
                    nk, nb, ni = values
                    if min(values) <= 0 or ni != len(species):
                        raise ValueError('PROCAR 维度无效或原子数与 POSCAR 不一致')
                    energies = np.lib.format.open_memmap(target / 'band_energy.npy', mode='w+', dtype='float32', shape=(2, nk, nb))
                    kcoords = np.lib.format.open_memmap(target / 'band_kcoords.npy', mode='w+', dtype='float32', shape=(nk, 3))
                elif values != (nk, nb, len(species)):
                    raise ValueError('PROCAR 不同自旋通道维度不一致')
                continue
            if not nk: continue
            match = re.match(r'spin component\s+(\d+)', line, re.I)
            if match:
                finish_k()
                value = int(match.group(1)) - 1
                if (value == 0 and k == -1 and next_k == 0 and spin == 0):
                    pass
                elif value == spin + 1 and next_k == nk:
                    spin = value
                else:
                    raise ValueError('PROCAR 自旋通道重复或顺序无效')
                if spin > 1 or (noncollinear and spin):
                    raise ValueError('PROCAR 自旋通道与计算模式不一致')
                k = band = -1; next_k = next_band = 0
                continue
            match = kline.match(line)
            if match:
                finish_k()
                index = int(match.group(1)) - 1
                if index == 0 and next_k == nk:
                    spin += 1; next_k = 0
                if spin > 1 or (noncollinear and spin) or index != next_k:
                    raise ValueError('PROCAR k 点重复、缺失或自旋通道无效')
                values = re.findall(NUMBER, match.group(2))
                if len(values) != 3 or not all(math.isfinite(float(value)) for value in values):
                    raise ValueError('PROCAR k 点坐标无效')
                point = [float(value) for value in values]
                if spin == 0: kcoords[index] = point
                elif not np.allclose(point, kcoords[index], atol=2e-6, rtol=0):
                    raise ValueError('PROCAR 上下自旋 k 点坐标不一致')
                k = index; next_k += 1; band = -1; next_band = 0
                continue
            match = bandline.match(line)
            if match:
                finish_band()
                index = int(match.group(1)) - 1
                energy = float(match.group(2))
                if k < 0 or index != next_band or index >= nb or not math.isfinite(energy):
                    raise ValueError('PROCAR 能带编号、顺序或能量无效')
                energies[spin, k, index] = energy
                band = index; next_band += 1; completed = ion = 0; header = False; charge = None
                continue
            if band < 0 or completed >= expected_tables:
                # LORBIT phase-factor blocks are not charge projections.
                continue
            fields = line.split()
            if fields and fields[0].lower() == 'ion':
                names = [_canonical(name) for name in fields[1:-1]]
                if ion or fields[-1].lower() != 'tot' or not names or len(set(names)) != len(names):
                    raise ValueError('PROCAR 轨道表头或原子行无效')
                if not labels:
                    labels = names
                    weights = np.lib.format.open_memmap(target / 'band_weight.npy', mode='w+', dtype='float32',
                                                       shape=(2, nk, nb, len(elements), len(labels)))
                elif names != labels:
                    raise ValueError('PROCAR 轨道表头不一致')
                if completed == 0 and charge is None: charge = np.zeros((len(elements), len(labels)), dtype='float32')
                header = True
                continue
            if fields and (fields[0].isdigit() or fields[0].lower() == 'tot'):
                if not header or len(fields) != len(labels) + 2:
                    raise ValueError('PROCAR 原子投影列数不完整')
                values = [float(value) for value in fields[1:]]
                if not all(math.isfinite(value) for value in values):
                    raise ValueError('PROCAR 投影含无效数值')
                if fields[0].lower() == 'tot':
                    if ion != len(species): raise ValueError('PROCAR 原子投影缺失')
                    if completed == 0: weights[spin, k, band] = charge
                    completed += 1; ion = 0
                else:
                    if int(fields[0]) != ion + 1 or ion >= len(species):
                        raise ValueError('PROCAR 原子投影重复或顺序无效')
                    if completed == 0:
                        charge[atom_elements[ion]] += values[:-1]
                    ion += 1
    finish_k()
    if weights is None or next_k != nk or k < 0:
        raise ValueError('PROCAR 能带投影不完整')
    spins = spin + 1
    energies.flush(); weights.flush(); kcoords.flush()
    del energies, weights, kcoords
    if eigenval is not None:
        _check_procar_eigenval(eigenval, target, spins, nk, nb)
    return {'elements': elements, 'orbitals': labels, 'spins': spins, 'nk': nk, 'nb': nb,
            'source_format': 'procar', 'parser': 'fpilot.procar.v1', 'procar_sha256': digest.hexdigest(),
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

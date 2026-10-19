"""Bounded, source-labeled scalar comparison for VASP attempts."""
import json
import re
import hashlib
import math
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from filelock import FileLock

from . import execution as ex, remote_paths, scans,remote_transport
from pymatgen.core import Lattice

LATTICE_KEYS = ('lattice_a', 'lattice_b', 'lattice_c', 'lattice_alpha', 'lattice_beta', 'lattice_gamma', 'volume')
SUMMARY_PARSER = r'''
import hashlib,json,os,re,sys
path='OUTCAR';cached=json.loads(sys.argv[1]);before=os.stat(path,follow_symlinks=False)
def stamp(s):return [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]
if cached.get('signature')==stamp(before):
    print(cached['output']);sys.exit(0)
digest=hashlib.sha256();found={};lattice=[];lattice_line=None;remaining=0
patterns=('free  energy   TOTEN','number of electron.*magnetization','Elapsed time \\(sec\\)','reached required accuracy')
with open(path,'rb') as stream:
    for index,raw in enumerate(stream,1):
        digest.update(raw);line=raw.decode(errors='replace').rstrip('\r\n')
        for pattern in patterns:
            if re.search(pattern,line):found[pattern]=line
        if 'direct lattice vectors' in line.lower():
            lattice=[];lattice_line=index;remaining=3
        elif remaining:
            lattice.append(line);remaining-=1
after=os.stat(path,follow_symlinks=False)
if stamp(before)!=stamp(after):raise ValueError('OUTCAR changed during summary extraction')
sha=digest.hexdigest();lines=['FPILOT_SHA='+sha]+list(found.values())
if lattice_line:
    lines+=['FPILOT_LATTICE_LINE='+str(lattice_line),'FPILOT_LATTICE_BEGIN']+lattice+['FPILOT_LATTICE_END']
lines+=['FPILOT_SHA_AFTER='+sha,'FPILOT_SUMMARY_STAMP='+json.dumps(stamp(after))]
print('\n'.join(lines))
'''


def _lattice_metrics(output, source):
    result = dict.fromkeys(LATTICE_KEYS)
    match = re.search(r'^FPILOT_LATTICE_BEGIN\n(.*?)^FPILOT_LATTICE_END$', output, re.M | re.S)
    if not match:
        return {**result, 'lattice_error': 'OUTCAR 未提供最后一组完整晶格；未用项目原始结构代替'}
    try:
        lines = match.group(1).strip().splitlines()
        if len(lines) != 3 or any(len(line.split()) != 6 for line in lines):
            raise ValueError('最后一组晶格不完整')
        matrix = [[float(value.replace('D', 'E').replace('d', 'e')) for value in line.split()[:3]] for line in lines]
        if not all(math.isfinite(value) for row in matrix for value in row):
            raise ValueError('晶格含非有限值')
        lattice = Lattice(matrix)
        values = (*lattice.abc, *lattice.angles, lattice.volume)
        if not all(math.isfinite(value) and value > 0 for value in values) or lattice.volume < 1e-8:
            raise ValueError('晶格退化或体积为零')
        lattice_source = {**source, 'method': 'last direct lattice vectors; pymatgen Lattice'}
        line = re.search(r'^FPILOT_LATTICE_LINE=(\d+)$', output, re.M)
        if line:
            lattice_source['line'] = int(line.group(1))
        return {key: {'value': float(value), 'unit': 'Å' if index < 3 else '°' if index < 6 else 'Å³', 'source': lattice_source}
                for index, (key, value) in enumerate(zip(LATTICE_KEYS, values))}
    except (ValueError, TypeError, OverflowError) as exc:
        return {**result, 'lattice_error': 'OUTCAR 晶格不可用：' + str(exc)[:200] + '；未用项目原始结构代替'}


def _cell_composition(manifest, files):
    raw = files.get('POSCAR') or files.get('POSCAR.reference')
    lines = raw.decode(errors='replace').splitlines() if raw else []
    for index in (6, 5):
        if len(lines) > index and re.fullmatch(r'\s*\d+(?:\s+\d+)*\s*', lines[index]):
            counts = list(map(int, lines[index].split()))
            species = lines[5].split() if index == 6 else manifest.get('species') or []
            if len(species) != len(counts) or any(count <= 0 for count in counts):
                return None
            composition = {}
            for element, count in zip(species, counts):
                composition[element] = composition.get(element, 0) + count
            return hashlib.sha256(json.dumps(composition, sort_keys=True).encode()).hexdigest()
    return None


def _atom_count(record):
    _, files = ex.bundle(record['bundle_id'], allow_dependencies=True)
    raw = files.get('POSCAR') or files.get('POSCAR.reference')
    if not raw: return None
    lines = raw.decode(errors='replace').splitlines()
    for index in (6, 5):
        if len(lines) > index and re.fullmatch(r'\s*\d+(?:\s+\d+)*\s*', lines[index]):
            return sum(map(int, lines[index].split()))
    return None


def _extract(record):
    folder=ex.ROOT/'scan-metrics';folder.mkdir(parents=True,exist_ok=True)
    with FileLock(str(folder/(record['id']+'.lock')),timeout=190):
        return _extract_locked(record,folder/(record['id']+'.json'))


def _extract_locked(record,cache_path):
    path = remote_paths.shell_path(ex.attempt_path(record)['shell'])
    import shlex
    identity=[record['config']['alias'],path,record['bundle_id'],1]
    try:cached=json.loads(cache_path.read_text())
    except (OSError,ValueError):cached={}
    if cached.get('identity')!=identity:cached={}
    command=('set -e\ncd '+path+'\ntest -f OUTCAR; test ! -L OUTCAR\n'+shlex.quote(remote_transport._interpreter(record['config']['alias']))+' -c '+
             shlex.quote(SUMMARY_PARSER)+' '+shlex.quote(json.dumps(cached)))
    receipt = ex.ssh(record['config']['alias'], command, timeout=180, stdout_limit=16384)
    if receipt['exit_code'] != 0: raise ValueError('远端 OUTCAR 摘要提取失败')
    output = receipt['stdout']
    match = re.search(r'^FPILOT_SHA=([a-f0-9]{64})$', output, re.M)
    if not match: raise ValueError('远端 OUTCAR hash 回执缺失')
    sha = match.group(1)
    after = re.search(r'^FPILOT_SHA_AFTER=([a-f0-9]{64})$', output, re.M)
    if not after or after.group(1) != sha:
        raise ValueError('OUTCAR 在读取过程中发生变化或缺少读取后校验；请重新读取指标')
    stamp=re.search(r'^FPILOT_SUMMARY_STAMP=(.+)$',output,re.M)
    if stamp:
        from .extension_protocol import atomic_json
        atomic_json(cache_path,{'identity':identity,'signature':json.loads(stamp.group(1)),'output':output})
    def last(pattern):
        found = re.findall(pattern, output, re.M)
        return float(found[-1]) if found else None
    number = r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)'
    energy = last(r'free\s+energy\s+TOTEN\s*=\s*' + number)
    magnetic = re.findall(r'number of electron\s+' + number + r'\s+magnetization\s+' + number, output)
    magnetization = float(magnetic[-1][1]) if magnetic else None
    seconds = last(r'Elapsed time \(sec\):\s*' + number)
    atoms = _atom_count(record)
    source = {'file':'OUTCAR','sha256':sha,'method':'bounded remote summary','host':record['config']['alias']}
    def scalar(value, unit):
        return {'value':value,'unit':unit,'source':source} if value is not None else None
    manifest,_=ex.bundle(record['bundle_id'],allow_dependencies=True)
    return {'energy':scalar(energy,'eV/cell'), 'energy_per_atom':scalar(energy/atoms,'eV/atom') if energy is not None and atoms else None,
            'magnetization':scalar(magnetization,'mu_B/cell'), 'elapsed':scalar(seconds,'s'),
            'ionic_converged':bool(re.search(r'reached required accuracy',output)) if manifest.get('stage')=='relax' else None,
            'atoms':atoms,'source':source, **_lattice_metrics(output, source)}


def compare(project_id, attempt_ids):
    if not isinstance(attempt_ids,list) or not 1 <= len(attempt_ids) <= 64 or len(attempt_ids)!=len(set(attempt_ids)):
        raise ValueError('请选择 1–64 个不重复 attempt')
    records=[ex.get(attempt_id) for attempt_id in attempt_ids]
    if any(record.get('project_id')!=project_id for record in records): raise ValueError('attempt 不属于当前项目')
    def one(record):
        attempt_id=record['id']
        evidence=record.get('evidence') or {}
        manifest,files=ex.bundle(record['bundle_id'],allow_dependencies=True)
        incar=files.get('INCAR',b'').decode(errors='replace')
        model_lines=[line.strip() for line in incar.splitlines() if re.match(r'\s*(GGA|LDAU|LDAUTYPE|LDAUL|LDAUU|LDAUJ|ISPIN|LSORBIT|LNONCOLLINEAR)\s*=',line)]
        row={'attempt_id':attempt_id,'node_id':record.get('node_id'),'state':record['state'],'job_id':record.get('job_id'),
             'stage':manifest.get('stage'),'geometry_sha256':manifest.get('geometry_sha256'),
             'cell_composition_sha256':_cell_composition(manifest,files),
             'potcar_sha256':hashlib.sha256(files['POTCAR']).hexdigest() if 'POTCAR' in files else None,
             'model_sha256':hashlib.sha256('\n'.join(sorted(model_lines)).encode()).hexdigest(),
             'scheduler_state':evidence.get('scheduler_state'),'program_done':evidence.get('vasp_timing') is True,
             'ediff_reached':evidence.get('ediff_reached'),'scientific_validation':evidence.get('scientific_validation','NOT_PERFORMED'),
             'metrics':None,'error':None}
        if record['state']=='COMPLETED' and evidence.get('vasp_timing') is True:
            try: row['metrics']=_extract(record)
            except (ValueError,OSError,KeyError) as exc: row['error']=str(exc)[:500]
        return row
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows=list(pool.map(one,records))
    from . import result_overview
    for record,row in zip(records,rows):
        row['input']=result_overview.frozen_input(record)
        result_overview.cache(record,row)
    return {'project_id':project_id,'rows':rows}


def promote(batch_id, project_id, case_id, reason):
    if not isinstance(reason,str) or not reason.strip() or len(reason)>2000: raise ValueError('请填写选择理由')
    batch=scans.get(batch_id)
    case=next((c for c in batch['cases'] if c['id']==case_id),None)
    if batch['project_id']!=project_id or not case or not case['attempt_id']: raise ValueError('批次尚未结束或 case 不属于当前批次')
    record=ex.get(case['attempt_id'])
    evidence=record.get('evidence') or {}
    if record['state']!='COMPLETED' or not evidence.get('vasp_timing') or evidence.get('ediff_reached') is not True or evidence.get('wrapper_exit') not in (0,None):
        raise ValueError('case 缺少完成与电子收敛证据，不能提升')
    if batch['stage']=='relax':
        metric=_extract(record)
        if metric['ionic_converged'] is not True: raise ValueError('relax 缺少离子收敛证据，不能提升')
    selected={'case_id':case_id,'attempt_id':record['id'],'bundle_id':case['bundle_id'],'case_table_hash':batch['case_table_hash'],
              'parameters':case['parameters'],'input_hashes':case['input_hashes'],'reason':reason.strip(),'at':ex.now()}
    batch['promotion']=selected
    scans.save(batch)
    return selected

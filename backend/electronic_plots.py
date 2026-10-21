"""Local PDOS and fatband figures from attempt-bound VASP files."""
import hashlib
import json
import re
import shlex
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import artifacts, execution, remote_paths, servers
from .recycle import is_recycled
from .storage import ROOT

SCRIPT = Path(__file__).with_name('builtin_analysis') / 'banddos.py'
EXTRACTOR = Path(__file__).with_name('builtin_analysis') / 'extract_bounded.py'
SAFE_SELECTOR = re.compile(r'^(?:all|[0-9]+(?:[-,][0-9]+)*)$')
SAFE_ORBITALS = re.compile(r'^[A-Za-z0-9_,+-]{1,120}$')


def _local_file(attempt_id, entry, directory):
    key = hashlib.sha256(f"{attempt_id}|{entry['path']}|{entry['size_bytes']}|{entry['modified_at']}".encode()).hexdigest()
    cache = ROOT / 'artifact-cache' / 'inputs' / key
    if not cache.is_file():
        raw, _ = artifacts.pull(attempt_id, entry['path'])
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(raw)
    owner = ROOT / 'artifact-cache' / 'input-owners' / (key+'.json')
    owner.parent.mkdir(parents=True, exist_ok=True)
    owner.write_text(json.dumps({'attempt_id':attempt_id}))
    destination = directory / entry['name']
    destination.write_bytes(cache.read_bytes())
    return {'name':entry['name'], 'sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),
            'size_bytes':destination.stat().st_size, 'remote_path':entry['path']}


def _remote_table(record, entry, directory, mode, ions, orbitals, fermi=None, noncollinear=False):
    alias = record['config']['alias']
    from .analysis_queue import interpreter as analysis_interpreter
    interpreter = analysis_interpreter(alias)
    root = remote_paths.shell_path(artifacts._root(record))
    command = 'cd ' + root + ' && ' + shlex.quote(interpreter) + ' - ' + ' '.join(
        shlex.quote(str(part)) for part in ([mode, entry['path'], ions, orbitals] +
                                     ([str(fermi)] if fermi is not None else []) +
                                     (['noncollinear'] if noncollinear else [])))
    from .analysis_queue import slot,environment
    with slot(alias):
        receipt = execution.ssh(alias,environment()+command,data=EXTRACTOR.read_bytes(),timeout=180,
                                stdout_limit=artifacts.MAX_PULL_BYTES+4096,lane='analysis')
    if receipt['exit_code'] != 0 or receipt.get('stdout_truncated'):
        raise ValueError('服务器侧有界提取失败：' + receipt.get('stderr', '')[-500:] + receipt.get('stdout', '')[-500:])
    lines = receipt['stdout'].splitlines()
    if len(lines) < 3 or not lines[0].startswith('FPILOT_META=') or lines[-1] != 'FPILOT_END':
        raise ValueError('服务器侧提取结果不完整')
    meta = json.loads(lines[0].split('=', 1)[1])
    if meta.get('kind') != mode or not re.fullmatch(r'[0-9a-f]{64}', meta.get('sha256', '')):
        raise ValueError('服务器侧提取结果来源无效')
    body = '\n'.join(lines[1:-1]) + '\n'
    if len(body.encode()) > artifacts.MAX_PULL_BYTES:
        raise ValueError('提取结果超过 10 MiB，请缩小离子、轨道或能量范围')
    table = directory / 'plot-table.dat'
    table.write_text(body)
    return {'name':entry['name'], 'sha256':meta['sha256'], 'size_bytes':entry['size_bytes'],
            'remote_path':entry['path'], 'derived_table':table.name, 'meta':meta}


def run(project_id, attempt_id, mode, ions='all', orbitals='d'):
    from .analysis_queue import subprocess_environment
    if mode not in ('pdos','fatband') or not SAFE_SELECTOR.fullmatch(ions) or not SAFE_ORBITALS.fullmatch(orbitals):
        raise ValueError('请选择 PDOS/fatband 与有效的离子、轨道')
    record = execution.get(attempt_id)
    if record.get('project_id') != project_id or record.get('state') != 'COMPLETED' or not (record.get('evidence') or {}).get('vasp_timing'):
        raise ValueError('只能使用当前项目已完成的 VASP attempt')
    manifest, frozen_files = execution.bundle(record['bundle_id'], allow_dependencies=True)
    noncollinear = any(execution._incar_value(frozen_files['INCAR'], tag) in ('.TRUE.','TRUE','T')
                       for tag in ('LSORBIT','LNONCOLLINEAR'))
    expected = 'dos' if mode == 'pdos' else 'bands'
    if manifest.get('stage') != expected:
        raise ValueError('PDOS 需要 DOS attempt；fatband 需要能带 attempt')
    listing = artifacts.refresh(attempt_id)
    names = {entry['path']:entry for entry in listing['files']}
    required = 'DOSCAR' if mode == 'pdos' else 'PROCAR'
    if required not in names:
        raise ValueError('远端 attempt 缺少 ' + required)
    large = names[required]['size_bytes'] > artifacts.MAX_PULL_BYTES
    analysis_id = uuid.uuid4().hex
    directory = ROOT / 'plots' / analysis_id
    directory.mkdir(parents=True, exist_ok=False)
    result = {'schema':'fpilot.local-plot.v1', 'id':analysis_id, 'project_id':project_id,
              'attempt_id':attempt_id, 'mode':mode, 'ions':ions, 'orbitals':orbitals,
              'created_at':datetime.now(timezone.utc).isoformat(), 'state':'RUNNING',
              'scientific_validation':'NOT_PERFORMED', 'host':record['config']['alias'],
              'remote_path':artifacts._root(record),
              'projection_component':'charge' if noncollinear else 'collinear-or-nonspin'}
    try:
        sources=[]
        command=[sys.executable,str(SCRIPT),'dos' if mode=='pdos' else 'fatband',
                 '--formats','png,pdf','--output','figure']
        if mode=='fatband':
            if 'OUTCAR' not in names:
                raise ValueError('fatband 缺少能量参考 OUTCAR')
            base = remote_paths.shell_path(artifacts._root(record))
            receipt = execution.ssh(record['config']['alias'],
                                    "set -e\ncd " + base + "\ngrep 'E-fermi' OUTCAR | tail -n 1", timeout=45)
            match = re.search(r'E-fermi\s*:\s*([-+0-9.Ee]+)', receipt.get('stdout',''))
            if receipt['exit_code'] != 0 or not match:
                raise ValueError('无法从 OUTCAR 核对 fatband 的 E-fermi')
            result['fermi_eV'] = float(match.group(1))
            if not large:
                command.extend(['--fermi',match.group(1)])
            for optional in ('KPOINTS','POSCAR'):
                if optional in names and names[optional]['size_bytes']<=artifacts.MAX_PULL_BYTES:
                    sources.append(_local_file(attempt_id,names[optional],directory))
                    if not large:
                        command.extend(['--'+optional.lower(),optional])
        commands = [command]
        if large:
            kind = 'doscar' if mode == 'pdos' else 'procar'
            source = _remote_table(record,names[required],directory,kind,ions,orbitals,
                                   result.get('fermi_eV') if mode == 'fatband' else None,
                                   noncollinear=noncollinear and mode == 'pdos')
            sources.insert(0,source)
            if mode == 'pdos':
                labels = ('Total up,Total down,PDOS up,PDOS down' if source['meta']['spin']
                          else 'Total DOS,PDOS')
                command.extend(['--table','plot-table.dat','--labels',labels,
                                '--fermi',str(source['meta']['fermi_eV'])])
            else:
                groups = {}
                for line in (directory/'plot-table.dat').read_text().splitlines():
                    columns = line.split()
                    if len(columns) != 4 or not columns[0].isdigit():
                        raise ValueError('PROCAR 有界表列数无效')
                    groups.setdefault(int(columns[0]), []).append(' '.join(columns[1:]))
                commands = []
                for spin, rows in sorted(groups.items()):
                    table = f'spin{spin}.table'
                    (directory/table).write_text('\n'.join(rows)+'\n')
                    commands.append([sys.executable,str(SCRIPT),'fatband','--table',table,
                                     '--formats','png,pdf','--output',f'figure_spin{spin}'])
        elif noncollinear and mode == 'pdos':
            sources.insert(0,_local_file(attempt_id,names[required],directory))
            extracted=subprocess.run([sys.executable,str(EXTRACTOR),'doscar',required,ions,orbitals,'noncollinear'],
                                     cwd=directory,capture_output=True,text=True,timeout=180,env=subprocess_environment())
            lines=extracted.stdout.splitlines()
            if extracted.returncode or len(lines)<3 or not lines[0].startswith('FPILOT_META=') or lines[-1]!='FPILOT_END':
                raise ValueError('本机 SOC DOSCAR 电荷投影提取失败：'+(extracted.stderr or extracted.stdout)[-500:])
            meta=json.loads(lines[0].split('=',1)[1])
            (directory/'plot-table.dat').write_text('\n'.join(lines[1:-1])+'\n')
            command.extend(['--table','plot-table.dat','--labels','Total DOS,PDOS',
                            '--fermi',str(meta['fermi_eV'])])
        else:
            sources.insert(0,_local_file(attempt_id,names[required],directory))
            command.extend(['--'+required.lower(),required,'--ions',ions,'--orbitals',orbitals])
        completed=None
        for plot_command in commands:
            completed=subprocess.run(plot_command,cwd=directory,capture_output=True,text=True,timeout=300,env=subprocess_environment())
            if completed.returncode:
                raise ValueError((completed.stderr or completed.stdout)[-900:])
        outputs=sorted(path.name for path in directory.glob('figure*') if path.suffix in ('.png','.pdf'))
        if not outputs:
            raise ValueError('扩展未生成图片')
        result.update(state='COMPLETED',sources=sources,outputs=outputs,
                      extension='electronic-structure-characterization',stdout=completed.stdout[-1000:])
    except Exception as exc:
        result.update(state='FAILED',error=str(exc)[:1000])
    (directory / 'analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result


def history(project_id, attempt_id):
    rows=[]
    for path in sorted((ROOT/'plots').glob('*/analysis.json'),key=lambda p:p.stat().st_mtime,reverse=True):
        try:
            row=json.loads(path.read_text())
            if row.get('project_id')==project_id and row.get('attempt_id')==attempt_id:
                if not is_recycled('plots',row['id']):
                    rows.append(row)
        except (OSError,ValueError):
            continue
    return rows[:100]


def output(key,name):
    if not re.fullmatch(r'[a-f0-9]{32}',key) or not re.fullmatch(r'figure(?:_spin[0-9]+)?\.(?:png|pdf)',name):
        raise ValueError('图片标识无效')
    directory=ROOT/'plots'/key
    row=json.loads((directory/'analysis.json').read_text())
    if name not in row.get('outputs',[]):
        raise ValueError('图片不存在')
    return (directory/name).read_bytes()

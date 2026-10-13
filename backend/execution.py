"""One frozen-input PBS/Slurm attempt with at-most-once dispatch and evidence refresh."""
import hashlib
import io
import json
import re
import shlex
import sqlite3
import subprocess
import tarfile
import tempfile
import uuid
import os
import sys
import zipfile
import copy
from functools import lru_cache
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from .storage import ROOT
from . import servers
from . import remote_paths
from . import job_templates
from . import recycle


def now():return datetime.now(timezone.utc).isoformat()


def scheduler_name(record):
    return servers.normalize_scheduler(record.get('config',{}).get('scheduler'))


def script_name(scheduler):
    return 'run.slurm' if scheduler == 'slurm' else 'run.pbs'


def template_resources(profile):
    result = {'name':profile['name'], 'queue':profile.get('queue',profile.get('partition')),
            'node':profile['node'], 'ppn':profile.get('ppn',profile.get('ntasks')),
            'walltime':profile['walltime']}
    if profile.get('resource_mode') == 'gpu':
        result.update(mpi_ranks=profile['mpi_ranks'], gpus=profile['gpus'],
                      gpu_resource_registered='yes' if profile['gpu_resource_registered'] else 'no')
        if profile.get('gpu_type'): result['gpu_type'] = profile['gpu_type']
    return result


def validate_parallel(incar, ranks, gpu=False):
    from .source_contracts import validate_pead
    validate_pead(incar, ranks, gpu)
    parallel = {name: int(match.group(1)) for name in ('KPAR', 'NCORE')
                if (match := re.search(r'^\s*' + name + r'\s*=\s*(\d+)\s*$', incar, re.M))}
    if not parallel:
        return
    if not isinstance(ranks, int) or ranks < 1:
        raise ValueError('无法确认实际 MPI 核数，不能审核 KPAR/NCORE')
    kpar, ncore = parallel.get('KPAR', 1), parallel.get('NCORE', 1)
    if kpar < 1 or ncore < 1:
        raise ValueError('KPAR/NCORE 必须是正整数')
    # VASP's OpenACC GPU port resets NCORE to 1; preserve the input as evidence.
    if gpu: ncore = 1
    if ranks % kpar or ranks // kpar % ncore:
        raise ValueError(f'KPAR={kpar}、NCORE={ncore} 与本次 MPI 核数 {ranks} 不整除')


def with_exit_trap(script):
    marker='trap \'rc=$?; printf "%s\\n" "$rc" > .exit-status\' EXIT'
    for line in ('set -euo pipefail','set -eo pipefail'):
        if line in script:
            return script.replace(line,line+'\n'+marker,1)
    lines=script.splitlines(keepends=True)
    index=1 if lines and lines[0].startswith('#!') else 0
    while index<len(lines) and (not lines[index].strip() or lines[index].lstrip().startswith('#PBS') or lines[index].lstrip().startswith('#SBATCH')):
        index+=1
    lines.insert(index,marker+'\n')
    return ''.join(lines)


def db():
    ROOT.mkdir(parents=True,exist_ok=True)
    connection=sqlite3.connect(ROOT/'execution.sqlite3',timeout=10)
    connection.execute('CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,state TEXT NOT NULL,body TEXT NOT NULL)')
    connection.execute('CREATE TABLE IF NOT EXISTS execution_events(id INTEGER PRIMARY KEY,attempt_id TEXT NOT NULL,at TEXT NOT NULL,event TEXT NOT NULL,details TEXT NOT NULL)')
    connection.commit();return connection


def save(record, expected_updated_at=False):
    if recycle.purged('attempts',record['id']):return record
    from . import execution_control as control
    with closing(db()) as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT body FROM attempts WHERE id=?',(record['id'],)).fetchone()
        if not row or recycle.purged('attempts',record['id']):
            c.rollback();return record
        existing=json.loads(row[0]) if row else {}
        if expected_updated_at is not False and existing.get('updated_at') != expected_updated_at:
            c.rollback();return existing
        if existing.get('state') in ('COMPLETED','FAILED','CANCELLED'):
            allowed=('watcher_pid','watcher_error','notification')
            old_cancel, new_cancel = existing.get('cancellation', {}), record.get('cancellation', {})
            if (old_cancel.get('operation_id') and old_cancel.get('operation_id') == new_cancel.get('operation_id')
                    and old_cancel.get('state') != 'CONFIRMED'):
                allowed += ('cancellation',)
            if record.get('state') == existing.get('state'):
                # Terminal state is immutable, but a scheduler accounting row
                # may settle just after the wrapper evidence.  Permit only
                # evidence/receipt enrichment for the same terminal state.
                allowed += ('status_receipt','evidence','updated_at')
            record={**record,**existing,**{k:record[k] for k in allowed if k in record}}
        if existing.get('state') != record['state']:
            control.journal(c,record['id'],'STATE_CHANGED',{'from':existing.get('state'),'to':record['state'],'job_id':record.get('job_id')})
        from . import branch_bindings
        branch_bindings.commit(c,record)
        c.execute('UPDATE attempts SET state=?,body=? WHERE id=?',(record['state'],json.dumps(record,ensure_ascii=False),record['id']));c.commit()
    if record['state']=='FAILED':
        from . import problem_records
        problem_records.try_capture('attempts',record['id'],record)
    return record


def get(key):
    with closing(db()) as c:
        row=c.execute('SELECT body FROM attempts WHERE id=?',(key,)).fetchone()
    if not row:raise ValueError('任务记录不存在')
    return json.loads(row[0])


def history(project_id=None, limit=100):
    with closing(db()) as c:
        rows = [json.loads(row[0]) for row in c.execute('SELECT body FROM attempts ORDER BY rowid DESC')]
    from .calculation_nodes import case_info
    return [{**row, **case_info(row)} for row in rows if (project_id is None or row.get('project_id') == project_id) and
            not recycle.inactive('attempts',row['id'],row)][:limit]


def bundle(key, allow_dependencies=False):
    if not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('冻结输入 ID 无效')
    from .file_cache import identity
    return copy.deepcopy(_bundle_cached(identity(ROOT/'inputs'/(key+'.zip')), key, allow_dependencies))


@lru_cache(maxsize=32)
def _bundle_cached(file_identity, key, allow_dependencies):
    raw=Path(file_identity[0]).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=key:raise ValueError('冻结包 hash 校验失败')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        manifest=json.loads(z.read('manifest.json'))
        dependencies=manifest.get('dependencies') or []
        if manifest.get('errors'):
            raise ValueError('该冻结包存在输入错误，不能提交')
        bound = bool(manifest.get('source_contract', {}).get('schema') == 'fpilot.source-contract.v2' and manifest['source_contract']['mode'] != 'planned')
        if dependencies and not allow_dependencies and not bound:
            raise ValueError('该冻结包尚有未满足依赖，不能作为独立任务提交')
        if not dependencies and not manifest.get('ready') and not bound:
            raise ValueError('该冻结包未就绪，不能提交')
        allowed=('scf','relax') if not dependencies else ('relax','scf','dos','bands','vasp-wannier')
        if manifest.get('stage') not in allowed:
            raise ValueError('该节点尚不支持提交')
        poscar = 'POSCAR' if 'POSCAR' in manifest.get('files', {}) else 'POSCAR.reference'
        names = ('INCAR', 'KPOINTS', 'POTCAR', poscar)
        try:
            files={name:z.read(name) for name in names}
        except KeyError as exc:
            raise ValueError('冻结输入缺少必须文件') from exc
        for name,data in files.items():
            if hashlib.sha256(data).hexdigest()!=manifest['files'][name]['sha256']:raise ValueError('冻结输入文件校验失败')
    return manifest,files


def _restart_policy(manifest, files):
    policy = manifest.get('source_plan')
    if manifest.get('source_contract', {}).get('schema') == 'fpilot.source-contract.v2' and policy:
        init = policy['initialization']
        if any(_incar_value(files['INCAR'], name) != str(value) for name, value in init.items()):
            raise ValueError('冻结初始化参数与来源文件角色不一致')
        return {'schema': 'fpilot.vasp-restart.v2', 'stage': manifest['stage'], **init,
                'required_files': policy['required_files'], 'uses_wavecar': 'WAVECAR' in policy['required_files'],
                'note': '读取本次角色合同绑定的具体文件；不代表物理有效性。'}
    dependencies=manifest.get('dependencies') or []
    stage=manifest.get('stage')
    expected={'relax': {'CONTCAR'}, 'scf': {'CONTCAR', 'CHGCAR'} if any(item.get('file') == 'CHGCAR' for item in dependencies) else {'CONTCAR'}, 'dos': {'CONTCAR','CHGCAR'},
              'bands': {'CONTCAR','CHGCAR'}, 'vasp-wannier': {'CONTCAR','CHGCAR'}}.get(stage)
    actual={item.get('file') for item in dependencies}
    if not expected or len({item.get('node_id') for item in dependencies}) != 1:
        raise ValueError('依赖集不符合当前阶段的固定合同')
    if not manifest.get('source_contract') and actual!=expected:raise ValueError('依赖集不符合当前阶段的固定合同')
    if manifest.get('source_contract',{}).get('schema')=='fpilot.source-contract.v1':
        draft=manifest.get('source_draft',{})
        if stage in ('relax','scf') and draft.get('mode')=='upstream':
            expected={'CONTCAR','CHGCAR'} | ({'WAVECAR'} if draft.get('files')=='both' else set())
        elif stage in ('relax','scf') and draft.get('mode')=='new':expected={'CONTCAR'}
        if actual!=expected:raise ValueError('文件来源合同与阶段不一致')
    text=files['INCAR'].decode('utf-8')
    def tag(name):
        matches=re.findall(r'^\s*'+name+r'\s*=\s*([^!#\s]+)',text,re.I|re.M)
        return matches[-1] if matches else None
    expected_icharg='11' if stage in ('dos','bands','vasp-wannier') else '1' if 'CHGCAR' in expected else '2'
    expected_start='1' if 'WAVECAR' in expected else '0'
    if tag('ISTART') != expected_start or tag('ICHARG') != expected_icharg:
        raise ValueError('依赖策略必须显式为 ISTART=0 且 ICHARG='+expected_icharg)
    return {'schema':'fpilot.vasp-restart.v1','stage':stage,'ISTART':int(expected_start),'ICHARG':int(expected_icharg),
            'required_files':sorted(expected),'uses_wavecar':'WAVECAR' in expected,
            'note':'只复用经哈希复核的上游文件；不继承 WAVECAR，不代表科学有效性。'}


def _incar_value(raw, tag):
    matches = re.findall(r'^\s*'+re.escape(tag)+r'\s*=\s*([^!#\r\n]+)', raw.decode('utf-8'), re.I|re.M)
    return ' '.join(matches[-1].upper().split()) if matches else None


def _review_input_audit(audit, dependency=None, restart=None, stage=None):
    """Defer restart files supplied by a verified remote source; report real errors briefly."""
    errors = list(audit.get('errors') or [])
    remote_source = bool(restart or dependency and stage in ('scf', 'dos', 'bands', 'vasp-wannier'))
    if remote_source:
        supplied = {'CHGCAR'} if dependency or restart else set()
        if restart and restart.get('mode') in ('both', 'wave'):
            supplied.add('WAVECAR')
        if restart and restart.get('mode') == 'wave':
            supplied.discard('CHGCAR')
        deferred = [item for item in errors if any(
            item == f'{name} required by INCAR restart settings' for name in supplied)]
        if deferred:
            errors = [item for item in errors if item not in deferred]
            audit = {**audit, 'errors': errors, 'deferred_errors': deferred,
                     'dependency_snapshot': dependency, 'restart_snapshot': restart,
                     'note': '续算文件由已核对的远端来源物化；提交前再次复核哈希。',
                     'status': 'FAIL' if errors else 'PASS_WITH_REMOTE_DEPENDENCY'}
    if audit.get('status') not in ('PASS', 'PASSED', 'PASS_WITH_REMOTE_DEPENDENCY'):
        raise ValueError('提交前输入审查未通过：' + '；'.join(errors or ['审查状态异常'])[:1200])
    return audit


def source_file_plan(snapshot, incar):
    """Copy the small structure; hardlink large read-only inputs; isolate writable outputs."""
    if not snapshot:
        return {}
    false_values = {'F', 'FALSE', '.FALSE.'}
    charge_read_only = _incar_value(incar, 'LCHARG') in false_values
    wave_read_only = _incar_value(incar, 'LWAVE') in false_values
    result = {}
    manual_restart = (snapshot.get('schema') == 'fpilot.restart-snapshot.v1' or
                      snapshot.get('schema') == 'fpilot.role-source.v1' and snapshot.get('mode') in ('self','manual') or
                      snapshot.get('schema') == 'fpilot.chain-source.v1' and
                      str(snapshot.get('kind', '')).endswith('-restart'))
    for name, info in snapshot['files'].items():
        if snapshot.get('schema') == 'fpilot.role-source.v1' and info.get('usage') == 'geometry-reference' and name in ('POSCAR', 'CONTCAR'):
            continue  # verified baseline; the bundle contains the scaled POSCAR
        target = 'POSCAR' if name == 'CONTCAR' else name
        writable = (name == 'CHGCAR' and not charge_read_only or name == 'WAVECAR' and not wave_read_only
                    or manual_restart and name in ('CHGCAR', 'WAVECAR'))
        mode = 'copy' if name in ('CONTCAR', 'POSCAR') else 'reflink-or-copy' if writable else 'hardlink'
        result[name] = {**info, 'target': target, 'mode': mode}
    return result


def source_file_commands(snapshot, plan):
    source = remote_paths.shell_path(snapshot['source_path'])
    commands = []
    for name, item in sorted(plan.items()):
        origin, target = remote_paths.shell_path(item.get('source_path',snapshot['source_path'])) + '/' + name, shlex.quote(item['target'])
        mode = item['mode']
        if mode not in ('hardlink', 'reflink-or-copy', 'copy'):
            raise ValueError('依赖文件存储策略无效')
        from .source_files import guard
        commands.extend(guard(origin, item))
        commands.extend(['rm -f -- '+target,
            ('ln '+origin+' '+target if mode == 'hardlink' else
             'cp -- '+origin+' '+target if mode == 'copy' else
             'cp --reflink=auto '+origin+' '+target),
        ])
        commands.extend(guard(target, item, copied=True))
        if item.get('mtime'):
            commands.extend(guard(origin, item))
        if mode == 'hardlink':
            commands.append('test "$(stat -c %d:%i '+origin+')" = "$(stat -c %d:%i '+target+')"')
    return '\n'.join(commands) + ('\n' if commands else '')


def _restart_incar(raw, mode, start_override=None):
    start, charge = (1, 0) if mode == 'wave' else (1, 1) if mode == 'both' else (0, 1)
    if start_override is not None:
        start = start_override
    text = raw.decode('utf-8')
    for tag, value in [('ISTART', start), ('ICHARG', charge)]:
        pattern = re.compile(r'^\s*'+tag+r'\s*=.*$', re.I|re.M)
        text, count = pattern.subn(f'{tag} = {value}', text)
        if not count:
            text += f'{tag} = {value}\n'
    return text.encode('utf-8')


def chain_source_snapshot(bundle_id, manifest, source_id, alias, kind):
    if manifest.get('source_contract', {}).get('schema') == 'fpilot.source-contract.v2':
        return _v2_chain_source(bundle_id, manifest, source_id, alias, kind)
    return _legacy_chain_source(bundle_id, manifest, source_id, alias, kind)


def _v2_chain_source(bundle_id, manifest, source_id, alias, kind):
    from . import source_contracts as sources
    if kind not in ('dependency', 'scf-restart', 'relax-restart'):
        raise ValueError('计算链来源文件合同无效')
    source = get(source_id)
    if source.get('config', {}).get('alias') != alias or source.get('project_id') != frozen_project(bundle_id)['id']:
        raise ValueError('计算链来源须属于同一项目和主机')
    policy = copy.deepcopy(manifest['source_plan'])
    _, raw = bundle(bundle_id, allow_dependencies=True)
    report = {**manifest, 'files': {name: value.decode() for name, value in raw.items() if name != 'POTCAR'}}
    if kind != 'dependency':
        stage = kind.split('-', 1)[0]
        if stage != manifest['stage'] or source['node_id'] != manifest['node_id'] or source['state'] != 'COMPLETED':
            raise ValueError('续算来源与当前节点或已完成状态不符')
        start = sources.wave_start(stage, raw['INCAR'], continuing=stage == 'relax')
        policy.update(mode='self', parent_id=None, parent_stage=None, purpose='continue' if stage == 'relax' else 'charge',
                      structure_file='CONTCAR' if stage == 'relax' else 'POSCAR',
                      required_files=['CONTCAR', 'WAVECAR'] if stage == 'relax' else ['POSCAR', 'CHGCAR', 'WAVECAR'],
                      electronic_usage='none' if stage == 'relax' else 'initial',
                      initialization={'ISTART': start, 'ICHARG': 0 if stage == 'relax' else 1})
        report['files']['INCAR'] = _restart_incar(raw['INCAR'], 'wave' if stage == 'relax' else 'both', start).decode()
    policy['acknowledge_differences'] = True  # reviewed in this chain's authorization
    contract = sources.bind(policy, source, report, manifest.get('verification_mode', 'full'))
    roles = contract['roles']
    return {'schema': 'fpilot.chain-source.v1', 'source_attempt_id': source_id,
            'source_path': next(iter(roles.values()))['source_path'], 'source_state': source['state'],
            'source_job_id': source.get('job_id'), 'kind': kind,
            'verification_mode': contract['verification_mode'], 'source_contract': contract,
            'mode': 'wave' if kind == 'relax-restart' else 'both' if 'WAVECAR' in policy['required_files'] else 'charge',
            'initialization': policy['initialization'], 'actual_poscar': report['files']['POSCAR'],
            'files': {role['file']: role['metadata'] for role in roles.values()}}


def _legacy_chain_source(bundle_id, manifest, source_id, alias, kind):
    """Only file identity and attempt lineage are binding for an approved chain."""
    required = ({'dependency': sorted({item['file'] for item in manifest.get('dependencies', [])}),
                 'relax-restart': ['CONTCAR', 'WAVECAR'],
                 'scf-restart': ['CONTCAR', 'CHGCAR', 'WAVECAR']}).get(kind)
    if not required or any(name not in ('CONTCAR', 'CHGCAR', 'WAVECAR') for name in required):
        raise ValueError('计算链来源文件合同无效')
    if kind.endswith('-restart') and manifest.get('stage') != kind.split('-', 1)[0]:
        raise ValueError('续算阶段与冻结输入不符')
    source = get(source_id)
    if source.get('state') != 'COMPLETED' or source.get('path_schema') != 'fpilot.remote-attempt-path':
        raise ValueError('计算链来源不是已完成的受控 attempt')
    if source.get('config', {}).get('alias') != alias:
        raise ValueError('计算链依赖须在同一主机读取')
    if source.get('project_id') != frozen_project(bundle_id).get('id'):
        raise ValueError('计算链来源不属于同一项目')
    if kind == 'dependency' and source.get('node_id') not in {
            item.get('node_id') for item in manifest.get('dependencies', [])}:
        raise ValueError('来源 attempt 与冻结的直接上游不符')
    if kind.endswith('-restart') and source.get('node_id') != manifest.get('node_id'):
        raise ValueError('续算来源与当前节点不符')
    source_path = attempt_path(source)['shell']
    if manifest.get('verification_mode') in ('lightweight', 'full'):
        from .source_files import snapshot
        found = snapshot(alias, source_path, required, ssh, manifest['verification_mode'])
        return {'schema': 'fpilot.chain-source.v1', 'source_attempt_id': source_id,
                'source_path': source_path, 'source_state': source['state'],
                'source_job_id': source.get('job_id'), 'kind': kind,
                'verification_mode': manifest['verification_mode'],
                'mode': 'wave' if kind == 'relax-restart' else 'both' if kind == 'scf-restart' or 'WAVECAR' in required else None,
                'files': found}
    checks = '\n'.join("test -s {0}\nprintf 'FPILOT_CHAIN={0}|'\nsha256sum {0} | awk '{{printf $1\"|\"}}'\nwc -c < {0}".format(name) for name in required)
    receipt = ssh(alias, 'set -e\ncd ' + remote_paths.shell_path(source_path) + '\n' + checks, timeout=180)
    if receipt['exit_code'] != 0:
        raise ValueError('计算链来源文件不可读或为空')
    found = {m.group(1): {'sha256': m.group(2), 'size_bytes': int(m.group(3))}
             for m in re.finditer(r'^FPILOT_CHAIN=(CONTCAR|CHGCAR|WAVECAR)\|([a-f0-9]{64})\|(\d+)\s*$', receipt['stdout'], re.M)}
    if set(found) != set(required):
        raise ValueError('计算链来源文件哈希回执不完整')
    return {'schema': 'fpilot.chain-source.v1', 'source_attempt_id': source_id,
            'source_path': source_path, 'source_state': source['state'],
            'source_job_id': source.get('job_id'), 'kind': kind,
            'mode': 'wave' if kind == 'relax-restart' else 'both' if kind == 'scf-restart' or 'WAVECAR' in required else None,
            'files': found}


def restart_snapshot(bundle_id, manifest, files, source_id, alias, mode, reason):
    if mode not in ('both', 'charge') or not isinstance(reason, str) or len(reason) > 2000:
        raise ValueError('请选择 CHGCAR 或 WAVECAR+CHGCAR 续算')
    if manifest['stage'] not in ('relax', 'scf'):
        raise ValueError('当前仅 relax/SCF 支持从同节点续算')
    source = get(source_id)
    if source.get('path_schema') != 'fpilot.remote-attempt-path':
        raise ValueError('续算来源缺少可核对的远端 attempt 路径')
    if source.get('config',{}).get('alias') != alias:
        raise ValueError('续算文件须在当前主机可读；请选择同一主机的来源')
    identity = remote_paths.frozen_identity(manifest, frozen_project(bundle_id))
    warnings = []
    if source.get('state') != 'COMPLETED':
        warnings.append('来源未标记 COMPLETED；请自行核对程序结束与收敛')
    if (source.get('evidence') or {}).get('ediff_reached') is not True:
        warnings.append('来源没有已确认的电子收敛证据')
    if source.get('project_id') != identity['project_id']:
        warnings.append('来源与当前输入属于不同项目')
    old_files = {}
    try:
        old_manifest, old_files = bundle(source['bundle_id'], allow_dependencies=True)
    except (KeyError, OSError, ValueError):
        warnings.append('来源冻结输入不可读取；仅按远端文件哈希记录来源')
    else:
        if old_manifest.get('stage') not in ('relax', 'scf'):
            warnings.append('来源阶段不是 relax/SCF')
        if old_files.get('POTCAR') != files.get('POTCAR'):
            warnings.append('POTCAR 与来源不同')
        old_poscar = old_files.get('POSCAR', old_files.get('POSCAR.reference', b''))
        new_poscar = files.get('POSCAR', files.get('POSCAR.reference', b''))
        try:
            old_counts = old_poscar.decode('utf-8').splitlines()[6].split()
            new_counts = new_poscar.decode('utf-8').splitlines()[6].split()
            if old_counts != new_counts:
                warnings.append('结构物种顺序或原子数与来源不同')
        except (UnicodeError, IndexError):
            warnings.append('来源或当前结构物种计数不可读取')
        for tag in ('GGA','ISPIN','LDAU','LDAUTYPE','LDAUL','LDAUU','LDAUJ','LMAXMIX',
                    'ENCUT','PREC','LSORBIT','LNONCOLLINEAR','SAXIS','GGA_COMPAT'):
            old = _incar_value(old_files['INCAR'], tag)
            new = _incar_value(files['INCAR'], tag)
            if old != new:
                warnings.append(f'{tag} 不同：来源={old or "未设置"}，本次={new or "未设置"}')
        if mode == 'both' and old_files.get('KPOINTS') != files.get('KPOINTS'):
            warnings.append('KPOINTS 与来源不同，WAVECAR 可能无法读取')
    required = ['CHGCAR'] + (['WAVECAR'] if mode == 'both' else [])
    source_path = attempt_path(source)['shell']
    source_expr = remote_paths.shell_path(source_path)
    if manifest.get('verification_mode') in ('lightweight', 'full'):
        from .source_files import snapshot
        records = snapshot(alias, source_path, required, ssh, manifest['verification_mode'])
    else:
        checks = '\n'.join(f"test -s {name}\nprintf 'FPILOT_RESTART={name}|'\nsha256sum {name} | awk '{{printf $1\"|\"}}'\nwc -c < {name}" for name in required)
        receipt = ssh(alias, 'set -e\ncd '+source_expr+'\n'+checks, timeout=180)
        if receipt['exit_code'] != 0:
            raise ValueError('续算文件不可读或为空；未准备新 attempt')
        records = {}
        for match in re.finditer(r'^FPILOT_RESTART=(CONTCAR|CHGCAR|WAVECAR)\|([a-f0-9]{64})\|(\d+)\s*$', receipt['stdout'], re.M):
            records[match.group(1)] = {'sha256':match.group(2), 'size_bytes':int(match.group(3))}
        if set(records) != set(required):
            raise ValueError('续算文件哈希回执不完整')
    old_saxis = _incar_value(old_files['INCAR'], 'SAXIS') if old_files.get('INCAR') else None
    new_saxis = _incar_value(files['INCAR'], 'SAXIS')
    saxis_change = {'source':old_saxis, 'target':new_saxis} if old_saxis != new_saxis else None
    return {'schema':'fpilot.restart-snapshot.v1', 'source_attempt_id':source_id,
            'source_path':source_path, 'source_state':source['state'], 'files':records,
            'mode':mode, 'reason':reason.strip(), 'source_converged':bool((source.get('evidence') or {}).get('ediff_reached')),
            'source_bundle_id':source.get('bundle_id'), 'target_bundle_id':bundle_id,
            'compatibility_warnings':warnings, 'saxis_change':saxis_change}


def dependency_snapshot(bundle_id, manifest, files, upstream_attempt_id, alias, reason):
    if not upstream_attempt_id or not isinstance(reason,str) or len(reason) > 2000:
        raise ValueError('依赖任务必须绑定相连节点的上游 attempt')
    parent=get(upstream_attempt_id)
    if parent.get('path_schema') != 'fpilot.remote-attempt-path':
        raise ValueError('上游 attempt 目录结构不受支持')
    if parent.get('state') != 'COMPLETED':
        raise ValueError('上游 attempt 未以 COMPLETED 结束，依赖任务不推进')
    evidence=parent.get('evidence') or {}
    if evidence.get('wrapper_exit') not in (0, None) or not evidence.get('vasp_timing'):
        raise ValueError('上游缺少 VASP timing 或报告了非零退出，依赖任务不推进')
    if manifest['stage'] in ('dos','bands','vasp-wannier') and evidence.get('ediff_reached') is not True:
        raise ValueError('固定电荷下游需要已确认电子收敛的 SCF 来源')
    if not re.fullmatch(r'\d+(?:\.[A-Za-z0-9_.-]+)?',str(parent.get('job_id') or '')):
        raise ValueError('上游没有可核对的精确 job ID，依赖任务不推进')
    if parent.get('config',{}).get('alias') != alias:
        raise ValueError('依赖文件仅可在同一登记主机内复用')
    dependency_node=next(iter({item['node_id'] for item in manifest['dependencies']}))
    from .calculation_nodes import owner as attempt_owner
    if attempt_owner(parent) != dependency_node:
        raise ValueError('上游 attempt 与冻结包指定的依赖节点不一致')
    project=frozen_project(bundle_id)
    identity=remote_paths.frozen_identity(manifest,project)
    if parent.get('project_id') != identity['project_id'] or parent.get('flow_id') != identity['flow_id']:
        raise ValueError('上游 attempt 不属于同一项目和流程')
    parent_manifest,parent_files=bundle(parent['bundle_id'],allow_dependencies=True)
    allowed_parent = {'relax': ('relax',), 'scf': ('relax', 'scf'),
                      'dos': ('scf',), 'bands': ('scf',), 'vasp-wannier': ('scf',)}.get(manifest['stage'], ())
    if parent_manifest.get('stage') not in allowed_parent:
        raise ValueError('上游阶段不符合固定依赖链')
    compatibility_warnings=[]
    if parent_files['POTCAR'] != files['POTCAR']:
        compatibility_warnings.append('上游 POTCAR 与当前节点不同')
    if manifest['stage'] in ('dos','bands','vasp-wannier'):
        for tag in ('GGA','ISPIN','LDAU','LDAUTYPE','LDAUL','LDAUU','LDAUJ','LMAXMIX',
                    'LSORBIT','LNONCOLLINEAR','SAXIS','GGA_COMPAT'):
            if _incar_value(parent_files['INCAR'],tag) != _incar_value(files['INCAR'],tag):
                compatibility_warnings.append('上游 INCAR 与当前节点不同：'+tag)
    required=sorted({item['file'] for item in manifest['dependencies']})
    source=attempt_path(parent)['shell']
    source_expr=remote_paths.shell_path(source) if attempt_path(parent)['root'] else '"$HOME"/'+shlex.quote(parent['relative_path'])
    if manifest.get('verification_mode') in ('lightweight', 'full'):
        from .source_files import snapshot
        records = snapshot(alias, source, required, ssh, manifest['verification_mode'])
    else:
        checks='\n'.join(f"test -s {name}\nprintf 'FPILOT_DEP={name}|'\nsha256sum {name} | awk '{{printf $1\"|\"}}'\nwc -c < {name}" for name in required)
        receipt=ssh(alias,'set -e\ncd '+source_expr+'\n'+checks,timeout=30)
        if receipt['exit_code'] != 0:
            raise ValueError('上游依赖文件不可读或为空；没有推进')
        records={}
        for match in re.finditer(r'^FPILOT_DEP=(CONTCAR|CHGCAR)\|([a-f0-9]{64})\|(\d+)\s*$',receipt['stdout'],re.M):
            records[match.group(1)]={'sha256':match.group(2),'size_bytes':int(match.group(3))}
        if set(records) != set(required):
            raise ValueError('上游依赖回执不完整；没有推进')
    return {'schema':'fpilot.dependency-snapshot.v1','upstream_attempt_id':parent['id'],
            'upstream_job_id':parent.get('job_id'),'source_path':source,'source_path_absolute':parent.get('remote_path_absolute'),
            'source_node_id':parent['node_id'],'source_state':'COMPLETED','files':records,
            'reason':reason.strip(),'scientific_validation':evidence.get('scientific_validation','NOT_PERFORMED'),
            'compatibility_warnings':compatibility_warnings}
def frozen_project(key):
    if not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('冻结输入 ID 无效')
    from .file_cache import identity
    return copy.deepcopy(_frozen_project(identity(ROOT/'inputs'/(key+'.zip')), key))


@lru_cache(maxsize=64)
def _frozen_project(file_identity, key):
    """Read the small project identity record embedded in a frozen ZIP.

    ``POTCAR`` is intentionally not read here; path planning only needs the
    project graph identity and the manifest.  Older synthetic bundles may not
    contain ``project.json`` and are rejected by ``frozen_identity`` unless
    their manifest carries an explicit project ID.
    """
    if not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('冻结输入 ID 无效')
    raw = Path(file_identity[0]).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=key:raise ValueError('冻结包 hash 校验失败')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        try:
            return json.loads(archive.read('project.json'))
        except KeyError:
            return None


def attempt_path(record):
    """Return the only supported 当前 attempt path."""
    if record.get('path_schema') != 'fpilot.remote-attempt-path':
        raise ValueError('attempt 目录结构不受支持')
    root = record.get('remote_workspace_root')
    relative = record.get('remote_relative_path')
    if root and relative:
        root = remote_paths.normalize_workspace_root(root)
        relative = remote_paths.normalize_relative_path(relative)
        return {
            'root': root,
            'relative': relative,
            'shell': record.get('remote_path_absolute') or remote_paths.join_workspace(root, relative),
        }
    raise ValueError('当前 attempt 缺少冻结的工作区根目录或相对路径')


def path_preview(bundle_id,alias,remote_workspace_root=None):
    """Read-only layout preview, independent of scheduler and free capacity."""
    if alias not in servers.load_registry()['hosts']:
        raise ValueError('请选择登记的 SSH 别名')
    project=frozen_project(bundle_id)
    with zipfile.ZipFile(ROOT/'inputs'/(bundle_id+'.zip')) as archive:
        manifest=json.loads(archive.read('manifest.json'))
    identity=remote_paths.readable_identity(manifest,project)
    root=remote_paths.normalize_workspace_root(remote_workspace_root)
    receipt=ssh(alias,remote_paths.workspace_probe_command(root),timeout=30)
    snapshot=remote_paths.parse_workspace_probe(receipt,root)
    relative=remote_paths.attempt_relative_path(identity['project_slug'],identity['stage_sequence'],identity['stage_slug'],'YYYYMMDD-HHMMSS-XXXX')
    return {**identity,'alias':alias,'remote_workspace_root':root,'workspace':snapshot,
            'remote_path_absolute':remote_paths.absolute_attempt_path(snapshot['absolute_path'],relative),
            'remote_mutation':False,'path_schema':'fpilot.remote-attempt-path','note':'只读路径预览；时间与短后缀在准备执行时生成，不创建目录或提交任务。'}


def prepare(bundle_id,config,probe_id,node,cores=None,reason='',template_id=None,retry_of=None,
            dependency_attempt_id=None,dependency_reason='',project_id=None,
            restart_attempt_id=None,restart_mode=None,restart_reason='',attempt_id=None,
            chain_source_id=None,chain_source_kind=None,execution_scope=None):
    recycle.assert_available('inputs',bundle_id,{'project_id':project_id,'template_id':template_id,'source_attempt_ids':[v for v in (retry_of,dependency_attempt_id,restart_attempt_id,chain_source_id) if v]})
    if attempt_id is not None:
        recycle.assert_available('attempts',attempt_id)
        if not re.fullmatch(r'[a-f0-9]{32}',attempt_id): raise ValueError('attempt ID 无效')
        try: existing=get(attempt_id)
        except ValueError: existing=None
        if existing:
            if existing.get('bundle_id')!=bundle_id or existing.get('project_id')!=project_id or existing.get('config',{}).get('alias')!=config.get('alias') or existing.get('node')!=node:
                raise ValueError('确定性 case attempt ID 已被不同输入占用')
            return existing
    if (ROOT/'recycle'/'inputs'/bundle_id).exists():
        raise ValueError('冻结输入已移入回收区，请先恢复')
    if template_id and (ROOT/'recycle'/'job-templates'/template_id).exists():
        raise ValueError('提交脚本已移入回收区，请先恢复')
    project = frozen_project(bundle_id)
    if project_id is not None and (not project or project.get('id') != project_id):
        raise ValueError('冻结输入不属于当前项目')
    if project_id and recycle.inactive('projects',project_id):
        raise ValueError('当前项目已移入回收区')
    if retry_of:
        parent=get(retry_of)
        if parent.get('path_schema') != 'fpilot.remote-attempt-path':
            raise ValueError('attempt 目录结构不受支持，不能重试')
        if parent['state']!='FAILED' or parent['bundle_id']!=bundle_id or parent['config']!=config:
            raise ValueError('只可从已确认 FAILED 的原记录创建新 attempt；输入及配置必须沿用')
    from . import workbench_settings
    if template_id is None:
        template_id=servers.selected_template(config)
    if config.get('runtime_source')=='script' and template_id:
        config=servers.with_script(config,template_id)
    settings_snapshot=workbench_settings.snapshot(config['alias'],servers.program_name(config),config,template_id)
    rendered=servers.preview_for_prepare(config,probe_id,node,cores,reason)
    probe_id=rendered.get('probe_id',probe_id)
    if not rendered['resource_ready']:raise ValueError('资源检查未通过：'+'; '.join(rendered['blockers']))
    template_snapshot = None
    scheduler=servers.normalize_scheduler(config.get('scheduler'))
    if template_id:
        template_snapshot = job_templates.render_template(template_id,
            template_resources(rendered['profile']), config['alias'], config)
        if not template_snapshot['submission_supported']:
            raise ValueError('模板执行检查未通过：' + '; '.join(template_snapshot['execution_blockers']))
        rendered['script'] = template_snapshot['rendered_text']
    manifest,files=bundle(bundle_id,allow_dependencies=True)
    from . import source_contracts, branch_bindings
    frozen_scope=manifest.get('execution_scope')
    if not frozen_scope:
        from .scans import case_scopes
        legacy_scopes=case_scopes().get(bundle_id,[])
        if legacy_scopes and execution_scope not in legacy_scopes:
            raise ValueError('旧扫描 case 只能通过其冻结批次运行')
    if frozen_scope and frozen_scope != manifest['node_id'] and execution_scope != frozen_scope:
        raise ValueError('扫描 case 只能通过其冻结批次运行，不能作为基础节点的单任务提交')
    if frozen_scope and execution_scope and execution_scope != frozen_scope:
        raise ValueError('执行身份与冻结输入不一致')
    execution_scope=execution_scope or frozen_scope
    contract = manifest.get('source_contract')
    binding = branch_bindings.check(project,manifest['node_id'],config['alias'],contract,execution_scope)
    role_source = None
    restart = None
    chain_source = None
    if contract and contract.get('mode')=='planned' and manifest.get('dependencies') and not chain_source_id and not dependency_attempt_id:
        raise ValueError('此输入绑定计算链计划节点，请从冻结计算链运行')
    if contract and contract.get('schema') == 'fpilot.source-contract.v2' and contract.get('mode') == 'planned' and dependency_attempt_id:
        chain_source_id, chain_source_kind = dependency_attempt_id, 'dependency'
        dependency_attempt_id, dependency_reason = None, ''
    if contract and not chain_source_id:
        if dependency_attempt_id or restart_attempt_id or restart_mode or restart_reason:
            raise ValueError('计算来源已冻结，请返回计算配置修改；提交页不能覆盖来源')
        role_source = source_contracts.materialize(contract,config['alias'])
    elif chain_source_id:
        if restart_attempt_id or dependency_attempt_id or retry_of:
            raise ValueError('计算链来源不能与手动来源或重试混用')
        chain_source = chain_source_snapshot(bundle_id, manifest, chain_source_id, config['alias'], chain_source_kind)
        if chain_source.get('actual_poscar'):
            files.pop('POSCAR.reference', None)
            files['POSCAR'] = chain_source['actual_poscar'].encode()
        if manifest.get('stage') == 'bands' and chain_source_kind == 'dependency':
            structure_file = manifest.get('source_plan', {}).get('structure_file', 'CONTCAR')
            if (manifest.get('band_path_source_sha256') != chain_source['files'].get(structure_file, {}).get('sha256') or
                    manifest.get('band_path_source_attempt_id') != chain_source_id):
                raise ValueError('能带路径与本次 SCF 实际结构来源不一致')
        if chain_source_kind in ('relax-restart', 'scf-restart'):
            files['INCAR'] = _restart_incar(files['INCAR'], 'wave' if chain_source_kind == 'relax-restart' else 'both',
                                           chain_source.get('initialization', {}).get('ISTART'))
        restart = chain_source if chain_source_kind != 'dependency' else None
    elif restart_attempt_id:
        restart = restart_snapshot(bundle_id,manifest,files,restart_attempt_id,config['alias'],restart_mode,restart_reason)
        files['INCAR'] = _restart_incar(files['INCAR'],restart_mode)
    elif manifest.get('restart_required'):
        raise ValueError('此冻结输入指定 ICHARG=1；请先选择同项目、同服务器的兼容 CHGCAR 来源')
    elif restart_mode or restart_reason:
        raise ValueError('续算参数缺少来源 attempt')
    validate_parallel(files.get('INCAR', b'').decode('utf-8', errors='replace'),
                      rendered['profile'].get('mpi_ranks', rendered['profile'].get('ppn', rendered['profile'].get('ntasks'))),
                      gpu=rendered['profile'].get('resource_mode') == 'gpu')
    source_contracts.validate_wave_parallel(chain_source.get('source_contract') if chain_source else contract, files['INCAR'],
                                            rendered['profile'].get('mpi_ranks', rendered['profile'].get('ppn', rendered['profile'].get('ntasks'))),
                                            gpu=rendered['profile'].get('resource_mode') == 'gpu')
    restart_policy=None
    dependency=None
    if manifest.get('dependencies') and not restart and not chain_source and not contract:
        if retry_of:
            raise ValueError('依赖任务失败后必须重新选择并复核上游，不沿用独立任务重试入口')
        restart_policy=_restart_policy(manifest,files)
        dependency=dependency_snapshot(bundle_id,manifest,files,dependency_attempt_id,config['alias'],dependency_reason)
        if manifest['stage'] == 'bands':
            source_hash = manifest.get('band_path_source_sha256')
            final_hash = dependency['files']['CONTCAR']['sha256']
            source_attempt = manifest.get('band_path_source_attempt_id')
            if source_hash != final_hash or source_attempt and source_attempt != dependency_attempt_id:
                raise ValueError('能带路径尚未依据该 SCF 最终 CONTCAR 生成：请读取该文件、重新生成或编辑路径并冻结输入；未准备提交')
    elif (dependency_attempt_id or dependency_reason) and not restart and not chain_source:
        raise ValueError('该冻结输入没有图依赖；若要读取上游 CHGCAR，请在“续算来源”中选择 attempt')
    ncl_required=any(_incar_value(files['INCAR'],tag) in ('.TRUE.','TRUE','T') for tag in ('LSORBIT','LNONCOLLINEAR'))
    executable_name=PurePosixPath(config['vasp_executable']).name
    if ncl_required and executable_name!='vasp_ncl':
        raise ValueError('非共线/SOC 输入必须明确选择 vasp_ncl 运行方式/脚本；不会自动替换程序命令')
    if executable_name not in ('vasp_std','vasp_ncl'):
        raise ValueError('当前 VASP 程序应明确选择 vasp_std 或 vasp_ncl')
    with tempfile.TemporaryDirectory() as temp:
        for name,data in files.items():
            target='POSCAR' if name=='POSCAR.reference' else name
            (Path(temp)/target).write_bytes(data)
        audit=servers.pbs.audit_inputs(temp,profile='relax' if manifest['stage']=='relax' else 'static',executable=executable_name)
    audit=_review_input_audit(audit,dependency or (chain_source if chain_source_kind == 'dependency' else None),
                              ({**role_source,'mode':'both' if 'WAVECAR' in role_source['files'] else 'charge'} if role_source else restart or (chain_source if chain_source and 'WAVECAR' in chain_source['files'] else None)),manifest['stage'])
    if rendered['profile'].get('resource_mode') == 'gpu' and _incar_value(files['INCAR'], 'NCORE') not in (None, '', '1'):
        audit.setdefault('warnings', []).append('GPU 模式按 VASP OpenACC 的 NCORE=1 检查 MPI/KPAR；原 INCAR 未修改，实际 GPU 程序能力仍需核对')
    storage_source = role_source or chain_source or restart or dependency
    storage_plan = source_file_plan(storage_source, files['INCAR'])
    identity = remote_paths.readable_identity(manifest, project)
    with closing(db()) as identity_db:
        prior_attempts = [json.loads(row[0]) for row in identity_db.execute('SELECT body FROM attempts')]
    for prior in prior_attempts:
        if prior.get('path_schema') != 'fpilot.remote-attempt-path' or prior.get('project_id') != identity['project_id']:
            continue
        # Local preparation cannot permanently claim a remote directory name.
        never_submitted = (prior.get('state') in ('PREPARED', 'FAILED') and
                           not prior.get('job_id') and
                           (prior.get('not_dispatched') is True or
                            (prior.get('state') == 'PREPARED' and
                             not prior.get('dispatch_started_at') and not prior.get('dispatch_receipt'))))
        if never_submitted:
            continue
        if prior.get('project_slug') != identity['project_slug']:
            raise ValueError('该项目已有远端派发记录，远程项目标识已锁定')
        if prior.get('node_id') == identity['node_id'] and (
            prior.get('stage_sequence') != identity['stage_sequence'] or
            prior.get('stage_slug') != identity['stage_slug']
        ):
            raise ValueError('该节点已产生 当前 attempt，远程阶段标识已锁定')
    root = remote_paths.normalize_workspace_root(config.get('remote_workspace_root'))
    workspace_receipt = ssh(config['alias'], remote_paths.workspace_probe_command(root), timeout=30)
    snapshot = remote_paths.parse_workspace_probe(workspace_receipt, root)
    key=attempt_id or uuid.uuid4().hex
    attempt_segment=datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')+'-'+key[:4]
    relative = remote_paths.attempt_relative_path(identity['project_slug'],identity['stage_sequence'],identity['stage_slug'],attempt_segment)
    remote_path = remote_paths.join_workspace(root, relative)
    absolute_path = remote_paths.absolute_attempt_path(snapshot['absolute_path'], relative)
    artifact_names = (
        'INCAR', 'POSCAR', 'KPOINTS', 'POTCAR', script_name(scheduler),
        '_fpilot/attempt.json', '_fpilot/materialization.json',
        '_fpilot/dependency.json',
        'input-manifest.sha256', 'OUTCAR', 'vasp.out', 'vasp.err',
        'CONTCAR', 'OSZICAR', 'CHGCAR', 'WAVECAR', 'vasprun.xml',
        '.job-id', '.exit-status',
    )
    artifact_paths = {name: absolute_path + '/' + name for name in artifact_names}
    script=with_exit_trap(rendered['script'])
    stored_config = dict(config)
    stored_config['remote_workspace_root'] = root
    record={'id':key,'state':'PREPARED','created_at':now(),'bundle_id':bundle_id,'config':stored_config,
            'probe_id':probe_id,'node':node,'cores':cores,'reason':reason,'profile':rendered['profile'],
            'project_id':identity['project_id'],'flow_id':identity['flow_id'],'node_id':identity['node_id'],
            'project_slug':identity['project_slug'],'flow_slug':identity['flow_slug'],
            'stage_sequence':identity['stage_sequence'],'stage_slug':identity['stage_slug'],
            'attempt_segment':attempt_segment,
            'remote_workspace_root':root,'remote_workspace_root_abs':snapshot['absolute_path'],
            'remote_workspace_snapshot':snapshot,'remote_relative_path':relative,
            'relative_path':relative,'remote_path':remote_path,'remote_path_absolute':absolute_path,
            'remote_host':config['alias'],'artifact_paths':artifact_paths,
            'remote_project_path_absolute':snapshot['absolute_path'].rstrip('/')+'/material-projects/'+identity['project_slug'],
            'path_schema':'fpilot.remote-attempt-path','script':script,'input_audit':audit,
            'source_file_plan':storage_plan,'source_contract':contract, 'role_source_snapshot':role_source,
            'final_incar': files['INCAR'].decode(),
            'effective_input': {'schema': 'fpilot.effective-input.v1',
                                'files': {('POSCAR' if name == 'POSCAR.reference' else name):
                                          {'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)}
                                          for name, raw in files.items()},
                                'initialization': {name: int(value) if value is not None else None for name in ('ISTART', 'ICHARG') for value in [_incar_value(files['INCAR'], name)]},
                                'source_files': storage_plan},
            'execution_scope':execution_scope or identity['node_id'],'branch_scopes':binding['scopes'],
            'settings_snapshot':settings_snapshot,
            'script_sha256':hashlib.sha256(script.encode()).hexdigest(),'job_id':None,'scheduler':scheduler,
            'note':'准备完成尚未上传；点击提交授权仅此 attempt 一次 {}。'.format('sbatch' if scheduler=='slurm' else 'qsub')}
    if contract and contract.get('roles') and manifest.get('dependencies'):
        first=next(iter(contract['roles'].values()))
        record['dependency_snapshot']={'schema':'fpilot.dependency-snapshot.v1','upstream_attempt_id':first['attempt_id'],'source_path':first['source_path'],'source_node_id':first['node_id'],'files':role_source['files'],'reason':'冻结来源合同','scientific_validation':'NOT_PERFORMED'}
    if dependency:
        record['dependency_snapshot']=dependency
        record['restart_policy']=restart_policy
        record['note']='依赖快照和显式重启参数已冻结；尚未上传，提交前会再次核对上游哈希。'
    if restart and not chain_source:
        record['restart_snapshot']=restart
        record['restart_policy']={'ISTART':1 if restart_mode=='both' else 0,'ICHARG':1,
                                  'required_files':sorted(restart['files']),'uses_wavecar':restart_mode=='both',
                                  'note':'只读文件使用硬链接；可能写出的 CHGCAR/WAVECAR 使用独立文件。'}
        record['final_incar']=files['INCAR'].decode('utf-8')
        record['note']='续算来源与文件哈希已冻结；提交前将再次核对。'
    if chain_source:
        record['chain_source_snapshot'] = chain_source
        if chain_source_kind.endswith('-restart'):
            record['restart_policy'] = {'ISTART': int(_incar_value(files['INCAR'], 'ISTART')), 'ICHARG': 0 if chain_source_kind == 'relax-restart' else 1,
                                        'required_files': sorted(chain_source['files']), 'uses_wavecar': True}
            record['final_incar'] = files['INCAR'].decode('utf-8')
        else:
            record['restart_policy'] = _restart_policy(manifest, files)
        record['note'] = '计算链来源文件已核对并冻结哈希；提交前再次复核。'
    if template_snapshot:
        record['template_snapshot'] = template_snapshot
    for info in storage_plan.values():
        if info['target'] == 'POSCAR':
            record['effective_input']['files']['POSCAR'] = {k: info[k] for k in ('sha256', 'size_bytes')}
    if retry_of:record['retry_of']=retry_of
    with closing(db()) as c:
        from .execution_control import journal
        c.execute('INSERT INTO attempts VALUES(?,?,?)',(key,'PREPARED',json.dumps(record,ensure_ascii=False)))
        journal(c,key,'PREPARED',{'retry_of':retry_of});c.commit()
    return record


def ssh(alias,command,data=None,timeout=90,stdout_limit=524288,mutating=None):
    if os.environ.get('FPILOT_SSH_MODE', 'session') == 'session':
        from .remote_transport import run
        return run(alias,command,data,timeout,stdout_limit,mutating=mutating)
    args=['ssh',*ssh_multiplex_options(alias),'-o','BatchMode=yes','-o','ConnectTimeout=8','-o','ServerAliveInterval=10','-o','ServerAliveCountMax=2',
          '-o','StrictHostKeyChecking=yes',alias,command]
    try:
        r=subprocess.run(args,input=data,capture_output=True,timeout=timeout)
        return {'exit_code':r.returncode,'stdout':r.stdout[-stdout_limit:].decode(errors='replace'),
                'stdout_truncated':len(r.stdout)>stdout_limit,'stderr':r.stderr.decode(errors='replace')[-32768:]}
    except subprocess.TimeoutExpired:
        return {'exit_code':None,'stdout':'','stderr':'SSH 超时，提交结果不确定，禁止自动重投'}


def ssh_multiplex_options(alias):
    """Reuse a connection briefly without changing the user's SSH config."""
    from .platform_runtime import ssh_options
    return ssh_options(alias)


def pbs_history_command(job_id):
    """A bounded, exact-job fallback after qstat has aged out the job."""
    job = shlex.quote(job_id)
    return (f'''if qstat -f {job} 2>/dev/null; then :; else
  if command -v tracejob >/dev/null 2>&1; then
    trace=$(tracejob -n 30 {job} 2>/dev/null || true)
    if printf '%s\\n' "$trace" | grep -Fq 'Job: {job_id}'; then
      printf 'FPILOT_PBS_TRACE_EXIT='
      printf '%s\\n' "$trace" | sed -n -E 's/.*Exit_status=([0-9]+).*/\\1/p' | tail -1
      printf 'FPILOT_PBS_TRACE_COMPLETE='
      printf '%s\\n' "$trace" | grep -c 'dequeuing from .*state COMPLETE' || true
    fi
  fi
fi
''')


def pbs_history_evidence(output):
    exit_match = re.search(r'^FPILOT_PBS_TRACE_EXIT=(\d+)$', output, re.M)
    complete_match = re.search(r'^FPILOT_PBS_TRACE_COMPLETE=([1-9]\d*)$', output, re.M)
    return (int(exit_match.group(1)) if exit_match else None, bool(complete_match))


def recovered_job_command(scheduler):
    """Query the original receipt in the same read, with no shell evaluation."""
    command="""if test -s .job-id; then
  fpilot_job=$(head -c 256 .job-id | awk '/^[0-9]+([.][A-Za-z0-9_.-]+)?(;[A-Za-z0-9_.-]+)?$/ {sub(/;.*/, ""); print; exit}')
  if test -n "$fpilot_job"; then
    printf 'FPILOT_JOB_ID=%s\\n' "$fpilot_job"
"""
    if scheduler=='pbs':
        command+='''    if qstat -f "$fpilot_job" 2>/dev/null; then :; else
      if command -v tracejob >/dev/null 2>&1; then
        trace=$(tracejob -n 30 "$fpilot_job" 2>/dev/null || true)
        if printf '%s\\n' "$trace" | grep -Fq "Job: $fpilot_job"; then
          printf 'FPILOT_PBS_TRACE_EXIT='
          printf '%s\\n' "$trace" | sed -n -E 's/.*Exit_status=([0-9]+).*/\\1/p' | tail -1
          printf 'FPILOT_PBS_TRACE_COMPLETE='
          printf '%s\\n' "$trace" | grep -c 'dequeuing from .*state COMPLETE' || true
        fi
      fi
    fi
'''
    else:
        command+='''    squeue -h -j "$fpilot_job" -o 'FPILOT_SLURM_LIVE=%T' || true
    sacct -n -P -j "$fpilot_job" --format=JobIDRaw,State,ExitCode 2>/dev/null | awk -F'|' -v job="$fpilot_job" '$1 == job {print "FPILOT_SLURM_ACCOUNT="$2"|"$3; exit}' || true
'''
    return command+'  fi\nfi\n'


def start_watcher(key):
    record=get(key)
    try:
        with (ROOT/(key+'.watch.log')).open('ab') as log:
            process=subprocess.Popen([sys.executable,str(Path(__file__).resolve().parents[1]/'scripts/watch_execution.py'),key],
                stdout=log,stderr=log,start_new_session=True,env={**os.environ,'FPILOT_WORKSPACE':str(ROOT)})
        record=get(key);record['watcher_pid']=process.pid;save(record)
    except OSError as exc:
        record=get(key);record['watcher_error']=str(exc);save(record)


def project_directory_guard(record):
    """Protect an existing project's namespace and return an actionable blocker."""
    from .remote_transport import _interpreter
    project_dir = remote_paths.shell_path(record['remote_project_path_absolute'])
    identity = {'schema': 'fpilot.remote-project.v1', 'project_id': record.get('project_id'),
                'project_slug': record.get('project_slug')}
    identity_json = shlex.quote(json.dumps(identity, ensure_ascii=False, separators=(',', ':')))
    check = """import json,sys
expected=json.loads(sys.argv[1])
try:
    with open(sys.argv[2]) as handle: actual=json.load(handle)
    matches=isinstance(actual,dict) and all(actual.get(key)==value for key,value in expected.items())
except (OSError,ValueError): matches=False
if not matches:
    print('FPILOT_PROJECT_CONFLICT=1')
    print('远端项目目录已属于其他项目或归属记录无效；请更换远端项目标识后重新冻结。没有提交任务，也没有修改原目录。',file=sys.stderr)
    sys.exit(1)
"""
    interpreter = shlex.quote(_interpreter(record['config']['alias']))
    return f'''project_dir={project_dir}
if test -d "$project_dir"; then
  if test -f "$project_dir/.fpilot/project.json"; then
    {interpreter} -c {shlex.quote(check)} {identity_json} "$project_dir/.fpilot/project.json"
  elif ! test -z "$(find "$project_dir" -mindepth 1 -maxdepth 1 -print -quit)"; then
    printf 'FPILOT_PROJECT_CONFLICT=1\n'
    printf '远端项目目录非空且没有归属记录；请使用独立的远端项目标识后重新冻结。没有提交任务。\n' >&2
    exit 1
  fi
else
  mkdir -p "$project_dir"
fi
mkdir -p "$project_dir/.fpilot"
if ! test -e "$project_dir/.fpilot/project.json"; then printf '%s' {identity_json} > "$project_dir/.fpilot/project.json"; fi
'''


from .attempt_operations import serialized_submit


@serialized_submit
def submit(key):
    from . import execution_control as control
    record=get(key)
    recycle.assert_available('attempts',key,record)
    if record.get('kind') == 'wannier90-plot':
        from . import wannier_plot_restart
        return wannier_plot_restart.submit(key)
    if record.get('kind') == 'wannier90':
        from . import wannier_execution
        return wannier_execution.submit(key)
    if record['state']!='PREPARED':return record
    from .workbench_settings import assert_current
    assert_current(record.get('settings_snapshot'))
    # Reject a locally changed frozen script before any remote read.
    snapshot = record.get('template_snapshot')
    if snapshot and (record['script'] != with_exit_trap(snapshot['rendered_text'])
                     or hashlib.sha256(record['script'].encode()).hexdigest() != record['script_sha256']):
        raise ValueError('冻结模板脚本不一致；没有提交')
    # Fresh resource probe before every dispatch; unchanged rendered resources required.
    from . import fast_dispatch
    fast=fast_dispatch.enabled(record)
    fresh={'id':record['probe_id']} if fast else servers.capacity_probe(record['config'])
    checked_config = record['config']
    if not record.get('template_snapshot') and not checked_config.get('runtime_source'):
        checked_config = {**checked_config, 'runtime_source': 'registered'}
    checked={'resource_ready':True,'profile':record['profile']} if fast else servers.preview(checked_config,fresh['id'],record['node'],record['cores'],record['reason'])
    if not checked['resource_ready'] or checked['profile']!=record['profile']:
        raise ValueError('实时资源已变化或不可用，请重新准备；没有提交')
    if record.get('template_snapshot'):
        snapshot = record['template_snapshot']
        fresh_template = job_templates.render_template(snapshot['id'],
            template_resources(checked['profile']), record['config']['alias'], record['config'])
        if (not fresh_template['submission_supported'] or fresh_template['original_hash'] != snapshot['original_hash']
                or fresh_template['rendered_text'] != snapshot['rendered_text']):
            raise ValueError('模板或运行时已变化；没有提交')
        expected_script = with_exit_trap(fresh_template['rendered_text'])
        if record['script'] != expected_script or hashlib.sha256(record['script'].encode()).hexdigest() != record['script_sha256']:
            raise ValueError('冻结模板脚本不一致；没有提交')
    manifest,files=bundle(record['bundle_id'],allow_dependencies=True)
    from . import source_contracts, branch_bindings
    if not fast and record.get('source_contract') and not record.get('chain_source_snapshot'):
        fresh_role=source_contracts.materialize(record['source_contract'],record['config']['alias'])
        if fresh_role != record.get('role_source_snapshot'):
            raise ValueError('冻结来源发生变化；没有提交')
    if record.get('chain_source_snapshot'):
        snap=record['chain_source_snapshot']
        fresh_chain=snap if fast else chain_source_snapshot(record['bundle_id'],manifest,snap['source_attempt_id'],
                                          record['config']['alias'],snap['kind'])
        if any(fresh_chain.get(k)!=snap.get(k) for k in ('source_attempt_id','source_path','files','kind')):
            raise ValueError('计算链来源文件已变化；没有提交')
        if snap['kind'].endswith('-restart'):
            files['INCAR']=_restart_incar(files['INCAR'],'wave' if snap['kind']=='relax-restart' else 'both',snap.get('initialization',{}).get('ISTART'))
            if files['INCAR'].decode('utf-8')!=record.get('final_incar'):
                raise ValueError('计算链续算 INCAR 与准备记录不一致')
    if record.get('restart_snapshot'):
        snap=record['restart_snapshot']
        fresh_restart=snap if fast else restart_snapshot(record['bundle_id'],manifest,files,snap['source_attempt_id'],
                                       record['config']['alias'],snap['mode'],snap['reason'])
        if any(fresh_restart.get(k)!=snap.get(k) for k in ('source_attempt_id','source_path','files','mode')):
            raise ValueError('续算来源文件或状态已变化；没有提交')
        files['INCAR']=_restart_incar(files['INCAR'],snap['mode'])
        if files['INCAR'].decode('utf-8')!=record.get('final_incar'):
            raise ValueError('续算最终 INCAR 与准备记录不一致；没有提交')
    if record.get('chain_source_snapshot', {}).get('actual_poscar'):
        files.pop('POSCAR.reference', None)
        files['POSCAR'] = record['chain_source_snapshot']['actual_poscar'].encode()
    validate_parallel(files.get('INCAR', b'').decode(), record['profile'].get('mpi_ranks', record['profile'].get('ppn', record['profile'].get('ntasks'))),
                      gpu=record['profile'].get('resource_mode') == 'gpu')
    source_contracts.validate_wave_parallel(record.get('chain_source_snapshot', {}).get('source_contract') or record.get('source_contract'), files.get('INCAR', b''),
                                            record['profile'].get('mpi_ranks', record['profile'].get('ppn', record['profile'].get('ntasks'))),
                                            gpu=record['profile'].get('resource_mode') == 'gpu')
    if not fast and record.get('dependency_snapshot') and not record.get('source_contract'):
        fresh_dependency=dependency_snapshot(record['bundle_id'],manifest,files,
            record['dependency_snapshot']['upstream_attempt_id'],record['config']['alias'],
            record['dependency_snapshot']['reason'])
        stable_fields=('upstream_attempt_id','upstream_job_id','source_path','source_node_id','source_state','files')
        if any(fresh_dependency.get(field) != record['dependency_snapshot'].get(field) for field in stable_fields):
            raise ValueError('上游依赖文件或证据已变化；没有提交，请重新准备')
    with closing(db()) as c:
        c.execute('BEGIN IMMEDIATE')
        current=c.execute('SELECT state FROM attempts WHERE id=?',(key,)).fetchone()
        if not current or current[0]!='PREPARED':c.rollback();return get(key)
        recycle.assert_available('attempts',key,record)
        control.enforce_limits(c,record)
        branch_bindings.reserve(c,record)
        changed=c.execute('UPDATE attempts SET state=? WHERE id=? AND state=?',('DISPATCHING',key,'PREPARED')).rowcount
        if not changed:c.rollback();return get(key)
        fast_dispatch.clear_previous_dispatch(record)
        dispatch_timeout=900 if any(item['mode']=='reflink-or-copy' for item in record.get('source_file_plan',{}).values()) else 180
        record.update(state='DISPATCHING',dispatch_started_at=now(),final_probe_id=fresh['id'],dispatch_timeout_seconds=dispatch_timeout)
        control.journal(c,key,'DISPATCHING',{'node':record['node'],'cores':record['profile'].get('ppn',record['profile'].get('ntasks'))})
        c.execute('UPDATE attempts SET body=? WHERE id=?',(json.dumps(record,ensure_ascii=False),key));c.commit()
    scheduler=scheduler_name(record)
    job_script=script_name(scheduler)
    files[job_script]=record['script'].encode()
    binding={k:record[k] for k in ('project_id','flow_id','node_id','remote_workspace_root','remote_path_absolute') if k in record}
    files['_fpilot/attempt.json']=json.dumps({'schema':'fpilot.web-attempt.v1','id':key,'stage':manifest['stage'],'bundle_id':record['bundle_id'],**binding,**{k:record[k] for k in ('scan_batch_id','scan_case_id','scan_case_table_hash','chain_run_id','chain_step_id','chain_plan_hash') if k in record}}).encode()
    files['_fpilot/materialization.json']=json.dumps({'schema':'fpilot.web-materialization.v1','input_audit':record['input_audit'],'config':record['config'],'script_sha256':record['script_sha256'],'template_snapshot':record.get('template_snapshot'),'restart_policy':record.get('restart_policy'),'restart_snapshot':record.get('restart_snapshot'),'dependency_snapshot':record.get('dependency_snapshot'),'chain_source_snapshot':record.get('chain_source_snapshot'),'source_file_plan':record.get('source_file_plan',{}),'source_contract':record.get('source_contract'),'effective_input':record.get('effective_input'),**binding}).encode()
    if record.get('dependency_snapshot'):
        files['_fpilot/dependency.json']=json.dumps(record['dependency_snapshot'],ensure_ascii=False,sort_keys=True).encode()
    files['input-manifest.sha256']=''.join(hashlib.sha256(data).hexdigest()+'  '+name+'\n' for name,data in files.items()).encode()
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as archive:
        for name,data in files.items():
            info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o600;archive.addfile(info,io.BytesIO(data))
    source_snapshot = record.get('role_source_snapshot') or record.get('chain_source_snapshot') or record.get('restart_snapshot') or record.get('dependency_snapshot')
    expected_plan = source_file_plan(source_snapshot, files['INCAR'])
    if source_snapshot and 'source_file_plan' not in record:
        raise ValueError('此任务按旧版复制策略准备；请重新准备，确认新的链接/写入策略后再提交')
    if expected_plan != record.get('source_file_plan', {}):
        raise ValueError('上游文件的链接/写入策略与准备时不同，没有提交')
    dependency_commands = source_file_commands(source_snapshot, expected_plan) if source_snapshot else ''
    paths = attempt_path(record)
    root_expr = remote_paths.shell_path(paths['root'])
    destination = remote_paths.shell_path(paths['shell'])
    parent = shlex.quote(str(PurePosixPath(record['remote_path_absolute']).parent))
    expected = shlex.quote(record['remote_workspace_root_abs'])
    project_guard=project_directory_guard(record)
    submit_command='sbatch --parsable '+job_script if scheduler=='slurm' else 'qsub '+job_script
    command=f'''set -e
root={root_expr}
{remote_paths.workspace_dispatch_guard(paths['root'],record['remote_workspace_root_abs'])}
{project_guard} 
dest={destination}
parent={parent}
mkdir -p "$parent"
mkdir "$dest"
tar -xf - -C "$dest"
cd "$dest"
sha256sum -c input-manifest.sha256
{dependency_commands}test -s POSCAR
mkdir .submit-once
{submit_command} > .job-id
printf 'FPILOT_JOB_ID='
cat .job-id
'''
    if fast:
        command=fast_dispatch.prefix(record)+command.replace('mkdir "$dest"','mkdir "$dest"\nfpilot_created=1').replace(submit_command+' > .job-id','fpilot_submitting=1\n'+submit_command+' > .job-id')
    try:
        receipt=ssh(record['config']['alias'],command,stream.getvalue(),timeout=dispatch_timeout)
        if (fast or receipt.get('transport_state')=='NOT_SENT') and fast_dispatch.known_not_submitted(record,receipt):return get(key)
        token_pattern=r'\d+(?:\.[A-Za-z0-9_.-]+)?' if scheduler=='pbs' else r'\d+(?:;[A-Za-z0-9_.-]+)?'
        match=re.search(r'^FPILOT_JOB_ID=('+token_pattern+r')\s*$',receipt['stdout'],re.M)
        job_id=match.group(1).split(';',1)[0] if match else None
        record.update(dispatch_receipt=receipt,job_id=match.group(1) if match else None,
                      state='SUBMITTED' if match and receipt['exit_code']==0 else 'UNKNOWN',updated_at=now())
        record['job_id']=job_id
        fast_dispatch.tracking_note(record)
        save(record)
        result=refresh(key) if record['job_id'] and not fast else record
        # The single local worker owns subsequent checks; a second per-attempt
        # watcher would duplicate every remote status query.
        return get(key)
    except BaseException:
        record['state']='UNKNOWN';fast_dispatch.tracking_note(record);save(record);raise


def assert_recovery_target(record):
    saved=record.get('settings_snapshot') or {}
    if saved.get('connection'):
        from .site_registration import connection_identity
        from .project_sync import continuation_connection
        if not continuation_connection(saved['connection'],connection_identity(record['config']['alias']),record['config']['alias']):
            raise ValueError('实际 SSH 连接目标已改变；请恢复原服务器连接后核查原作业，不会重投')


def refresh(key):
    if recycle.inactive('attempts',key,get(key)):return get(key)
    record=get(key)
    if record.get('cancellation', {}).get('state') in ('IN_FLIGHT', 'ACCEPTED', 'UNKNOWN'):
        from .execution_cancel import recover
        recover(record['project_id'], key)
        return get(key)
    if record['state'] == 'CANCELLED': return record
    if record.get('kind') in ('wannier90', 'wannier90-plot'):
        from . import wannier_execution
        return wannier_execution.refresh(key)
    if record['state'] in ('PREPARED','FAILED'):return record
    if record['state']=='COMPLETED':
        terminal={'pbs':'C','slurm':'COMPLETED'}[scheduler_name(record)]
        observed=(record.get('evidence') or {}).get('scheduler_state')
        # Older immutable terminal records did not always persist scheduler
        # state; preserve them rather than attempting a new remote read.
        if observed is None or observed == terminal:
            return record
    expected_updated_at=record.get('updated_at')
    assert_recovery_target(record)
    paths = attempt_path(record)
    path = remote_paths.shell_path(paths['shell'])
    command=f'''if ! test -d {path}; then printf 'FPILOT_ATTEMPT_MISSING=1\\n'; exit 3; fi
cd {path} || exit 3
if test -s .job-id; then printf 'FPILOT_JOB_ID='; cat .job-id; fi
if test -s .exit-status; then printf 'FPILOT_EXIT='; cat .exit-status; fi
if test -f OUTCAR; then
  printf 'FPILOT_TIMING='; grep -c 'General timing and accounting informations' OUTCAR || true
  printf 'FPILOT_EDIFF='; grep -c 'aborting loop because EDIFF is reached' OUTCAR || true
  printf 'FPILOT_IONIC='; awk '/Iteration[[:space:]]+[0-9]+[[:space:]]*\\([[:space:]]*[0-9]+[[:space:]]*\\)/{{converged=0}} /reached required accuracy/{{converged=1}} END{{print converged+0}}' OUTCAR
fi
if test -f vasp.out; then
  printf 'FPILOT_MPI_SINGLETON='; grep -c 'PMI server not found' vasp.out || true
fi
if test -f INCAR && test -f OSZICAR; then
  printf 'FPILOT_IONIC_STEP='; awk '/^[[:space:]]*[0-9]+[[:space:]]+F=/{{n=$1}} END{{print n+0}}' OSZICAR
  ediff=$(awk 'BEGIN{{IGNORECASE=1}} /^[[:space:]]*EDIFF[[:space:]]*=/{{gsub(/[!#].*/,"",$0); split($0,a,"="); gsub(/[[:space:]]/,"",a[2]); value=a[2]}} END{{print value}}' INCAR)
  last=$(awk '/^[[:space:]]*(DAV|RMM):/{{step=$2; de=$4; deps=$5}} END{{if(step!="") print step"|"de"|"deps}}' OSZICAR)
  if test -n "$ediff" && test -n "$last"; then
    printf 'FPILOT_EDIFF_NUMERIC=%s|%s\n' "$ediff" "$last"
  fi
fi
'''
    scheduler=scheduler_name(record)
    if record.get('job_id'):
        if scheduler=='pbs':
            command+=pbs_history_command(record['job_id'])
        else:
            job=shlex.quote(record['job_id'])
            command+="squeue -h -j {} -o 'FPILOT_SLURM_LIVE=%T' || true\n".format(job)
            command+="sacct -n -P -j {} --format=JobIDRaw,State,ExitCode 2>/dev/null | awk -F'|' '$1 == \"{}\" {{print \"FPILOT_SLURM_ACCOUNT=\"$2\"|\"$3; exit}}' || true\n".format(job,record['job_id'])
    else:
        command+=recovered_job_command(scheduler)
    receipt=ssh(record['config']['alias'],command,timeout=30)
    match=re.search(r'^FPILOT_JOB_ID=(\d+(?:\.[A-Za-z0-9_.-]+)?)\s*$',receipt['stdout'],re.M)
    if match and not record.get('job_id'):record['job_id']=match.group(1)
    out=receipt['stdout'];exit_match=re.search(r'^FPILOT_EXIT=(\d+)\s*$',out,re.M)
    state_match=re.search(r'job_state\s*=\s*([A-Z])',out)
    slurm_live=re.search(r'^FPILOT_SLURM_LIVE=([A-Z_+]+)',out,re.M)
    slurm_account=re.search(r'^FPILOT_SLURM_ACCOUNT=([A-Z_+]+)\|([^\s]+)',out,re.M)
    timing_match=re.search(r'^FPILOT_TIMING=(\d+)',out,re.M)
    timing_count=int(timing_match.group(1)) if timing_match else 0
    timing=timing_count > 0
    singleton_match=re.search(r'^FPILOT_MPI_SINGLETON=(\d+)',out,re.M)
    singleton_count=int(singleton_match.group(1)) if singleton_match else 0
    output_sanity=timing_count == 1 and singleton_count == 0
    ediff_text=re.search(r'^FPILOT_EDIFF=([1-9]\d*)',out,re.M) is not None
    numeric_match=re.search(r'^FPILOT_EDIFF_NUMERIC=([^|\s]+)\|(\d+)\|([^|\s]+)\|([^\s]+)',out,re.M)
    ediff_numeric=False;ediff_value=None;last_electronic_step=None;last_delta_e=None;last_delta_eps=None
    if numeric_match:
        try:
            ediff_value=float(numeric_match.group(1));last_electronic_step=int(numeric_match.group(2))
            last_delta_e=float(numeric_match.group(3));last_delta_eps=float(numeric_match.group(4))
            ediff_numeric=abs(last_delta_e)<=ediff_value and abs(last_delta_eps)<=ediff_value
        except ValueError: pass
    ediff=ediff_numeric if numeric_match else ediff_text
    ionic_match = re.search(r'^FPILOT_IONIC=(\d+)', out, re.M)
    ionic_step_match = re.search(r'^FPILOT_IONIC_STEP=(\d+)', out, re.M)
    trace_exit, trace_complete = pbs_history_evidence(out) if scheduler == 'pbs' else (None, False)
    state='UNKNOWN'
    if exit_match:
        program_ok=exit_match.group(1)=='0' and timing and output_sanity
        if not program_ok:
            state='FAILED'
        else:
            scheduler_terminal=(slurm_account and slurm_account.group(1).split('+',1)[0]=='COMPLETED') if scheduler=='slurm' else ((state_match and state_match.group(1)=='C') or (trace_complete and trace_exit == 0))
            state='COMPLETED' if scheduler_terminal else 'EXITING'
    elif state_match:state={'Q':'QUEUED','R':'RUNNING','H':'HELD','E':'EXITING','C':'UNKNOWN'}.get(state_match.group(1),'UNKNOWN')
    elif slurm_live:
        state={'PENDING':'QUEUED','CONFIGURING':'QUEUED','RUNNING':'RUNNING','COMPLETING':'EXITING',
               'SUSPENDED':'HELD'}.get(slurm_live.group(1),'UNKNOWN')
    elif slurm_account:
        terminal=slurm_account.group(1).split('+',1)[0]
        if terminal == 'COMPLETED': state='UNKNOWN'
        elif terminal in {'FAILED','CANCELLED','TIMEOUT','NODE_FAIL','OUT_OF_MEMORY','PREEMPTED','BOOT_FAIL'}: state='FAILED'
    if not exit_match and timing and output_sanity:
        scheduler_terminal = (slurm_account and slurm_account.group(1).split('+',1)[0]=='COMPLETED') if scheduler=='slurm' else ((state_match and state_match.group(1)=='C') or (trace_complete and trace_exit == 0))
        if scheduler_terminal:
            state='COMPLETED'
    scheduler_state=(state_match.group(1) if state_match else 'C' if trace_complete and trace_exit == 0 else
                     slurm_live.group(1) if slurm_live else
                     slurm_account.group(1) if slurm_account else None)
    record.update(state=state,status_receipt=receipt,updated_at=now(),
                  evidence={'wrapper_exit':int(exit_match.group(1)) if exit_match else None,'vasp_timing':timing,
                            'timing_count':timing_count,'mpi_singleton_count':singleton_count,
                            'output_sanity':output_sanity,'ediff_reached':ediff,
                            'ediff_source':'OSZICAR_NUMERIC' if numeric_match else 'OUTCAR_MARKER' if ediff_text else None,
                            'ediff_value':ediff_value,'last_electronic_step':last_electronic_step,
                            'last_delta_e':last_delta_e,'last_delta_eps':last_delta_eps,
                            'ionic_converged': int(ionic_match.group(1)) == 1 if ionic_match else None,
                            'last_ionic_step': int(ionic_step_match.group(1)) if ionic_step_match else None,
                            'scheduler':scheduler,'scheduler_state':scheduler_state,
                            'scheduler_evidence_source':'tracejob' if trace_complete else 'qstat' if state_match else 'sacct' if slurm_account else None,
                            'scheduler_exit_code':slurm_account.group(2) if slurm_account else None,
                            'scientific_validation':'NOT_PERFORMED'})
    from .fast_dispatch import tracking_note
    tracking_note(record)
    result=save(record,expected_updated_at=expected_updated_at)
    if result['state']=='FAILED':
        servers.invalidate_runtime(record['config'])
        from .execution_control import freeze_failure
        freeze_failure(key)
    return result


def retry(key,probe_id,node,cores=None,reason=''):
    parent=get(key)
    if parent.get('path_schema') != 'fpilot.remote-attempt-path':
        raise ValueError('attempt 目录结构不受支持，不能重试')
    if parent['state']!='FAILED':
        raise ValueError('只能重试已确认 FAILED；UNKNOWN、DISPATCHING 或在途任务不得重投')
    return prepare(parent['bundle_id'],dict(parent['config']),probe_id,node,cores,reason,
                   template_id=parent.get('template_snapshot',{}).get('id'),retry_of=key)

from .fast_prepare import batched as _batched_prepare
prepare = _batched_prepare(prepare, 'vasp')


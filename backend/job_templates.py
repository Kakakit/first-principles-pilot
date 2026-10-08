"""4C-2: immutable sources, bounded PBS resource editing, registered runtime binding."""
import difflib
import hashlib
import json
import os
import re
import shlex
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from .storage import ROOT
from . import servers

FIELDS = ('name', 'queue', 'node', 'ppn', 'walltime')
GPU_FIELDS = ('gpus', 'mpi_ranks', 'gpu_resource_registered', 'gpu_type')
MAX_BYTES = 65536
SCHEMA = 'fpilot.job-template.v1'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def script_bytes(text):
    raw = text if isinstance(text, bytes) else text.encode('utf-8') if isinstance(text, str) else b''
    if not raw or len(raw) > MAX_BYTES or b'\x00' in raw:
        raise ValueError('模板必须为非空 UTF-8 文本，最大 64 KiB')
    raw.decode('utf-8')
    return raw


def execution_text(text):
    """Normalize the execution copy for Linux; retain the exact imported bytes."""
    return text.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n')


def scheduler_of(text):
    kinds = [kind for kind, marker in [('pbs', 'PBS'), ('slurm', 'SBATCH')]
             if re.search(r'^\s*#' + marker + r'\b', text, re.M)]
    if len(kinds) != 1:
        raise ValueError('需要且只能包含一种 PBS 或 Slurm 调度器头')
    return kinds[0]


def resources_checked(values):
    if not isinstance(values, dict) or set(values) - set(FIELDS + GPU_FIELDS):
        raise ValueError('资源字段仅支持 name、queue、node、ppn、walltime')
    result = {}
    for key, value in values.items():
        if isinstance(value, bool) or value is None:
            raise ValueError(key + ' 无效')
        value = str(value)
        pattern = r'[1-9][0-9]*' if key in ('ppn', 'gpus', 'mpi_ranks') else r'[0-9]+:[0-5][0-9]:[0-5][0-9]' if key == 'walltime' else r'(?:yes|no)' if key == 'gpu_resource_registered' else r'[A-Za-z0-9_.-]+'
        if not re.fullmatch(pattern, value) or len(value) > 120:
            raise ValueError(key + ' 格式无效')
        if key == 'walltime' and servers.seconds(value) == 0:
            raise ValueError('walltime 必须为正值')
        result[key] = int(value) if key in ('ppn', 'gpus', 'mpi_ranks') else value
    return result


def parse(text, scheduler):
    text = execution_text(text)
    values, blockers, indexes = {}, [], {}
    body_started = False
    def add(key, value, index):
        if key in values:
            blockers.append('重复/冲突资源：' + key)
        try:
            values.update(resources_checked({key: value}))
        except ValueError as exc:
            blockers.append(str(exc))
        indexes.setdefault(index, []).append(key)
    for i, line in enumerate(text.splitlines(keepends=True)):
        code = line.strip()
        if scheduler == 'pbs' and code.startswith('#PBS'):
            if body_started:
                blockers.append('PBS 指令位于正文之后，调度器不会按预期读取')
            match = re.fullmatch(r'#PBS\s+(-N|-q|-l)\s+(.+)', code)
            if match:
                option, arg = match.groups()
                if option in ('-N', '-q'):
                    add('name' if option == '-N' else 'queue', arg, i)
                else:
                    for entry in arg.split(','):
                        entry=entry.strip()
                        if entry.startswith('nodes='):
                            node = re.fullmatch(r'nodes=([^:+]+):ppn=([0-9]+)(?::gpus=([0-9]+))?', entry.strip())
                            if node:
                                add('node', node[1], i); add('ppn', node[2], i)
                                if node[3]: add('gpus', node[3], i)
                            else:
                                blockers.append('nodes 仅支持单节点名称或数量及 ppn')
                        elif entry.strip().startswith('walltime='):
                            add('walltime', entry.strip().split('=', 1)[1], i)
                        else:
                            blockers.append('PBS -l 资源不支持：' + entry)
            elif not re.fullmatch(r'#PBS\s+(?:-j\s+(?:n|oe|eo)|-o\s+(?:vasp\.)?pbs\.out|-e\s+(?:vasp\.)?pbs\.err|-S\s+/bin/bash)', code):
                blockers.append('PBS 选项未纳入当前映射：' + code)
        if scheduler=='slurm' and code.startswith('#SBATCH'):
            if body_started:blockers.append('Slurm 指令位于正文之后，调度器不会按预期读取')
            options={'name':r'(?:--job-name(?:=|\s+)|-J\s*)', 'queue':r'(?:--partition(?:=|\s+)|-p\s*)',
                     'node':r'(?:--nodelist(?:=|\s+)|-w\s*)', 'ppn':r'(?:--ntasks(?:=|\s+)|-n\s*)',
                     'walltime':r'(?:--time(?:=|\s+)|-t\s*)'}
            for key,option in options.items():
                match=re.fullmatch(r'#SBATCH\s+'+option+r'(\S+)',code)
                if match:
                    value=match[1]
                    if key=='walltime' and value.isdigit():value='{:02d}:{:02d}:00'.format(int(value)//60,int(value)%60)
                    add(key,value,i)
            if re.search(r'^#SBATCH\s+(?:--(?:job-name|partition|nodelist|ntasks|time)\b|-[Jpwnt])',code) and i not in indexes:
                blockers.append('第 {} 行 Slurm 资源无法明确映射，请将各资源指令分行填写'.format(i+1))
        if not code or code.startswith('#'):
            continue
        body_started = True
        if re.search(r'\bcd\s+', code) and code not in ('cd $PBS_O_WORKDIR', 'cd "$PBS_O_WORKDIR"', 'cd "${PBS_O_WORKDIR:?PBS_O_WORKDIR is not set}"'):
            blockers.append('第 {} 行包含硬编码工作目录或非标准 cd'.format(i + 1))
        if re.search(r'\btrap\s+', code):
            blockers.append('第 {} 行包含 trap，与退出证据冲突'.format(i + 1))
        if re.search(r'(?:-np|-n|--ntasks)\s*=?\s*[0-9]+\b', code):
            blockers.append('第 {} 行包含硬编码 MPI 核数'.format(i + 1))
        if re.search(r'CUDA_VISIBLE_DEVICES|NVHPC|nvidia|gpu', code, re.I):
            blockers.append('检测到 GPU 环境，不能作为 CPU PBS 模板执行')
    return values, list(dict.fromkeys(blockers)), indexes


def atomic(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        temp = Path(f.name)
        try:
            f.write(raw); f.flush(); os.fsync(f.fileno())
            os.replace(temp, path)
        finally:
            if temp.exists(): temp.unlink()


def path_for(key):
    if not isinstance(key, str) or not re.fullmatch('[a-f0-9]{64}', key):
        raise ValueError('模板 ID 无效')
    return ROOT / 'job-templates' / 'metadata' / (key + '.json')


def get_template(key, include_script=True):
    record = json.loads(path_for(key).read_text())
    raw = (ROOT / 'job-templates' / 'scripts' / record['script_hash']).read_bytes()
    if digest(raw) != record['script_hash']:
        raise ValueError('模板原文 hash 校验失败')
    # Re-evaluate legacy metadata; cached availability is never authorization.
    text = raw.decode('utf-8')
    scheduler = scheduler_of(text)
    values, _, _ = parse(text, scheduler)
    record.update(resources=values, blockers=[], submission_supported=True,
                  availability='selectable', runtime_validation_required=True)
    from .gpu_requests import hints
    request = hints(text, record['program'], values)
    if request: record['resource_request'] = request
    named = set(re.findall(r'(?<![A-Za-z0-9_])(vasp_std|vasp_ncl)(?![A-Za-z0-9_])', '\n'.join(code_lines(text))))
    expected = 'vasp_std' if record['program'] == 'gpu_vasp' else record['program']
    if named and (expected == 'wannier90' or expected in ('vasp_std','vasp_ncl') and expected not in named):
        record['program_conflict'] = '原稿明确启动 ' + '/'.join(sorted(named)) + '，与保存分类不一致'
    from .verified_templates import SOURCES
    evidence = next((item[4] for item in SOURCES if item[0] == record['alias'] and item[2] == record['script_hash']), None)
    record['verification_status'] = ('candidate' if evidence.startswith('CANDIDATE') else 'historical') if evidence else 'user_imported'
    if include_script: record['original_text'] = text
    return record


def list_templates():
    result = []
    for path in sorted((ROOT / 'job-templates' / 'metadata').glob('*.json')):
        if (ROOT / 'recycle' / 'job-templates' / path.stem).exists():
            continue
        try:
            result.append(get_template(path.stem, False))
        except (ValueError, OSError, KeyError) as exc:
            result.append(dict(id=path.stem, name='不可读记录', availability='corrupt', submission_supported=False, blockers=[str(exc)]))
    return result


def import_template(text, alias, program, source, name='', prepared=False, parent_id=None, scheduler=None):
    raw = script_bytes(text)
    detected = scheduler_of(raw.decode('utf-8'))
    if scheduler is not None and scheduler != detected:
        raise ValueError('调度器声明与原稿不一致；未保存')
    if alias not in servers.load_registry()['hosts']:
        raise ValueError('请选择登记的 SSH 别名')
    if program not in ('vasp_std', 'vasp_ncl', 'gpu_vasp', 'wannier90', 'other'):
        raise ValueError('程序类型无效')
    for value in (source, name or '未命名模板'):
        if not isinstance(value, str) or not value.strip() or len(value) > 1000 or any(c in value for c in '\r\n\x00'):
            raise ValueError('名称与来源必须为非空单行文本')
    parent = get_template(parent_id) if parent_id else None
    if parent and (parent['alias'] != alias or parent['scheduler'] != detected):
        raise ValueError('适配副本必须保持来源主机和调度器绑定')
    identity = dict(schema=SCHEMA, adapter_version=2, script_hash=digest(raw), alias=alias, program=program,
                    source=source, name=name or '未命名模板', scheduler=detected, prepared=bool(prepared), parent_id=parent_id)
    key = digest(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode())
    record = dict(identity, id=key, original_hash=digest(raw), bytes=len(raw), created_at=datetime.now(timezone.utc).isoformat(),
                  parent_hash=parent['script_hash'] if parent else None)
    # All semantic validation precedes any write. A script alone cannot enable a template.
    script_path = ROOT / 'job-templates' / 'scripts' / digest(raw)
    if script_path.exists() and script_path.read_bytes() != raw:
        raise ValueError('原稿存储冲突')
    if not script_path.exists(): atomic(script_path, raw)
    if not path_for(key).exists(): atomic(path_for(key), json.dumps(record, ensure_ascii=False, indent=2).encode())
    return get_template(key)


def save_template(script, name, scheduler, alias, program, source, prepared=False, parent_id=None):
    return import_template(script, alias, program, source, name, prepared, parent_id, scheduler)


def render_pbs(text, values, indexes):
    lines = text.splitlines(keepends=True)
    node_spec = 'nodes={node}:ppn={ppn}'.format(**values)
    if values.get('mpi_ranks') and values.get('gpu_resource_registered') == 'yes':
        node_spec += ':gpus=' + str(values['gpus'])
    elif values.get('gpus') and not values.get('mpi_ranks'):
        node_spec += ':gpus=' + str(values['gpus'])
    def directives(fields):
        result = []
        if 'name' in fields: result.append('#PBS -N ' + values['name'])
        if 'queue' in fields: result.append('#PBS -q ' + values['queue'])
        if 'node' in fields or 'ppn' in fields: result.append('#PBS -l ' + node_spec)
        if 'walltime' in fields: result.append('#PBS -l walltime=' + values['walltime'])
        return result
    seen = set()
    for i, fields in indexes.items():
        seen.update(fields)
        eol = '\r\n' if lines[i].endswith('\r\n') else '\n'
        resource=re.fullmatch(r'(\s*#PBS\s+-l\s+)([^\r\n]+)(?:\r?\n)?',lines[i])
        if resource:
            entries=[]
            for entry in resource[2].split(','):
                name=entry.strip().split('=',1)[0]
                if name=='nodes' and ('node' in fields or 'ppn' in fields):entry=node_spec
                elif name=='walltime' and 'walltime' in fields:entry='walltime='+values['walltime']
                entries.append(entry)
            lines[i]=resource[1]+','.join(entries)+eol
        else:lines[i] = eol.join(directives(fields)) + eol
    missing = set(FIELDS) - seen
    if missing:
        eol = '\r\n' if '\r\n' in text else '\n'
        insert = 1 if lines and lines[0].startswith('#!') else 0
        lines[insert:insert] = [eol.join(directives(missing)) + eol]
    return ''.join(lines)


def render_slurm(text, values):
    lines = text.splitlines(keepends=True)
    known = re.compile(r'^\s*#SBATCH\s+(?:--(?:job-name|partition|nodelist|ntasks|time|nodes)\b|-(?:J|p|w|N|n|t)(?:\b|[0-9]))')
    lines = [line for line in lines if not known.match(line)]
    if values.get('mpi_ranks'):
        lines = [line for line in lines if not re.match(r'^\s*#SBATCH\s+--(?:gpus(?:-per-node)?|gres|exclusive)(?:=|\s|$)', line)]
    directives = [f'#SBATCH --job-name={values["name"]}\n', f'#SBATCH --partition={values["queue"]}\n',
                  f'#SBATCH --nodelist={values["node"]}\n', f'#SBATCH --nodes=1\n',
                  f'#SBATCH --ntasks={values.get("mpi_ranks", values["ppn"])}\n', f'#SBATCH --time={values["walltime"]}\n']
    if values.get('mpi_ranks'):
        gpu_type = (values['gpu_type'] + ':') if values.get('gpu_type') else ''
        directives += ['#SBATCH --exclusive\n', '#SBATCH --gres=gpu:' + gpu_type + str(values['gpus']) + '\n']
    offset = 1 if lines and lines[0].startswith('#!') else 0
    lines[offset:offset] = directives
    return ''.join(lines)


def code_lines(text):
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]


def render_gpu_ranks(text, ranks):
    """Only rewrite a known resource-derived NPROCS assignment in execution copy."""
    expressions = (r'`\s*cat\s+[\"\']?\$(?:PBS_NODEFILE|\{PBS_NODEFILE\})[\"\']?\s*\|\s*wc\s+-l\s*`',
                   r'\$\(\s*wc\s+-l\s*<\s*[\"\']?\$(?:PBS_NODEFILE|\{PBS_NODEFILE\})[\"\']?\s*\)',
                   r'[\"\']?\$(?:SLURM_NTASKS|\{SLURM_NTASKS\})[\"\']?')
    pattern = r'(?m)^([ \t]*(?:export[ \t]+)?NPROCS[ \t]*=)[ \t]*(?:' + '|'.join(expressions) + r')[ \t]*$'
    return re.sub(pattern, lambda match: match[1] + str(ranks), text)


def runtime_match(text, alias, config=None):
    """Bounded supported body: same command sequence as the existing PBS adapter.

    This is not a shell interpreter. Other bodies stay preview-only; prepared
    declarations or matching a single executable name cannot grant execution.
    """
    try:
        registered = servers.select_profile(alias)
        if config and any(config.get(k) != registered[k] for k in ('mpi_executable', 'vasp_executable', 'environment_script')):
            return False
        expected = servers.pbs.render_script(servers.profile(registered, 'preview', 1))
        from .verified_templates import matches
        return (text.splitlines()[0] == '#!/bin/bash' and code_lines(text) == code_lines(expected)) or matches(text, alias)
    except (ValueError, IndexError):
        return False


def render_template(template_id, resources=None, alias=None, config=None, allow_prepared=False):
    record = get_template(template_id)
    execution_original = execution_text(record['original_text'])
    overrides = resources_checked(resources or {})
    values, parsed_blockers, indexes = parse(execution_original, record['scheduler'])
    blockers = []
    values.update(overrides)
    selected = alias or record['alias']
    if selected != record['alias']: blockers.append('模板主机绑定不一致')
    if config and servers.normalize_scheduler(config.get('scheduler')) != record['scheduler']:
        blockers.append('模板调度器与所选主机不一致')
    missing = [key for key in FIELDS if key not in values]
    if missing: blockers.append('缺少覆盖后的资源字段：' + '、'.join(missing))
    rendered = (render_pbs(execution_original, values, indexes) if record['scheduler']=='pbs'
                else render_slurm(execution_original, values)) if not blockers else None
    gpu_mode = bool(values.get('mpi_ranks'))
    if gpu_mode and rendered: rendered = render_gpu_ranks(rendered, values['mpi_ranks'])
    script_mode=bool(config and config.get('runtime_source')=='script')
    if rendered and config and not script_mode and record.get('adapter_version')!=2 and PurePosixPath(config.get('vasp_executable','')).name == 'vasp_ncl':
        standard = str(PurePosixPath(config['vasp_executable']).with_name('vasp_std'))
        registered_standard=servers.select_profile(selected)['vasp_executable']
        rendered = rendered.replace(registered_standard,config['vasp_executable']).replace(standard, config['vasp_executable'])
        rendered = re.sub(r'(?<![A-Za-z0-9_/])vasp_std(?![A-Za-z0-9_])',
                          config['vasp_executable'], rendered)
    runtime_ok = bool(rendered)
    execution_blockers = list(blockers)
    warnings=['脚本正文由用户管理；预览和实时资源检查仍需完成']
    if script_mode or record.get('adapter_version')==2:
        # Preserve arbitrary environment setup; reject only concrete mapping/exit conflicts.
        execution_blockers.extend(v for v in parsed_blockers if not v.startswith(('PBS -l 资源不支持：','PBS 选项未纳入当前映射：')) and 'GPU 环境' not in v and (not v.startswith('第 ') or 'Slurm 资源' in v))
        for i,line in enumerate(execution_original.splitlines(),1):
            code=line.strip()
            if not code or code.startswith('#'):continue
            if re.search(r'\bcd\s+',code):
                try:parts=shlex.split(code,comments=True)
                except ValueError:parts=[]
                args=parts[1:] if parts and parts[0]=='cd' else []
                if args and args[0]=='--':args=args[1:]
                workdir='PBS_O_WORKDIR' if record['scheduler']=='pbs' else 'SLURM_SUBMIT_DIR'
                safe=len(args)==1 and re.fullmatch(r'\$(?:'+workdir+r'|\{'+workdir+r'(?::\?[^}]*)?\})',args[0])
                if not safe and (not parts or 'cd' in parts):
                    execution_blockers.append('第 {} 行进入固定/未知目录；请保存适配副本，使用调度器提交目录'.format(i))
            if re.search(r'\btrap\s+',code):execution_blockers.append('第 {} 行已有 trap；请在适配副本中处理退出状态采集冲突'.format(i))
            for match in re.finditer(r'(?:-np|-n|--ntasks)\s*=?\s*([0-9]+)\b',code):
                if match[0].startswith(('-n ','-n=' ,'--ntasks')) and not re.search(r'\b(?:mpirun|mpiexec|srun)\b',code):continue
                expected_ranks = values.get('mpi_ranks', values.get('ppn'))
                if expected_ranks and int(match[1]) != expected_ranks:
                    execution_blockers.append(('第 {} 行固定 MPI 进程数 {} 与请求进程数 {} 不一致；请修改正文或保持原进程数' if gpu_mode else '第 {} 行固定 MPI 进程数 {} 与请求核数 {} 不一致；请修改正文或保持原核数').format(i,match[1],expected_ranks))
        if gpu_mode and rendered:
            for line in code_lines(rendered):
                if re.search(r'\b(?:mpirun|mpiexec)\b', line) and not re.search(r'(?:-np|-n|--ntasks)(?:=|\s+)\S+', line):
                    execution_blockers.append('GPU MPI 启动器未明确进程数；整节点分配会改变默认进程数，请编辑副本明确 -np')
            for line in code_lines(rendered):
                if not re.search(r'\b(?:mpirun|mpiexec|srun)\b',line): continue
                argument=re.search(r'(?:-np|-n|--ntasks)(?:=|\s+)(\S+)',line)
                if not argument: continue  # bare srun uses the mapped --ntasks header
                value=argument[1].strip('"\'')
                nprocs=value in ('$NPROCS','${NPROCS}') and re.search(r'(?m)^[ \t]*(?:export[ \t]+)?NPROCS[ \t]*=[ \t]*'+str(values['mpi_ranks'])+r'[ \t]*$',rendered)
                slurm=value in ('$SLURM_NTASKS','${SLURM_NTASKS}') and record['scheduler']=='slurm'
                if not (value.isdigit() or nprocs or slurm):
                    execution_blockers.append('动态 MPI 进程数无法明确映射，请在适配副本中明确启动进程数')
        if record['scheduler']=='slurm':
            if gpu_mode and re.search(r'(?m)^\s*#SBATCH\s+--(?:gpus-per-task|cpus-per-gpu|gpu-bind|gpu-freq|oversubscribe)\b',execution_original):
                execution_blockers.append('当前 GPU 适配支持单节点整机占用；逐任务绑定、共享或线程布局需要另存明确的适配副本')
            if re.search(r'(?m)^\s*#SBATCH\s+(?:--cpus-per-task(?:=|\s+)(?!1(?:\s|$))\S+|-c\s*(?!1(?:\s|$))\S+|--(?:ntasks-per-node|ntasks-per-core|ntasks-per-socket)\b)',execution_original) or (not gpu_mode and re.search(r'(?m)^\s*#SBATCH\s+--(?:gres|gpus)\b', execution_original)):
                execution_blockers.append('此 Slurm 脚本含线程/GPU/特殊任务布局，不能把总核数直接映射为 ntasks；请先保存纯 MPI 单节点适配副本')
            if re.search(r'(?m)^\s*#SBATCH\s+(?:--nodes(?:=|\s+)|-N\s*)(?!1(?:\s|$))\S+',execution_original):
                execution_blockers.append('当前资源选择仅支持单节点；请先保存单节点适配副本')
        elif re.search(r'(?m)^\s*#PBS\s+-l\s+.*(?:select=|nodes=[2-9][0-9]*:)',execution_original) or (not gpu_mode and re.search(r'(?m)^\s*#PBS\s+-l\s+.*gpus=', execution_original)):
            execution_blockers.append('PBS select/多节点/GPU 资源不属于当前单节点核数映射；请保存匹配的适配副本')
        if config and (script_mode or record.get('adapter_version')==2):
            expected=servers.program_name(config) or record['program']
            compatible=record['program']==expected or expected=='vasp_std' and record['program']=='gpu_vasp'
            if not compatible:execution_blockers.append('脚本分类 {} 与本次程序 {} 不一致，请选择对应脚本'.format(record['program'],expected))
            named=set(re.findall(r'(?<![A-Za-z0-9_])(vasp_std|vasp_ncl)(?![A-Za-z0-9_])', '\n'.join(code_lines(execution_original))))
            if expected in ('vasp_std','vasp_ncl') and named and expected not in named:
                execution_blockers.append('脚本正文明确启动 {}，本次需要 {}；不会自动替换程序命令'.format('/'.join(sorted(named)),expected))
            if expected == 'wannier90' and named:
                execution_blockers.append('脚本正文明确启动 VASP，与 Wannier90 分类不一致，请导入对应原稿')
    execution_blockers=list(dict.fromkeys(execution_blockers))
    parent_text = get_template(record['parent_id'])['original_text'] if record.get('parent_id') else record['original_text']
    return dict(schema='fpilot.job-template-preview.v1', id=template_id, name=record['name'], alias=selected,
                scheduler=record['scheduler'], program=record['program'], source=record['source'], original_hash=record['script_hash'],
                parent_id=record.get('parent_id'), parent_hash=record.get('parent_hash'), original_text=record['original_text'],
                rendered_text=rendered, script_text=rendered or record['original_text'], resources=values,
                line_endings_normalized=execution_original != record['original_text'],
                blockers=blockers, execution_blockers=execution_blockers, warnings=warnings,
                submission_supported=not execution_blockers, runtime_matches_registry=runtime_ok,
                runtime_validation_required=True, remote_submission_performed=False,
                resource_diff=''.join(difflib.unified_diff(record['original_text'].splitlines(True), (rendered or record['original_text']).splitlines(True), fromfile='original', tofile='resource-preview')),
                adaptation_diff=''.join(difflib.unified_diff(parent_text.splitlines(True), record['original_text'].splitlines(True), fromfile='parent-original', tofile='adapted-copy')))


preview_template = render_template

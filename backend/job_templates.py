"""4C-2: immutable sources, bounded PBS resource editing, registered runtime binding."""
import difflib
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from .storage import ROOT
from . import servers

FIELDS = ('name', 'queue', 'node', 'ppn', 'walltime')
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
    if not isinstance(values, dict) or set(values) - set(FIELDS):
        raise ValueError('资源字段仅支持 name、queue、node、ppn、walltime')
    result = {}
    for key, value in values.items():
        if isinstance(value, bool) or value is None:
            raise ValueError(key + ' 无效')
        value = str(value)
        pattern = r'[1-9][0-9]*' if key == 'ppn' else r'[0-9]+:[0-5][0-9]:[0-5][0-9]' if key == 'walltime' else r'[A-Za-z0-9_.-]+'
        if not re.fullmatch(pattern, value) or len(value) > 120:
            raise ValueError(key + ' 格式无效')
        if key == 'walltime' and servers.seconds(value) == 0:
            raise ValueError('walltime 必须为正值')
        result[key] = int(value) if key == 'ppn' else value
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
                        if entry.startswith('nodes='):
                            node = re.fullmatch(r'nodes=([^:]+):ppn=([0-9]+)', entry.strip())
                            if node:
                                add('node', node[1], i); add('ppn', node[2], i)
                            else:
                                blockers.append('nodes 仅支持单节点名称或数量及 ppn')
                        elif entry.strip().startswith('walltime='):
                            add('walltime', entry.strip().split('=', 1)[1], i)
                        else:
                            blockers.append('PBS -l 资源不支持：' + entry)
            elif not re.fullmatch(r'#PBS\s+(?:-j\s+(?:n|oe|eo)|-o\s+(?:vasp\.)?pbs\.out|-e\s+(?:vasp\.)?pbs\.err|-S\s+/bin/bash)', code):
                blockers.append('PBS 选项未纳入当前映射：' + code)
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
    if program not in ('vasp_std', 'gpu_vasp', 'wannier90', 'other'):
        raise ValueError('程序类型无效')
    for value in (source, name or '未命名模板'):
        if not isinstance(value, str) or not value.strip() or len(value) > 1000 or any(c in value for c in '\r\n\x00'):
            raise ValueError('名称与来源必须为非空单行文本')
    parent = get_template(parent_id) if parent_id else None
    if parent and (parent['alias'] != alias or parent['program'] != program or parent['scheduler'] != detected):
        raise ValueError('适配副本必须保持来源主机、程序和调度器绑定')
    identity = dict(schema=SCHEMA, script_hash=digest(raw), alias=alias, program=program,
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
    def directives(fields):
        result = []
        if 'name' in fields: result.append('#PBS -N ' + values['name'])
        if 'queue' in fields: result.append('#PBS -q ' + values['queue'])
        if 'node' in fields or 'ppn' in fields: result.append('#PBS -l nodes={node}:ppn={ppn}'.format(**values))
        if 'walltime' in fields: result.append('#PBS -l walltime=' + values['walltime'])
        return result
    seen = set()
    for i, fields in indexes.items():
        seen.update(fields)
        eol = '\r\n' if lines[i].endswith('\r\n') else '\n'
        lines[i] = eol.join(directives(fields)) + eol
    missing = set(FIELDS) - seen
    if missing:
        eol = '\r\n' if '\r\n' in text else '\n'
        insert = 1 if lines and lines[0].startswith('#!') else 0
        lines[insert:insert] = [eol.join(directives(missing)) + eol]
    return ''.join(lines)


def render_slurm(text, values):
    lines = text.splitlines(keepends=True)
    known = re.compile(r'^\s*#SBATCH\s+(?:--(?:job-name|partition|nodelist|ntasks|time|nodes)\b|-J\b|-p\b|-N\b|-n\b|-t\b)')
    lines = [line for line in lines if not known.match(line)]
    directives = [f'#SBATCH --job-name={values["name"]}\n', f'#SBATCH --partition={values["queue"]}\n',
                  f'#SBATCH --nodelist={values["node"]}\n', f'#SBATCH --nodes=1\n',
                  f'#SBATCH --ntasks={values["ppn"]}\n', f'#SBATCH --time={values["walltime"]}\n']
    offset = 1 if lines and lines[0].startswith('#!') else 0
    lines[offset:offset] = directives
    return ''.join(lines)


def code_lines(text):
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]


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
    values, _, indexes = parse(execution_original, record['scheduler'])
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
    if rendered and config and PurePosixPath(config.get('vasp_executable','')).name == 'vasp_ncl':
        standard = str(PurePosixPath(config['vasp_executable']).with_name('vasp_std'))
        registered_standard=servers.select_profile(selected)['vasp_executable']
        rendered = rendered.replace(registered_standard,config['vasp_executable']).replace(standard, config['vasp_executable'])
        rendered = re.sub(r'(?<![A-Za-z0-9_/])vasp_std(?![A-Za-z0-9_])',
                          config['vasp_executable'], rendered)
    runtime_ok = bool(rendered)
    execution_blockers = list(blockers)
    parent_text = get_template(record['parent_id'])['original_text'] if record.get('parent_id') else record['original_text']
    return dict(schema='fpilot.job-template-preview.v1', id=template_id, name=record['name'], alias=selected,
                scheduler=record['scheduler'], program=record['program'], source=record['source'], original_hash=record['script_hash'],
                parent_id=record.get('parent_id'), parent_hash=record.get('parent_hash'), original_text=record['original_text'],
                rendered_text=rendered, script_text=rendered or record['original_text'], resources=values,
                line_endings_normalized=execution_original != record['original_text'],
                blockers=blockers, execution_blockers=execution_blockers, warnings=['脚本正文由用户管理；预览和实时资源检查仍需完成'],
                submission_supported=not execution_blockers, runtime_matches_registry=runtime_ok,
                runtime_validation_required=True, remote_submission_performed=False,
                resource_diff=''.join(difflib.unified_diff(record['original_text'].splitlines(True), (rendered or record['original_text']).splitlines(True), fromfile='original', tofile='resource-preview')),
                adaptation_diff=''.join(difflib.unified_diff(parent_text.splitlines(True), record['original_text'].splitlines(True), fromfile='parent-original', tofile='adapted-copy')))


preview_template = render_template

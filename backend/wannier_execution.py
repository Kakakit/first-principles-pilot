"""C2: one explicit Wannier90 attempt from a completed VASP interface attempt.

Large matrices stay on the same host.  A PREPARED record freezes their hashes,
the edited .win, the script and the destination; submit rechecks all of them.
"""
import hashlib
import importlib.util
import io
import json
import math
import re
import shlex
import subprocess
import tarfile
import tempfile
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path, PurePosixPath

from . import execution as ex, execution_control as control, job_templates, remote_paths, servers
from .storage import ROOT, validate_workspace

spec = importlib.util.spec_from_file_location('fpilot_wannier_contract', Path(__file__).resolve().parents[1] / 'resources/sets/wannier90.py')
win_contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(win_contract)

SCRIPT_DIR = Path(__file__).resolve().parents[1] / 'resources/jobs/wannier90'
from .platform_runtime import controlled_pull, local_python
SAFE_PULL = controlled_pull()
REGISTERED_PYTHON = local_python()
SEED = 'wannier90'
SOURCE_NAMES = tuple(SEED + '.' + ext for ext in ('win', 'amn', 'mmn', 'eig'))
PARAMETERS = {
    'num_iter': ('局域化最大迭代', 'integer_nonnegative', ''),
    'conv_window': ('局域化收敛窗口', 'integer_window', ''),
    'conv_tol': ('局域化收敛阈值', 'positive', 'Å²'),
    'dis_num_iter': ('解纠缠最大迭代', 'integer_nonnegative', ''),
    'dis_mix_ratio': ('解纠缠混合比例', 'ratio', ''),
    'dis_conv_window': ('解纠缠收敛窗口', 'integer_positive', ''),
    'dis_conv_tol': ('解纠缠收敛阈值', 'positive', 'Å²'),
    'dis_win_min': ('外窗下界', 'number', 'eV'),
    'dis_win_max': ('外窗上界', 'number', 'eV'),
    'dis_froz_min': ('冻结窗下界', 'number', 'eV'),
    'dis_froz_max': ('冻结窗上界', 'number', 'eV'),
    'write_hr': ('输出 Hamiltonian', 'boolean', ''),
    'write_xyz': ('输出 Wannier 中心（TB2J hr 所需）', 'boolean', ''),
}
WINDOW_TAGS = ('dis_win_min', 'dis_win_max', 'dis_froz_min', 'dis_froz_max')


def parameter_catalog():
    official, _ = win_contract.official_tags()
    if not set(PARAMETERS).issubset(official):
        raise ValueError('Wannier90 参数目录与官方 tag 注册表不一致')
    return [{'tag':name, 'label':label, 'type':kind, 'unit':unit}
            for name,(label,kind,unit) in PARAMETERS.items()]


def parameter_values(win):
    document = win_contract.parse_win(win)
    return {name:win_contract._first_value(document,name) or '' for name in PARAMETERS}


def edit_win(win, changes, fermi_eV=None):
    if not isinstance(changes, dict) or set(changes) - set(PARAMETERS):
        raise ValueError('包含未登记的 Wannier90 参数')
    document = win_contract.parse_win(win)
    request = {'schema':win_contract.WIN_REQUEST_SCHEMA, 'add':{}, 'replace':{}}
    for name, raw in changes.items():
        if not isinstance(raw, str) or not raw.strip() or '\n' in raw or '\r' in raw:
            raise ValueError(name + ' 需要单行参数值')
        value = raw.strip()
        kind = PARAMETERS[name][1]
        if kind == 'boolean':
            if value.lower() not in ('true','false','.true.','.false.'):
                raise ValueError(name + ' 只能是 true 或 false')
            value = '.true.' if 'true' in value.lower() else '.false.'
        elif kind.startswith('integer'):
            if not re.fullmatch(r'[+-]?\d+', value):
                raise ValueError(name + ' 必须是整数')
            n = int(value)
            if kind == 'integer_positive' and n < 1 or kind == 'integer_nonnegative' and n < 0 or kind == 'integer_window' and n < -1:
                raise ValueError(name + ' 超出允许范围')
        else:
            try: n = float(value)
            except ValueError: raise ValueError(name + ' 必须是有限数值') from None
            if not math.isfinite(n) or kind == 'positive' and n <= 0 or kind == 'ratio' and not 0 < n <= 1:
                raise ValueError(name + ' 超出允许范围')
        if name in WINDOW_TAGS and fermi_eV is not None:
            if not isinstance(fermi_eV, (int, float)) or not math.isfinite(fermi_eV):
                raise ValueError('费米能必须是有限数值')
            value = format(float(value) + fermi_eV, '.12g')
        target = 'replace' if document.occurrences(name) else 'add'
        request[target][name] = value
    edited, audit = win_contract.merge_win(win, request)
    _checked_win(win, edited)
    return {'win':edited, 'changes':audit, 'parameters':parameter_values(edited)}


def source_names(seed, win_name=None):
    if seed not in ('wannier90', 'wannier90.1', 'wannier90.2'):
        raise ValueError('仅支持 VASP 输出的一组或显式 .1/.2 自旋通道')
    if win_name is None:
        win_name = seed + '.win'
    if win_name not in (seed + '.win', 'wannier90.win'):
        raise ValueError('接口 .win 必须属于所选通道或为共享 wannier90.win')
    return (win_name,) + tuple(seed + '.' + ext for ext in ('amn', 'mmn', 'eig'))


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def script_choices():
    choices = []
    for path in sorted(SCRIPT_DIR.glob('*')):
        alias = path.name.split('.', 1)[0]
        if alias not in servers.load_registry()['hosts']:
            continue
        choices.append({'alias': alias, 'name': path.name, 'sha256': _sha(path.read_bytes()),
                        'source': '用户提供的 wannier90_parallel 脚本副本',
                        'evidence': '来源目录标为候选' if path.suffix == '.candidate' else '来源目录记录了并行 smoke'})
    return choices


def _builtin(alias):
    matches = list(SCRIPT_DIR.glob(alias + '.*'))
    if len(matches) != 1:
        raise ValueError('该服务器没有随附的 Wannier90 脚本，请导入用户脚本')
    raw = matches[0].read_bytes()
    return {'source': str(matches[0]), 'text': raw.decode('utf-8'), 'sha256': _sha(raw)}


def _render_script(config, profile, template_id):
    scheduler = servers.normalize_scheduler(config['scheduler'])
    resources = ex.template_resources(profile)
    if template_id is None:
        template_id=config.get('template_id')
        if not template_id:
            from .workbench_settings import read
            template_id=read()['defaults'].get(config['alias']+':wannier90')
    if template_id:
        rendered = job_templates.render_template(template_id, resources, config['alias'], config)
        if not rendered['submission_supported']:
            raise ValueError('脚本与主机或调度器不匹配：' + '; '.join(rendered['execution_blockers']))
        return rendered['rendered_text'], {'id': template_id, 'sha256': rendered['original_hash'], 'source': rendered['source']}
    builtin = _builtin(config['alias'])
    text = builtin['text']
    if job_templates.scheduler_of(text) != scheduler:
        raise ValueError('随附脚本的调度器与服务器不匹配')
    values = job_templates.resources_checked(resources)
    if scheduler == 'slurm':
        text = job_templates.render_slurm(text, values)
    else:
        edits = ((r'(?m)^#PBS -N .*$', '#PBS -N ' + values['name']),
                 (r'(?m)^#PBS -q .*$', '#PBS -q ' + values['queue']),
                 (r'(?m)^#PBS -l nodes=[^\r\n]*$', '#PBS -l nodes=' + values['node'] + ':ppn=' + str(values['ppn']) + (':gpus=1' if ':gpus=1' in text else '')),
                 (r'(?m)^#PBS -l walltime=[^\r\n]*$', '#PBS -l walltime=' + values['walltime']))
        for pattern, replacement in edits:
            text, count = re.subn(pattern, replacement, text)
            if not count and not (pattern.startswith(r'(?m)^#PBS -q') and '#PBS -q' not in builtin['text']):
                raise ValueError('随附脚本缺少必要的 PBS 资源声明')
        if '#PBS -q' not in text:
            text = text.replace('#PBS -N ', '#PBS -q ' + values['queue'] + '\n#PBS -N ', 1)
    return text, {'id': None, 'sha256': builtin['sha256'], 'source': builtin['source']}


def _parent(project, node_id, parent_id, alias):
    validate_workspace({'activeId': project['id'], 'projects': [project]})
    nodes = {item['id']: item for item in project['nodes']}
    if node_id not in nodes or nodes[node_id]['data']['kind'] != 'wannier90':
        raise ValueError('请选择当前项目的 Wannier90 节点')
    parent = ex.get(parent_id)
    from . import recycle
    recycle.assert_available('attempts',parent_id,parent)
    if parent.get('state') != 'COMPLETED' or parent.get('path_schema') != 'fpilot.remote-attempt-path':
        raise ValueError('需要已完成且路径可追溯的 VASP-Wannier attempt')
    if parent.get('project_id') != project['id'] or parent.get('config', {}).get('alias') != alias:
        raise ValueError('VASP-Wannier 来源须属于当前项目和同一服务器')
    if not any(edge['source'] == parent.get('node_id') and edge['target'] == node_id for edge in project['edges']):
        raise ValueError('请在画布上将 Wannier90 节点直接连接 VASP-Wannier 来源')
    manifest, _ = ex.bundle(parent['bundle_id'], allow_dependencies=True)
    if manifest['stage'] != 'vasp-wannier' or not (parent.get('evidence') or {}).get('vasp_timing'):
        raise ValueError('来源不是已正常结束的 VASP-Wannier 接口任务')
    return parent, nodes[node_id]


def discover(project, node_id, parent_id, alias, force=False):
    parent, _ = _parent(project, node_id, parent_id, alias)
    source = ex.attempt_path(parent)['shell']
    from . import remote_cache
    identity={'parent':parent_id,'alias':alias,'path':source,'job_id':parent.get('job_id')}
    cached=remote_cache.read('wannier-discovery',identity)
    if cached and not force:return cached
    command = ('set -e\ncd ' + remote_paths.shell_path(source) + '\n'
               'for seed in wannier90 wannier90.1 wannier90.2; do\n'
               '  if { test -s "$seed.win" || test -s wannier90.win; } && test -s "$seed.amn" && test -s "$seed.mmn" && test -s "$seed.eig"; then printf "FPILOT_W90_SEED=%s\\n" "$seed"; fi\n'
               'done\n')
    receipt = ex.ssh(alias, command, timeout=30)
    if receipt['exit_code'] != 0:
        raise ValueError('无法读取接口文件目录')
    seeds = re.findall(r'^FPILOT_W90_SEED=(wannier90(?:\.[12])?)$', receipt['stdout'], re.M)
    if not seeds:
        raise ValueError('未找到完整的 Wannier90 接口四文件组')
    return remote_cache.write('wannier-discovery',identity,{'source_attempt_id':parent_id, 'source_path':source, 'seeds':seeds,
            'mode':'two-collinear-channels' if 'wannier90.1' in seeds and 'wannier90.2' in seeds else 'single',
            'remote_mutation':False})


def inspect(project, node_id, parent_id, alias, seed=SEED, verification_mode='lightweight', force=False, max_cache_age=None):
    from . import remote_cache, remote_energy, safe_files
    if verification_mode not in ('lightweight', 'full'):
        raise ValueError('文件核验策略无效')
    parent, _ = _parent(project, node_id, parent_id, alias)
    source = ex.attempt_path(parent)['shell']
    identity={'parent':parent_id,'alias':alias,'path':source,'seed':seed,'verification_mode':verification_mode}
    cached=remote_cache.read('wannier-source',identity,max_cache_age)
    if cached and not force:return cached
    source_names(seed)
    matrix_names = source_names(seed)[1:]
    names = ' '.join(shlex.quote(name) for name in matrix_names)
    command = ('set -e\ncd ' + remote_paths.shell_path(source) + '\n'
               'win_name=' + shlex.quote(seed + '.win') + '\n'
               'if ! test -s "$win_name"; then win_name=wannier90.win; fi\n'
               'for file in "$win_name" ' + names + '; do\n'
               '  test -f "$file" && test ! -L "$file" && test -s "$file"\n' +
               ("  case \"$file\" in *.win) hash=$(sha256sum \"$file\" | awk '{print $1}');; *) hash=-;; esac\n" if verification_mode == 'lightweight' else
                "  hash=$(sha256sum \"$file\" | awk '{print $1}')\n") +
               '  size=$(wc -c < "$file")\n'
               '  printf "FPILOT_W90_FILE=%s|%s|%s|" "$file" "$hash" "$size"; stat -c %y "$file"\n'
               'done\n'
               'test $(wc -c < "$win_name") -le 262144\n')
    # The selected seed may use the common wannier90.win; resolve and pull it in
    # the same controlled request, rather than another stat and SCP session.
    energy=remote_cache.read('energy',remote_energy.identity(parent))
    command += 'set +e\n'+remote_energy.command(parent,energy)+'set -e\n'
    win_name=(cached or {}).get('win_source_name') or ('wannier90.win' if seed!='wannier90' else seed+'.win')
    # A small explicit discovery of win_name is returned with the source receipt;
    # the controlled reader supports the exact seed path with a common fallback.
    with tempfile.TemporaryDirectory() as temporary:
        target=Path(temporary)/'source.win'
        request={'remote_path':source.rstrip('/')+'/'+seed+'.win','destination':str(target),
                 'fallback_path':source.rstrip('/')+'/wannier90.win'}
        pulled=safe_files.pull_many(alias,[request],command)
        receipt={'stdout':pulled['companion_stdout']}
        win=target.read_text(encoding='utf-8')
    files = {}
    for name, digest, size, mtime in re.findall(r'^FPILOT_W90_FILE=([^|\n]+)\|(-|[a-f0-9]{64})\|(\d+)\|([^\r\n]+)$', receipt['stdout'], re.M):
        files[name] = {'size_bytes': int(size), 'mtime': mtime,
                       'verification_mode': 'metadata' if digest == '-' else 'content',
                       **({'sha256': digest} if digest != '-' else {})}
    win_name = seed + '.win' if seed + '.win' in files else 'wannier90.win'
    selected_names = source_names(seed, win_name)
    if set(files) != set(selected_names):
        raise ValueError('Wannier90 接口文件核验回执不完整')
    if any(info['size_bytes'] <= 0 or bool(info.get('sha256')) != (verification_mode == 'full' or name == win_name)
           for name, info in files.items()):
        raise ValueError('Wannier90 接口文件回执与所选核验策略不符')
    if _sha(win.encode()) != files[win_name]['sha256']:
        raise ValueError('.win 内容与远端哈希不符')
    errors = win_contract.validate_win(win)
    if errors:
        raise ValueError('VASP 生成的 .win 需要检查：' + json.dumps(errors, ensure_ascii=False)[:1000])
    energy=None
    try: energy=remote_energy.store(parent,receipt['stdout'])
    except ValueError: pass  # A valid .win remains editable without an OUTCAR reference.
    return remote_cache.write('wannier-source',identity,{'source_attempt_id': parent_id, 'source_path': source, 'source_job_id': parent.get('job_id'),
            'alias': alias, 'seed': seed, 'win_source_name': win_name, 'files': files, 'win': win,
            'spinors': win_contract._first_value(win_contract.parse_win(win), 'spinors'),
            'parameters': parameter_values(win),'energy_reference':energy,
            'verification_mode': verification_mode, 'remote_mutation': False})


def _checked_win(source, edited):
    if not isinstance(edited, str) or not 0 < len(edited.encode()) <= 262144:
        raise ValueError('.win 必须是非空文本且不超过 256 KiB')
    errors = win_contract.validate_win(edited)
    if errors:
        raise ValueError('.win 审查未通过：' + json.dumps(errors, ensure_ascii=False)[:1500])
    before, after = win_contract.parse_win(source), win_contract.parse_win(edited)
    official_scalars, official_blocks = win_contract.official_tags()
    for name, entries in after.scalars.items():
        if name not in official_scalars and [item.value for item in entries] != [item.value for item in before.scalars.get(name, [])]:
            raise ValueError('新增或更改了未登记的 Wannier90 参数：' + name)
    for name, entries in after.blocks.items():
        if name not in official_blocks and [item.body for item in entries] != [item.body for item in before.blocks.get(name, [])]:
            raise ValueError('新增或更改了未登记的 Wannier90 参数块：' + name)
    for name in win_contract.PROTECTED_SCALARS:
        if win_contract._first_value(before, name) != win_contract._first_value(after, name):
            raise ValueError('VASP 生成字段不可改：' + name)
    for name in win_contract.PROTECTED_BLOCKS:
        left, right = before.blocks.get(name, []), after.blocks.get(name, [])
        if [item.body for item in left] != [item.body for item in right]:
            raise ValueError('VASP 生成字段不可改：' + name)


def prepare(project, node_id, parent_id, config, probe_id, node, cores=None, resource_reason='',
            input_reason='', win='', template_id=None, seed=SEED, energy_reference=None,
            relative_windows=None, band_path=None, frozen_id=None, attempt_id=None, execution_scope=None,
            scan_batch_id=None, scan_case_id=None, scan_case_table_hash=None):
    from . import recycle
    recycle.assert_available('projects',project['id'],{'parent_attempt_id':parent_id,'template_id':template_id})
    frozen=None
    if attempt_id is not None:
        if not re.fullmatch(r'[a-f0-9]{32}',attempt_id):raise ValueError('attempt 身份无效')
        recycle.assert_available('attempts',attempt_id)
        try:existing=ex.get(attempt_id)
        except ValueError:existing=None
        if existing:
            if (existing.get('bundle_id'),existing.get('execution_scope'),existing.get('scan_case_id'),existing.get('config',{}).get('alias'),existing.get('node')) != (frozen_id,execution_scope or node_id,scan_case_id,config['alias'],node):
                raise ValueError('原 attempt 与冻结窗口或派发身份不一致')
            return existing
    if frozen_id:
        recycle.assert_available('wannier-inputs',frozen_id)
        frozen=configuration(frozen_id)
        if (frozen['project_id'],frozen['node_id'],frozen['alias'])!=(project['id'],node_id,config['alias']):
            raise ValueError('冻结 Wannier90 输入的项目、节点或服务器不一致')
        if frozen.get('execution_scope') and (frozen['execution_scope'],frozen.get('scan_batch_id'),frozen.get('scan_case_id')) != (execution_scope,scan_batch_id,scan_case_id):
            raise ValueError('窗口 case 必须由对应批次派发，不能通过单任务入口提交')
        project=frozen['project']
        parent_id,win,seed,input_reason=(frozen[k] for k in ('parent_attempt_id','win','seed','input_reason'))
        energy_reference,relative_windows,band_path=(frozen[k] for k in ('energy_reference','relative_windows','band_path'))
    recycle.assert_available('projects',project['id'],{'parent_attempt_id':parent_id,'template_id':template_id})
    if not isinstance(input_reason, str) or not input_reason.strip() or len(input_reason) > 2000:
        raise ValueError('请填写本次 Wannier90 输入调整理由')
    parent, target = _parent(project, node_id, parent_id, config['alias'])
    from . import branch_bindings
    binding = branch_bindings.check(project,node_id,config['alias'],scope=execution_scope)
    if config.get('runtime_source')=='script':
        template_id=template_id or servers.selected_template(config)
        if template_id:config=servers.with_script(config,template_id,'wannier90')
    resource = servers.preview_for_prepare(config, probe_id, node, cores, resource_reason, purpose='wannier90')
    probe_id=resource.get('probe_id',probe_id)
    if not resource['resource_ready']:
        raise ValueError('实时资源检查未通过：' + '; '.join(resource['blockers']))
    if frozen:
        from .source_files import snapshot as file_snapshot
        current=file_snapshot(config['alias'],frozen['source_path'],frozen['source_files'],ex.ssh,mode=frozen.get('verification_mode','full'))
        if current!=frozen['source_files']:raise ValueError('冻结矩阵来源发生变化，请重新审查')
        source={'files':current,'source_path':frozen['source_path'],'win_source_name':frozen['source_win_name'],'win':frozen['win'],'spinors':frozen['spinors'],'verification_mode':frozen.get('verification_mode','full')}
    else:
        source = inspect(project, node_id, parent_id, config['alias'], seed)
    _checked_win(source['win'], win)
    if frozen and source['files']!=frozen['source_files']:raise ValueError('冻结矩阵来源发生变化，请重新审查')
    if band_path:
        from . import wannier_bands
        if (band_path.get('project_id'), band_path.get('parent_attempt_id'), band_path.get('alias')) != (project['id'], parent_id, config['alias']):
            raise ValueError('能带路径来源与本次 Wannier90 输入不一致')
        wannier_bands.recheck(band_path)
        wannier_bands.assert_applied(win, band_path)
    from . import interactive_plots
    actual_reference = interactive_plots.energy_reference(project['id'], parent_id)
    if not isinstance(energy_reference, dict) or energy_reference.get('outcar_sha256') != actual_reference['outcar_sha256'] or not math.isclose(
            float(energy_reference.get('fermi_eV', float('nan'))), actual_reference['fermi_eV'], abs_tol=1e-9):
        raise ValueError('VASP-Wannier OUTCAR 能量参考已变化，请重新读取并设置能窗')
    relative_windows = relative_windows or {}
    actual_values = parameter_values(win)
    for tag, relative in relative_windows.items():
        if tag not in WINDOW_TAGS or not isinstance(relative, (int, float)) or not math.isfinite(relative):
            raise ValueError('相对能窗参数无效')
        if not actual_values[tag] or not math.isclose(float(actual_values[tag]), relative + actual_reference['fermi_eV'], abs_tol=1e-7):
            raise ValueError(tag + ' 相对值与最终 .win 绝对值不一致')
    script, script_source = _render_script(config, resource['profile'], template_id)
    script = ex.with_exit_trap(script)
    root = remote_paths.normalize_workspace_root(config.get('remote_workspace_root'))
    snapshot = remote_paths.parse_workspace_probe(ex.ssh(config['alias'], remote_paths.workspace_probe_command(root), timeout=30), root)
    scope=execution_scope or node_id
    stage = next(n['data'] for n in project['nodes'] if n['id']==scope)
    slug = remote_paths.readable_slug(project['remoteSlug'], 'remoteSlug')
    if parent.get('project_slug') and parent['project_slug'] != slug:
        raise ValueError('项目已有上游 attempt，远端项目标识不可改变')
    stage_slug = remote_paths.readable_slug(stage['stageSlug'], 'stageSlug', 48)
    sequence = stage['stageSequence']
    for older in ex.history(project['id']):
        if older.get('execution_scope',older.get('node_id')) == scope and older.get('path_schema') == 'fpilot.remote-attempt-path' and (
                older.get('stage_slug') != stage_slug or older.get('stage_sequence') != sequence):
            raise ValueError('该 Wannier90 节点已有 attempt，远端阶段标识不可改变')
    key = attempt_id or uuid.uuid4().hex
    segment = datetime.now().astimezone().strftime('%Y%m%d-%H%M%S') + '-' + key[:4]
    relative = remote_paths.attempt_relative_path(slug, sequence, stage_slug, segment)
    absolute = remote_paths.absolute_attempt_path(snapshot['absolute_path'], relative)
    bundle = {'schema': 'fpilot.wannier90-frozen-bundle.v1', 'project_id': project['id'],
              'node_id': node_id, 'parent_attempt_id': parent_id, 'source_path': source['source_path'],
              'source_files': source['files'], 'source_win_name': source['win_source_name'],
              'verification_mode': source.get('verification_mode', 'full'),
              'source_win_sha256': source['files'][source['win_source_name']]['sha256'],
              'win': win, 'win_sha256': _sha(win.encode()), 'spinors': source['spinors'],
              'seed': seed,
              'input_reason': input_reason.strip(), 'energy_reference': actual_reference,
              'relative_windows': relative_windows,
              'absolute_windows': {tag:actual_values[tag] for tag in WINDOW_TAGS if actual_values[tag]},
              'band_path': band_path,
              'created_at': ex.now()}
    if frozen:
        bundle=frozen
    bundle_raw = json.dumps(bundle, ensure_ascii=False, sort_keys=True).encode()
    bundle_id = _sha(bundle_raw)
    from .inputs import put_file
    put_file(ROOT / 'wannier-bundles' / (bundle_id + '.json'), bundle_raw)
    scheduler = servers.normalize_scheduler(config['scheduler'])
    stored_config = {**config, 'remote_workspace_root': root}
    from .workbench_settings import snapshot as settings_snapshot
    record = {'settings_snapshot':settings_snapshot(config['alias'],'wannier90',config,script_source.get('id')),'id': key, 'state': 'PREPARED', 'kind': 'wannier90', 'created_at': ex.now(),
              'project_id': project['id'], 'flow_id': 'single', 'node_id': node_id,
              'execution_scope':scope,'branch_scopes':binding['scopes'],
              **({'scan_batch_id':scan_batch_id,'scan_case_id':scan_case_id,'scan_case_table_hash':scan_case_table_hash} if scan_batch_id else {}),
              'project_slug': slug, 'stage_sequence': sequence, 'stage_slug': stage_slug,
              'attempt_segment': segment, 'bundle_id': bundle_id, 'config': stored_config,
              'probe_id': probe_id, 'node': node, 'cores': cores, 'reason': resource_reason,
              'profile': resource['profile'], 'scheduler': scheduler,
              'remote_workspace_root': root, 'remote_workspace_root_abs': snapshot['absolute_path'],
              'remote_relative_path': relative, 'relative_path': relative,
              'remote_path': remote_paths.join_workspace(root, relative), 'remote_path_absolute': absolute,
              'remote_project_path_absolute': snapshot['absolute_path'].rstrip('/') + '/material-projects/' + slug,
              'path_schema': 'fpilot.remote-attempt-path', 'remote_host': config['alias'],
              'script': script, 'script_sha256': _sha(script.encode()), 'script_source': script_source,
              'parent_attempt_id': parent_id, 'source_snapshot': source, 'seed': seed,
              'live_energy_reference': actual_reference,
              'source_file_plan': {name: {'target': name, 'mode': 'hardlink', **source['files'][name]}
                                   for name in source_names(seed)[1:]},
              'input_audit': {'status': 'PASS', 'source_files': source['files'], 'win_sha256': bundle['win_sha256']},
              'job_id': None, 'note': 'Wannier90 矩阵将以同服务器硬链接复用；.win 为本次独立文件，尚未提交。'}
    with closing(ex.db()) as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT body FROM attempts WHERE id=?',(key,)).fetchone()
        if row:
            existing=json.loads(row[0])
            if (existing.get('bundle_id'),existing.get('execution_scope'),existing.get('scan_case_id'),existing.get('config'),existing.get('profile')) != (record['bundle_id'],scope,scan_case_id,record['config'],record['profile']):raise ValueError('原 attempt 合同不同')
            return existing
        recycle.assert_available('attempts',key,record)
        c.execute('INSERT INTO attempts VALUES(?,?,?)', (key, 'PREPARED', json.dumps(record, ensure_ascii=False)))
        control.journal(c, key, 'PREPARED', {'parent_attempt_id': parent_id}); c.commit()
    return record


def _bundle(record):
    key = record['bundle_id']
    if not re.fullmatch(r'[a-f0-9]{64}', key):
        raise ValueError('Wannier90 冻结包 ID 无效')
    raw = (ROOT / 'wannier-bundles' / (key + '.json')).read_bytes()
    if _sha(raw) != key:
        raise ValueError('Wannier90 冻结包哈希校验失败')
    return json.loads(raw)


def submit(key):
    record = ex.get(key)
    from . import recycle
    recycle.assert_available('attempts',key,record)
    if record.get('kind') != 'wannier90' or record['state'] != 'PREPARED':
        return record
    if record.get('scan_batch_id'):
        from . import scans, wannier_scans
        batch=scans.get(record['scan_batch_id'])
        recycle.assert_available('scans',batch['id'],batch)
        case=next((c for c in batch['cases'] if c['id']==record.get('scan_case_id')),None)
        if not wannier_scans.is_window(batch) or batch['state']!='ACTIVE':
            raise ValueError('能窗批次未授权、已暂停或被回收；没有派发')
        if not case or (case.get('attempt_id'),case['bundle_id'],batch['scan_node_id'],batch['case_table_hash']) != (record['id'],record['bundle_id'],record['execution_scope'],record['scan_case_table_hash']):
            raise ValueError('准确 case 派发身份与批次不一致')
        recycle.assert_available('wannier-inputs',batch['source_frozen_id'])
        with closing(ex.db()) as db:
            active=[json.loads(r[0]) for r in db.execute('SELECT body FROM attempts WHERE state IN ('+','.join('?' for _ in control.ACTIVE)+')',control.ACTIVE)]
        if sum(r.get('scan_batch_id')==batch['id'] for r in active)>=batch['authorization']['max_parallel']:
            raise ValueError('在途任务数已达上限：本轮并行额度已用完')
    from .workbench_settings import assert_current
    assert_current(record.get('settings_snapshot'))
    from . import fast_dispatch
    fast=fast_dispatch.enabled(record)
    bundle = _bundle(record)
    recycle.assert_available('wannier-inputs',record['bundle_id'])
    recycle.assert_available('attempts',bundle['parent_attempt_id'])
    if not fast:
        if bundle.get('band_path'):
            from . import wannier_bands
            wannier_bands.recheck(bundle['band_path'])
        from . import interactive_plots
        actual_reference = interactive_plots.energy_reference(record['project_id'], bundle['parent_attempt_id'])
        if actual_reference['outcar_sha256'] != bundle['energy_reference']['outcar_sha256'] or not math.isclose(
                actual_reference['fermi_eV'], bundle['energy_reference']['fermi_eV'], abs_tol=1e-9):
            raise ValueError('来源 OUTCAR 能量参考已变化，请重新准备')
    fresh = {'id':record['probe_id']} if fast else servers.capacity_probe(record['config'], purpose='wannier90')
    resource = {'resource_ready':True,'profile':record['profile']} if fast else servers.preview(record['config'], fresh['id'], record['node'], record['cores'], record['reason'], purpose='wannier90')
    if not resource['resource_ready'] or resource['profile'] != record['profile']:
        raise ValueError('Wannier90 实时资源已变化，请重新准备')
    script, source = _render_script(record['config'], record['profile'], record['script_source']['id'])
    if _sha(ex.with_exit_trap(script).encode()) != record['script_sha256'] or source != record['script_source']:
        raise ValueError('Wannier90 脚本已变化，请重新准备')
    # Recheck large files in place; they are never downloaded to the Mac.
    # The PREPARED record already binds the parent and project graph.  Recheck
    # the source directory directly, without needing an editable project draft.
    selected_names = source_names(bundle['seed'], bundle.get('source_win_name'))
    names = ' '.join(shlex.quote(name) for name in selected_names)
    source_path = remote_paths.shell_path(bundle['source_path'])
    if not fast:
        if bundle.get('verification_mode', 'full') == 'lightweight':
            from .source_files import snapshot
            current = snapshot(record['config']['alias'], bundle['source_path'], selected_names, ex.ssh)
            if current != bundle['source_files']:
                raise ValueError('来源接口文件元数据或小型输入内容已变化，没有提交')
        else:
            command = 'set -e\ncd ' + source_path + '\nfor file in ' + names + "; do test -f \"$file\" && test ! -L \"$file\" && test -s \"$file\"; printf \"FPILOT_W90_FILE=%s|\" \"$file\"; sha256sum \"$file\" | awk '{print $1}'; done\n"
            receipt = ex.ssh(record['config']['alias'], command, timeout=180)
            if receipt['exit_code'] != 0:
                raise ValueError('来源接口文件复核失败，没有提交')
            current = dict(re.findall(r'^FPILOT_W90_FILE=([^|\n]+)\|([a-f0-9]{64})$', receipt['stdout'], re.M))
            if current != {name: info['sha256'] for name, info in bundle['source_files'].items()}:
                raise ValueError('来源接口文件内容已变化，没有提交')
    with closing(ex.db()) as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT state FROM attempts WHERE id=?', (key,)).fetchone()
        if not row or row[0] != 'PREPARED':
            c.rollback(); return ex.get(key)
        recycle.assert_available('attempts',key,record)
        control.enforce_limits(c, record)
        from . import branch_bindings
        branch_bindings.reserve(c,record)
        record.update(state='DISPATCHING', dispatch_started_at=ex.now(), final_probe_id=fresh['id'])
        c.execute('UPDATE attempts SET state=?,body=? WHERE id=?', ('DISPATCHING', json.dumps(record, ensure_ascii=False), key))
        control.journal(c, key, 'DISPATCHING', {'parent_attempt_id': bundle['parent_attempt_id']}); c.commit()
    scheduler = record['scheduler']
    script_name = ex.script_name(scheduler)
    files = {bundle['seed'] + '.win': bundle['win'].encode(), script_name: record['script'].encode(),
             '_fpilot/attempt.json': json.dumps({'schema': 'fpilot.web-attempt.v1', 'id': key, 'stage': 'wannier90',
                                                  'parent_attempt_id': bundle['parent_attempt_id'], 'bundle_id': record['bundle_id']}).encode(),
             '_fpilot/source.json': json.dumps({**{k: v for k, v in bundle.items() if k != 'win'},
                                                 'source_file_plan': record['source_file_plan']}, ensure_ascii=False).encode()}
    files['input-manifest.sha256'] = ''.join(_sha(data) + '  ' + name + '\n' for name, data in files.items()).encode()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name, data in files.items():
            item = tarfile.TarInfo(name); item.size = len(data); item.mode = 0o600
            archive.addfile(item, io.BytesIO(data))
    destination = remote_paths.shell_path(record['remote_path_absolute'])
    parent_dir = shlex.quote(str(PurePosixPath(record['remote_path_absolute']).parent))
    project_dir = remote_paths.shell_path(record['remote_project_path_absolute'])
    identity = shlex.quote(json.dumps({'schema':'fpilot.remote-project.v1','project_id':record['project_id'],'project_slug':record['project_slug']}, ensure_ascii=False, separators=(',', ':')))
    link_names = [name for name in selected_names if name != bundle.get('source_win_name', bundle['seed'] + '.win')]
    expected_links = {name: {'target': name, 'mode': 'hardlink', **bundle['source_files'][name]} for name in link_names}
    if 'source_file_plan' not in record:
        raise ValueError('此 Wannier90 任务按旧版复制策略准备；请重新准备以确认硬链接策略')
    if expected_links != record['source_file_plan']:
        raise ValueError('Wannier90 矩阵链接策略与准备时不同，没有提交')
    links = ex.source_file_commands({'source_path': bundle['source_path']}, expected_links)
    submit_command = ('sbatch --parsable --export=ALL,W90_SEED=' + bundle['seed'] + ' ' + script_name if scheduler == 'slurm'
                      else 'qsub -v W90_SEED=' + bundle['seed'] + ' ' + script_name)
    remote = f'''set -e
root={remote_paths.shell_path(record['remote_workspace_root'])}
{remote_paths.workspace_dispatch_guard(record['remote_workspace_root'],record['remote_workspace_root_abs'])}
project_dir={project_dir}
if test -d "$project_dir"; then
  if test -f "$project_dir/.fpilot/project.json"; then
    test "$(cat "$project_dir/.fpilot/project.json")" = {identity}
  else
    test -z "$(find "$project_dir" -mindepth 1 -maxdepth 1 -print -quit)"
  fi
else mkdir -p "$project_dir"; fi
mkdir -p "$project_dir/.fpilot"
if ! test -e "$project_dir/.fpilot/project.json"; then printf '%s' {identity} > "$project_dir/.fpilot/project.json"; fi
dest={destination}
mkdir -p {parent_dir}
mkdir "$dest"
tar -xf - -C "$dest"
cd "$dest"
sha256sum -c input-manifest.sha256
{links}
mkdir .submit-once
{submit_command} > .job-id
printf 'FPILOT_JOB_ID='
cat .job-id
'''
    if fast:
        remote=fast_dispatch.prefix(record,'wannier90')+fast_dispatch.source_guards(bundle['source_path'],bundle['source_files'])+remote.replace('mkdir "$dest"','mkdir "$dest"\nfpilot_created=1').replace(submit_command+' > .job-id','fpilot_submitting=1\n'+submit_command+' > .job-id')
    try:
        receipt = ex.ssh(record['config']['alias'], remote, stream.getvalue(), timeout=180)
        if fast and fast_dispatch.known_not_submitted(record,receipt):return ex.get(key)
        token = r'\d+(?:\.[A-Za-z0-9_.-]+)?' if scheduler == 'pbs' else r'\d+(?:;[A-Za-z0-9_.-]+)?'
        match = re.search(r'^FPILOT_JOB_ID=(' + token + r')\s*$', receipt['stdout'], re.M)
        job_id = match.group(1).split(';', 1)[0] if match else None
        record.update(state='SUBMITTED' if job_id and receipt['exit_code'] == 0 else 'UNKNOWN',
                      job_id=job_id, dispatch_receipt=receipt, updated_at=ex.now())
        ex.save(record)
        return ex.get(key)
    except BaseException:
        record.update(state='UNKNOWN', updated_at=ex.now())
        ex.save(record)
        raise


def refresh(key):
    record = ex.get(key)
    if record['state'] in ('PREPARED', 'COMPLETED', 'FAILED'):
        return record
    root = remote_paths.shell_path(ex.attempt_path(record)['shell'])
    scheduler = record['scheduler']
    seed = source_names(record['seed'])[0][:-4]
    command = 'cd ' + root + ''' || exit 3
if test -s .job-id; then printf 'FPILOT_JOB_ID='; cat .job-id; fi
if test -s .exit-status; then printf 'FPILOT_EXIT='; cat .exit-status; fi
if test -s ''' + seed + '''.wout; then
  printf 'FPILOT_DONE='; grep -c 'All done: wannier90 exiting' ''' + seed + '''.wout || true
  printf 'FPILOT_FINAL='; grep -c 'Final State' ''' + seed + '''.wout || true
  printf 'FPILOT_DIS='; grep -c 'Disentanglement convergence criteria satisfied' ''' + seed + '''.wout || true
fi
'''
    if record.get('job_id'):
        job = shlex.quote(record['job_id'])
        if scheduler == 'pbs':
            command += ex.pbs_history_command(record['job_id'])
        else:
            command += "squeue -h -j {} -o 'FPILOT_SLURM_LIVE=%T' || true\n".format(job)
            command += "sacct -n -P -j {} --format=JobIDRaw,State,ExitCode 2>/dev/null | awk -F'|' '$1 == \"{}\" {{print \"FPILOT_SLURM_ACCOUNT=\"$2\"|\"$3; exit}}' || true\n".format(job, record['job_id'])
    receipt = ex.ssh(record['config']['alias'], command, timeout=30)
    out = receipt['stdout']
    exit_match = re.search(r'^FPILOT_EXIT=(\d+)', out, re.M)
    done = re.search(r'^FPILOT_DONE=([1-9]\d*)', out, re.M) is not None
    final = re.search(r'^FPILOT_FINAL=([1-9]\d*)', out, re.M) is not None
    disentangled = re.search(r'^FPILOT_DIS=([1-9]\d*)', out, re.M) is not None
    pbs_state = re.search(r'job_state\s*=\s*([A-Z])', out)
    slurm_live = re.search(r'^FPILOT_SLURM_LIVE=([A-Z_+]+)', out, re.M)
    slurm_account = re.search(r'^FPILOT_SLURM_ACCOUNT=([A-Z_+]+)\|([^\s]+)', out, re.M)
    trace_exit, trace_complete = ex.pbs_history_evidence(out) if scheduler == 'pbs' else (None, False)
    terminal = bool((pbs_state and pbs_state.group(1) == 'C') or (trace_complete and trace_exit == 0)) if scheduler == 'pbs' else bool(slurm_account and slurm_account.group(1).split('+',1)[0] == 'COMPLETED')
    state = 'UNKNOWN'
    if exit_match:
        state = 'FAILED' if exit_match.group(1) != '0' or not done else 'COMPLETED' if terminal else 'EXITING'
    elif pbs_state:
        state = {'Q':'QUEUED','R':'RUNNING','H':'HELD','E':'EXITING'}.get(pbs_state.group(1), 'UNKNOWN')
    elif slurm_live:
        state = {'PENDING':'QUEUED','RUNNING':'RUNNING','COMPLETING':'EXITING'}.get(slurm_live.group(1), 'UNKNOWN')
    elif slurm_account and slurm_account.group(1).split('+',1)[0] in {'FAILED','CANCELLED','TIMEOUT','NODE_FAIL','OUT_OF_MEMORY','PREEMPTED'}:
        state = 'FAILED'
    record.update(state=state, status_receipt=receipt, updated_at=ex.now(),
                  evidence={'scheduler':scheduler,
                            'scheduler_state':pbs_state.group(1) if pbs_state else 'C' if trace_complete and trace_exit == 0 else slurm_live.group(1) if slurm_live else slurm_account.group(1) if slurm_account else None,
                            'scheduler_evidence_source':'tracejob' if trace_complete else 'qstat' if pbs_state else 'sacct' if slurm_account else None,
                            'wrapper_exit':int(exit_match.group(1)) if exit_match else None,
                            'wannier_all_done':done, 'localization_final_state':final,
                            'disentanglement_converged':disentangled,
                            'localization_converged':None,
                            'dft_wannier_agreement':'NOT_EVALUATED',
                            'scientific_validation':'NOT_PERFORMED'})
    result = ex.save(record)
    if result['state'] == 'FAILED':
        control.freeze_failure(key)
    return result


def configuration(key):
    if not re.fullmatch(r'[a-f0-9]{64}',key):raise ValueError('冻结输入 ID 无效')
    raw=(ROOT/'wannier-bundles'/(key+'.json')).read_bytes()
    if _sha(raw)!=key:raise ValueError('冻结 Wannier90 输入内容已变化')
    return json.loads(raw)


def freeze_configuration(project,node_id,parent_id,alias,input_reason,win,seed=SEED,energy_reference=None,relative_windows=None,band_path=None):
    from . import recycle
    recycle.assert_available('projects',project['id'],{'parent_attempt_id':parent_id})
    if not isinstance(input_reason,str) or not input_reason.strip():raise ValueError('请填写输入调整理由')
    _parent(project,node_id,parent_id,alias)
    from . import branch_bindings
    branch_bindings.check(project,node_id,alias)
    source = inspect(project, node_id, parent_id, alias, seed, max_cache_age=60)
    _checked_win(source['win'], win)
    if band_path:
        from . import wannier_bands
        if (band_path.get('project_id'), band_path.get('parent_attempt_id'), band_path.get('alias')) != (project['id'], parent_id, alias):
            raise ValueError('能带路径来源与本次 Wannier90 输入不一致')
        wannier_bands.recheck(band_path,force=False,max_cache_age=60)
        wannier_bands.assert_applied(win, band_path)
    from . import interactive_plots
    actual_reference = interactive_plots.energy_reference(project['id'], parent_id)
    if not isinstance(energy_reference, dict) or energy_reference.get('outcar_sha256') != actual_reference['outcar_sha256'] or not math.isclose(
            float(energy_reference.get('fermi_eV', float('nan'))), actual_reference['fermi_eV'], abs_tol=1e-9):
        raise ValueError('VASP-Wannier OUTCAR 能量参考已变化，请重新读取并设置能窗')
    relative_windows = relative_windows or {}
    actual_values = parameter_values(win)
    for tag, relative in relative_windows.items():
        if tag not in WINDOW_TAGS or not isinstance(relative, (int, float)) or not math.isfinite(relative):
            raise ValueError('相对能窗参数无效')
        if not actual_values[tag] or not math.isclose(float(actual_values[tag]), relative + actual_reference['fermi_eV'], abs_tol=1e-7):
            raise ValueError(tag + ' 相对值与最终 .win 绝对值不一致')
    bundle={'schema':'fpilot.wannier90-frozen-bundle.v1','project_id':project['id'],'node_id':node_id,'project':project,'alias':alias,
            'parent_attempt_id':parent_id,'source_path':source['source_path'],'source_files':source['files'],
            'source_win_name':source['win_source_name'],'source_win_sha256':source['files'][source['win_source_name']]['sha256'],
            'verification_mode':source.get('verification_mode','full'),'win':win,'win_sha256':_sha(win.encode()),
            'spinors':source['spinors'],'seed':seed,'input_reason':input_reason.strip(),'energy_reference':actual_reference,
            'relative_windows':relative_windows,'absolute_windows':{tag:actual_values[tag] for tag in WINDOW_TAGS if actual_values[tag]},
            'band_path':band_path,'created_at':ex.now()}
    raw=json.dumps(bundle,ensure_ascii=False,sort_keys=True).encode()
    key=_sha(raw)
    from .inputs import put_file
    put_file(ROOT/'wannier-bundles'/(key+'.json'),raw)
    recycle.reactivate('wannier-inputs',key)
    return {'id':key,**bundle}


def configurations(project_id,node_id):
    from . import recycle
    result=[]
    for path in (ROOT/'wannier-bundles').glob('*.json'):
        item=json.loads(path.read_text())
        if item.get('project_id')==project_id and item.get('node_id')==node_id and 'project' in item and not item.get('execution_scope') and not recycle.inactive('wannier-inputs',path.stem,item):
            result.append({'id':path.stem,'seed':item['seed'],'alias':item['alias'],'input_reason':item['input_reason'],'created_at':item['created_at'],'parent_attempt_id':item.get('parent_attempt_id'),'relative_windows':item.get('relative_windows',{}),'absolute_windows':item.get('absolute_windows',{}),'energy_reference':item.get('energy_reference',{}),'spinors':item.get('spinors')})
    return sorted(result,key=lambda x:(x['created_at'],x['id']),reverse=True)

from .fast_prepare import batched as _batched_prepare
prepare = _batched_prepare(prepare, 'wannier90')
from .fast_prepare import batched_freeze as _batched_freeze
freeze_configuration = _batched_freeze(freeze_configuration)


def _source_singleflight(function):
    from functools import wraps
    from . import remote_cache
    import inspect as python_inspect
    signature=python_inspect.signature(function)
    @wraps(function)
    def run(*args,**kwargs):
        values=signature.bind(*args,**kwargs);values.apply_defaults()
        identity={k:v for k,v in values.arguments.items() if k not in ('project','force','max_cache_age')}
        with remote_cache.singleflight(function.__name__,identity):return function(*values.args,**values.kwargs)
    return run

inspect=_source_singleflight(inspect)
discover=_source_singleflight(discover)

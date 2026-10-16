from .calculation_nodes import kind as calculation_kind, owner as attempt_owner, case_info
"""Explicitly authorized VASP graph runs backed by the ordinary attempt ledger."""
import copy
import hashlib
import json
import re
import uuid
from contextlib import closing
from pathlib import Path, PurePosixPath

from . import artifacts, band_path, execution as ex, execution_control as control
from . import inputs, servers, recycle, source_contracts
from .storage import validate_workspace
from .platform_runtime import exclusive_lock

KINDS = ('relax', 'scf', 'bands', 'dos', 'vasp-wannier')
MODEL_TAGS = ('GGA', 'ENCUT', 'PREC', 'ISPIN', 'LDAU', 'LDAUTYPE',
              'LDAUL', 'LDAUU', 'LDAUJ', 'LMAXMIX', 'LSORBIT',
              'LNONCOLLINEAR', 'SAXIS', 'GGA_COMPAT', 'MAGMOM')


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def database():
    db = ex.db()
    db.execute('CREATE TABLE IF NOT EXISTS chain_runs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, state TEXT NOT NULL, body TEXT NOT NULL)')
    return db


def save(run):
    if recycle.inactive('chains',run['id'],run):return run
    with closing(database()) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT body FROM chain_runs WHERE id=?', (run['id'],)).fetchone()
        current = json.loads(row[0]) if row else None
        if current and current['revision'] != run['revision']:
            db.rollback()
            raise ValueError('计算链状态已变化，请刷新后重试')
        if current == run:
            db.rollback()
            return run
        updated = {**run, 'revision': run['revision'] + 1, 'updated_at': ex.now()}
        packed = json.dumps(updated, ensure_ascii=False, allow_nan=False)
        if current:
            db.execute('UPDATE chain_runs SET state=?,body=? WHERE id=?',
                       (updated['state'], packed, run['id']))
        else:
            db.execute('INSERT INTO chain_runs VALUES(?,?,?,?)',
                       (run['id'], run['project_id'], run['state'], packed))
        db.commit()
    return updated


def get(run_id):
    with closing(database()) as db:
        row = db.execute('SELECT body FROM chain_runs WHERE id=?', (run_id,)).fetchone()
    if not row:
        raise ValueError('计算链运行不存在')
    return json.loads(row[0])


def status(run_id, project_id):
    """Local projection only: no remote reads or ledger writes."""
    run = get(run_id)
    if run['project_id'] != project_id:
        raise ValueError('计算链不属于当前项目')
    by_id = {step['node_id']: step for step in run['steps']}
    for step in run['steps']:
        if not step.get('attempt_id'):
            parent = by_id.get(step.get('parent_id'))
            if parent and parent['state'] != 'DONE' and step['state'] == 'PENDING':
                step['waiting_kind'] = 'DEPENDENCY'
                step['waiting_reason'] = '等待上游 '+parent['label']+' 完成并通过审核'
            continue
        try:
            attempt = ex.get(step['attempt_id'])
        except ValueError:
            continue
        if (attempt.get('project_id') != project_id or
                attempt.get('chain_run_id', run_id) != run_id):
            continue
        step['attempt_state'] = attempt['state']
        step['job_id'] = attempt.get('job_id')
        if attempt.get('note') and attempt['state'] in ('PREPARED', 'UNKNOWN'):
            step['waiting_reason'] = attempt['note']
    return run


def _dispatch_lock(run_id):
    # Hash the identity so a route parameter never becomes a filesystem path.
    return exclusive_lock(ex.ROOT/'chain-locks'/(digest(run_id)+'.lock'))


def _update_dispatch(run_id, **fields):
    # Pause/refresh can update the chain while a remote operation is in flight.
    # Merge only dispatch metadata into the latest revision, never its state.
    for _ in range(3):
        run = get(run_id)
        if 'initial_dispatch' not in run:
            return run
        run['initial_dispatch'].update(fields)
        try:
            return save(run)
        except ValueError:
            if get(run_id)['revision'] == run['revision']:
                raise
    return get(run_id)


def authorize_and_dispatch(run_id, **payload):
    request_hash = digest(payload)
    with _dispatch_lock(run_id) as owned:
        current = get(run_id)
        prior = current.get('initial_dispatch', {})
        if (current['project_id'] == payload['project_id'] and
                prior.get('request_hash') == request_hash and current.get('authorization')):
            return status(run_id, payload['project_id'])
        if not owned:
            raise ValueError('计算链正在核对或派发，请读取当前状态后重试')
        authorize(run_id, **payload, _initial_request_hash=request_hash)
        _dispatch_pass(run_id)
        return status(run_id, payload['project_id'])


def history(project_id):
    with closing(database()) as db:
        rows = db.execute('SELECT body FROM chain_runs WHERE project_id=? ORDER BY rowid DESC',
                          (project_id,)).fetchall()
    from .recycle import is_recycled
    return [item for row in rows if not recycle.inactive('chains',(item := json.loads(row[0]))['id'],item)]


def _selected(project, node_ids):
    validate_workspace({'activeId': project['id'], 'projects': [project]})
    if not isinstance(node_ids, list) or not 1 <= len(node_ids) <= 64 or len(node_ids) != len(set(node_ids)):
        raise ValueError('请选择 1–64 个不重复的 VASP 计算节点')
    nodes = {item['id']: item for item in project['nodes']}
    if any(key not in nodes or nodes[key]['data']['kind'] not in KINDS for key in node_ids):
        raise ValueError('首版计算链仅接受 VASP 计算节点')
    selected = set(node_ids)
    parents = {}
    for key in node_ids:
        incoming = [edge['source'] for edge in project['edges'] if edge['target'] == key]
        if len(incoming) != 1:
            raise ValueError('每个计算节点须有唯一直接上游')
        parent = incoming[0]
        kind, parent_kind = nodes[key]['data']['kind'], calculation_kind(nodes[parent], project)
        if kind in ('relax', 'scf') and parent_kind not in ('structure', 'relax', 'scf'):
            raise ValueError('relax / SCF 上游须为结构、relax 或 SCF')
        if kind in ('bands', 'dos', 'vasp-wannier') and parent_kind != 'scf':
            raise ValueError('非自洽 VASP 节点须直接依赖 SCF')
        parents[key] = parent
    ordered, pending = [], set(node_ids)
    while pending:
        ready = [key for key in node_ids if key in pending and parents[key] not in pending]
        if not ready:
            raise ValueError('计算链存在循环依赖')
        ordered.extend(ready)
        pending.difference_update(ready)
    return nodes, parents, ordered


def _workflow_project(project, ordered):
    result = copy.deepcopy(project)
    nodes = {item['id']: item for item in result['nodes']}
    differences = {}
    for key in ordered:
        item = nodes[key]
        kind = item['data']['kind']
        values = item['data'].setdefault('parameters', {'values': {}}).setdefault('values', {})
        shared = result.get('parameters', {}).get('shared', {}).get('values', {})
        before = {tag: values.get(tag, shared.get(tag)) for tag in ('LWAVE', 'LCHARG')}
        if kind == 'relax':
            values['LWAVE'] = 'true'
            needs_charge = any(e['source'] == key and nodes[e['target']]['data']['kind'] in KINDS and 'CHGCAR' in source_contracts.plan(result, e['target'])['required_files'] for e in result['edges'])
            if needs_charge:values['LCHARG']='true'
            else:values.setdefault('LCHARG','false')
        elif kind == 'scf':
            values.update(LWAVE='true', LCHARG='true')
        else:
            values.setdefault('LWAVE', 'false')
            values.setdefault('LCHARG', 'false')
        differences[key] = {tag: {'before': before[tag], 'after': values[tag]}
                            for tag in before if before[tag] != values.get(tag)}
        direct_parent=next((e['source'] for e in result['edges'] if e['target']==key),None)
        if kind == 'bands' and nodes.get(direct_parent,{}).get('data',{}).get('kind')!='scan':
            upstream = key
            while True:
                upstream = next((e['source'] for e in result['edges'] if e['target'] == upstream), None)
                if not upstream:
                    raise ValueError('能带节点未连接结构来源')
                if nodes[upstream]['data']['kind'] == 'structure':
                    structure = nodes[upstream]['data'].get('structure')
                    if not structure:
                        raise ValueError('能带节点缺少已导入的结构')
                    proposal = band_path.generate(structure)
                    draft = item['data'].setdefault('inputDraft', {})
                    draft['path'] = proposal['path']
                    draft['path_structure_hash'] = proposal['structure_hash']
                    draft.pop('path_source_attempt_id', None)
                    draft.setdefault('line_points', '40')
                    break
    return result, differences


def _input_differences(report, manifest, files):
    current = report.get('files', {})
    old_incar = files.get('INCAR', b'')
    new_incar = current.get('INCAR', '').encode()
    tags = set(re.findall(r'^([A-Z][A-Z0-9_]*)\s*=', old_incar.decode(), re.M)) | set(re.findall(r'^([A-Z][A-Z0-9_]*)\s*=', new_incar.decode(), re.M))
    ignored = {'LWAVE', 'LCHARG', 'ISTART', 'ICHARG', 'NCORE', 'NPAR', 'KPAR'}
    differences = []
    for tag in sorted(tags - ignored):
        before, after = ex._incar_value(old_incar, tag), ex._incar_value(new_incar, tag)
        if before != after:
            differences.append({'field': tag, 'before': before, 'after': after})
    for name in ('KPOINTS', 'POSCAR', 'POTCAR'):
        old_name = 'POSCAR.reference' if name == 'POSCAR' and 'POSCAR.reference' in files else name
        new_name = 'POSCAR.reference' if name == 'POSCAR' and 'POSCAR.reference' in current else name
        before = manifest.get('files', {}).get(old_name, {}).get('sha256')
        after = (report.get('potcar') or {}).get('sha256') if name == 'POTCAR' else hashlib.sha256(current.get(new_name, '').encode()).hexdigest()
        if before != after:
            differences.append({'field': name, 'before': before, 'after': after})
    return differences


def _reuse(project, nodes, parents, ordered, reports, source_alias, choices, mode, context):
    required = {}
    for key in ordered:
        parent = parents[key]
        if reports[key].get('dependencies') and parent not in ordered and nodes[parent]['data']['kind'] not in ('structure','scan'):
            required.setdefault(parent, set()).update(item['file'] for item in reports[key].get('dependencies', []))
    if set(choices) - set(required):
        raise ValueError('复用来源不属于本次未执行的直接上游')
    bindings, sources, frozen_reports = {}, [], {}
    records = ex.history(project['id'], limit=None) if required else []
    records.sort(key=lambda record:(record.get('created_at',''),record['id']),reverse=True)
    for parent, names in required.items():
        draft = inputs.generate(project, parent, context)
        candidates = []
        for record in records:
            if attempt_owner(record) != parent:
                continue
            candidate = {'attempt_id': record['id'], 'job_id': record.get('job_id'),
                         'alias': record.get('config', {}).get('alias'), 'eligible': False,
                         'differences': [], 'reason': '', 'state': record['state']}
            try:
                if not source_alias:
                    raise ValueError('请先选择本次执行主机')
                if candidate['alias'] != source_alias:
                    raise ValueError('来源主机与本次执行主机不同')
                if record.get('path_schema') != 'fpilot.remote-attempt-path':
                    raise ValueError('来源不是受控的计算目录')
                verdict = convergence(record, nodes[parent]['data']['kind'])
                if verdict['status'] != 'CONVERGED':
                    raise ValueError(verdict.get('reason', '未确认最终步收敛'))
                manifest, files = source_contracts.actual_inputs(record)
                if manifest.get('node_id') != parent or manifest.get('stage') != nodes[parent]['data']['kind']:
                    raise ValueError('来源冻结输入与上游节点不符')
                for key in ordered:
                    if parents[key] == parent:
                        review = source_contracts.compatibility(reports[key]['source_plan'], record, reports[key])
                        if review['blockers']:
                            raise ValueError('；'.join(review['blockers']))
                from .source_files import snapshot
                receipt = snapshot(source_alias, ex.attempt_path(record)['shell'], sorted(names), ex.ssh, mode)
                candidate.update(eligible=True, differences=_input_differences(draft, manifest, files),
                                 evidence=verdict, files=receipt, source_path=ex.attempt_path(record)['shell'],
                                 bundle_id=record['bundle_id'], verification_mode=mode)
                frozen_reports[record['id']] = {'files': {'INCAR': files['INCAR'].decode()},
                                               'potcar': {'sha256': manifest['files']['POTCAR']['sha256']}}
            except (ValueError, KeyError, OSError) as exc:
                candidate['reason'] = str(exc)
            candidates.append(candidate)
        eligible = [item for item in candidates if item['eligible']]
        selected = next((item for item in eligible if item['attempt_id'] == choices.get(parent)), None)
        if not choices.get(parent):
            consumes_electronic = any(parents[key] == parent and reports[key]['source_plan']['electronic_usage'] != 'none' for key in ordered)
            matching=[item for item in eligible if not consumes_electronic or not item['differences']]
            selected=matching[0] if matching else None
        reason = '' if selected else ('所选历史来源当前不可用' if choices.get(parent) else
                                     '有多个来源或输入差异，请显式选择历史结果' if eligible else
                                     '尚无提供 ' + '、'.join(sorted(names)) + ' 的合格结果')
        sources.append({'node_id': parent, 'label': nodes[parent]['data']['label'],
                        'required_files': sorted(names), 'candidates': candidates,
                        'selected_attempt_id': selected['attempt_id'] if selected else None,
                        'reason': reason})
        if selected:
            bindings[parent] = {key: selected[key] for key in ('attempt_id', 'bundle_id', 'source_path', 'files', 'evidence', 'verification_mode', 'alias', 'differences')}
            reports[parent] = frozen_reports[selected['attempt_id']]
        else:
            for key in ordered:
                if parents[key] == parent:
                    reports[key]['errors'].append(nodes[parent]['data']['label'] + '：' + reason)
    return bindings, sources


def preview(project, node_ids, source_alias=None, reuse_sources=None, verification_mode='lightweight'):
    return _preview(project, node_ids, source_alias, reuse_sources, verification_mode)[0]


def _preview(project, node_ids, source_alias=None, reuse_sources=None, verification_mode='lightweight'):
    if verification_mode not in ('lightweight', 'full'):
        raise ValueError('文件核验策略无效')
    nodes, parents, ordered = _selected(project, node_ids)
    effective, output_diff = _workflow_project(project, ordered)
    effective_nodes = {item['id']: item for item in effective['nodes']}
    context = inputs.GenerationContext(effective)
    steps, reports = [], {}
    for key in ordered:
        report = inputs.generate(effective, key, context)
        if source_alias:
            from . import branch_bindings
            try: branch_bindings.check(effective,key,source_alias)
            except ValueError as exc: report['errors'].append(str(exc))
        if report.get('restart_required'):
            report['errors'].append('计算链的原始节点不能预设 ICHARG=1；请使用图依赖或先单独完成手动续算')
        if nodes[parents[key]]['data']['kind']=='scan':
            selection=source_contracts.resolve(effective,key)
            report['errors'].extend(selection['blockers'])
        reports[key] = report
        incar = report.get('files', {}).get('INCAR', '').encode()
        steps.append({'node_id': key, 'label': nodes[key]['data']['label'],
                      'stage': nodes[key]['data']['kind'], 'parent_id': parents[key] if report.get('dependencies') or nodes[parents[key]]['data']['kind']=='structure' else None,
                      'errors': report['errors'], 'warnings': report['warnings'],
                      'output_diff': output_diff[key],
                      'pending_sources': [{'node_id': item['node_id'], 'file': item['file'],
                                           'sha256': '待上游完成后物化'}
                                          for item in report.get('dependencies', [])],
                      'conditional_retry': ({'source_files': ['CONTCAR', 'WAVECAR'],
                                             'incar_diff': {'ISTART': str(source_contracts.wave_start('relax', incar, True)), 'ICHARG': '0'}}
                                            if nodes[key]['data']['kind'] == 'relax' else
                                            {'source_files': ['POSCAR', 'WAVECAR', 'CHGCAR'],
                                             'incar_diff': {'ISTART': '1', 'ICHARG': '1'}}
                                            if nodes[key]['data']['kind'] == 'scf' else None),
                      'nelm': ex._incar_value(incar, 'NELM') or '60 (VASP 默认)',
                      'nsw': ex._incar_value(incar, 'NSW'),
                      'requires_ncl': any(ex._incar_value(incar, tag) in ('.TRUE.', 'TRUE', 'T')
                                          for tag in ('LSORBIT', 'LNONCOLLINEAR')),
                      'input_hashes': {**{name: hashlib.sha256(text.encode()).hexdigest()
                                          for name, text in report.get('files', {}).items()},
                                       **({'POTCAR': report['potcar']['sha256']}
                                          if report.get('potcar') else {})},
                      'path_policy': {'algorithm': 'seekpath.get_path_orig_cell',
                                      'symprec': 0.01,
                                      'line_points': effective_nodes[key]['data'].get('inputDraft', {}).get('line_points')}
                      if nodes[key]['data']['kind'] == 'bands' else None})
    bindings, sources = _reuse(effective, nodes, parents, ordered, reports, source_alias,
                               reuse_sources or {}, verification_mode, context)
    for step in steps:
        binding = bindings.get(step['parent_id'])
        if binding:
            step['reuse_source_attempt_id'] = binding['attempt_id']
            for item in step['pending_sources']:
                info = binding['files'].get(item['file'], {})
                item['sha256'] = info.get('sha256') or '轻量核验：大小与修改时间；已冻结历史来源'
    mismatches = []
    for key in ordered:
        parent = parents[key]
        if parent not in reports:
            continue
        a, b = reports[parent], reports[key]
        policy = b.get('source_plan')
        if policy and policy['electronic_usage'] == 'none' and 'WAVECAR' not in policy['required_files']:
            continue
        if policy and a.get('files', {}).get('INCAR') and b.get('files', {}).get('INCAR'):
            review = source_contracts.review_inputs(policy, {'INCAR': a['files']['INCAR'].encode(),
                                                        'KPOINTS': a['files'].get('KPOINTS', '').encode(),
                                                        'POTCAR': inputs.potcar_bytes(a['potcar']['sha256'])}, b)
            b['errors'].extend(review['blockers'])
        old, new = a.get('files', {}).get('INCAR', '').encode(), b.get('files', {}).get('INCAR', '').encode()
        for tag in MODEL_TAGS:
            before, after = ex._incar_value(old, tag), ex._incar_value(new, tag)
            if before != after:
                mismatches.append({'parent_id': parent, 'node_id': key, 'field': tag,
                                   'before': before, 'after': after})
        before, after = (a.get('potcar') or {}).get('sha256'), (b.get('potcar') or {}).get('sha256')
        if before != after:
            mismatches.append({'parent_id': parent, 'node_id': key, 'field': 'POTCAR',
                               'before': before, 'after': after})
    result = {'project_id': project['id'], 'node_ids': ordered, 'steps': steps,
            'source_alias': source_alias, 'reuse_sources': {key: value['attempt_id'] for key, value in bindings.items()},
            'reuse_bindings': bindings, 'sources': sources, 'verification_mode': verification_mode,
            'mismatches': mismatches, 'mismatch_hash': digest(mismatches),
            'definition_hash': digest({'project': effective, 'nodes': ordered, 'source_alias': source_alias,
                                       'reuse_bindings': bindings, 'verification_mode': verification_mode}),
            'max_jobs': sum(2 if step['stage'] in ('relax', 'scf') else 1 for step in steps),
            'ready': all(not step['errors'] for step in steps)}
    return result, effective, reports


def freeze(project, node_ids, reason, expected_definition_hash, source_alias=None, reuse_sources=None, verification_mode='lightweight'):
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ValueError('请填写计算链输入理由')
    report, effective, reports = _preview(project, node_ids, source_alias, reuse_sources, verification_mode)
    if not report['ready'] or report['definition_hash'] != expected_definition_hash:
        raise ValueError('计算链输入或预览已变化；请重新预览')
    steps = []
    for item in report['steps']:
        parent_id=next(e['source'] for e in effective['edges'] if e['target']==item['node_id'])
        parent_node=next(n for n in effective['nodes'] if n['id']==parent_id)
        frozen = inputs.freeze(effective, item['node_id'], reason, report=reports[item['node_id']], verification_mode=verification_mode, defer_sources=parent_node['data']['kind']!='scan')
        steps.append({**item, 'bundle_id': frozen['id'], 'state': 'PENDING',
                      'attempts': [], 'attempt_id': None, 'final_attempt_id': None,
                      'convergence': None, 'blocker': None})
    run = {'schema': 'fpilot.chain-run.v1', 'id': uuid.uuid4().hex, 'project_id': project['id'],
           'project': effective, 'node_ids': report['node_ids'], 'steps': steps,
           'reuse_bindings': report['reuse_bindings'], 'source_alias': source_alias,
           'verification_mode': verification_mode,
           'definition_hash': report['definition_hash'],
           'mismatch_hash': report['mismatch_hash'], 'mismatches': report['mismatches'],
           'max_jobs': report['max_jobs'], 'reason': reason.strip(),
           'state': 'FROZEN', 'revision': 0, 'created_at': ex.now()}
    run['plan_hash'] = digest({key: run[key] for key in ('definition_hash', 'mismatch_hash', 'max_jobs', 'node_ids')})
    return save(run)


def authorize(run_id, project_id, plan_hash, mismatch_hash, config, resources, approved_previews,
              _initial_request_hash=None):
    if config.get('scheduler')=='custom':raise ValueError('自定义命令仅手动运行，本轮不自动推进计算链或迭代')
    run = get(run_id)
    recycle.assert_available('chains',run['id'],run)
    run.pop('local_cleanup_blocker',None)
    if run['project_id'] != project_id or run['state'] != 'FROZEN' or run['plan_hash'] != plan_hash:
        raise ValueError('冻结计算链已变化或不属于当前项目')
    if mismatch_hash != run['mismatch_hash']:
        raise ValueError('模型差异尚未按当前预览确认')
    if not isinstance(resources, dict) or set(resources) != set(run['node_ids']):
        raise ValueError('请逐节点设置资源')
    if PurePosixPath(config.get('vasp_executable', '')).name not in ('vasp_std', 'vasp_ncl'):
        raise ValueError('请选择已登记的 VASP 程序')
    from . import branch_bindings
    for node_id in run['node_ids']:branch_bindings.check(run['project'],node_id,config['alias'])
    checked = {}
    if run.get('reuse_bindings') and config.get('alias') != run.get('source_alias'):
        raise ValueError('执行主机须与冻结的历史来源主机一致')
    probes = servers.probe_many([_node_config(config, resources[key]) for key in run['node_ids']])
    by_id = {step['node_id']: step for step in run['steps']}
    for key in run['node_ids']:
        choice = resources[key]
        node_config = _node_config(config, choice)
        if by_id[key]['requires_ncl'] and PurePosixPath(node_config['vasp_executable']).name != 'vasp_ncl':
            raise ValueError('SOC/非共线节点须使用 vasp_ncl：' + by_id[key]['label'])
        candidates = choice.get('candidates') if isinstance(choice, dict) else None
        if candidates is None:
            candidates = [choice]
        if not isinstance(candidates, list) or not candidates or len(candidates) > 64:
            raise ValueError('请选择 1–64 个候选计算节点')
        if len({item.get('node') for item in candidates}) != len(candidates):
            raise ValueError('候选计算节点不能重复')
        probe_key = servers.config_hash(node_config)
        probe = probes[probe_key]
        approved = approved_previews.get(key, {})
        checked_candidates = []
        for candidate in candidates:
            rendered = servers.preview(node_config, probe['id'], candidate['node'],
                                       candidate.get('cores'), candidate.get('reason', ''))
            reviewed = approved.get(candidate['node'], {}) if choice.get('candidates') is not None else approved
            if (not (rendered['resource_ready'] or _capacity_wait(rendered)) or
                    rendered['script'] != reviewed.get('script') or rendered['profile'] != reviewed.get('profile')):
                raise ValueError('节点资源或脚本预览已变化：' + key + '/' + candidate['node'])
            checked_candidates.append({'node': candidate['node'], 'cores': candidate.get('cores'),
                                       'reason': candidate.get('reason', ''),
                                       'profile': rendered['profile'],
                                       'script_sha256': hashlib.sha256(rendered['script'].encode()).hexdigest()})
        checked[key] = {'vasp_executable': node_config['vasp_executable'],
                        **({'resource_request':choice['resource_request']} if choice.get('resource_request') else {}),
                        **({'template_id':node_config['template_id'],'runtime_source':'script'} if node_config.get('runtime_source')=='script' and node_config.get('template_id') else {}),
                        'candidates': checked_candidates}
    from .workbench_settings import snapshot as settings_snapshot
    settings_snapshots={key:settings_snapshot(config['alias'],PurePosixPath(checked[key]['vasp_executable']).name,
                       _node_config(config,checked[key]),checked[key].get('template_id')) for key in run['node_ids']}
    run['authorization'] = {'settings_snapshots':settings_snapshots,'at': ex.now(), 'plan_hash': plan_hash,
                            'mismatch_hash': mismatch_hash, 'config': config,
                            'resources': checked, 'max_jobs': run['max_jobs']}
    run['state'] = 'ACTIVE'
    if _initial_request_hash:
        run['initial_dispatch'] = {'state': 'IN_PROGRESS', 'started_at': ex.now(),
                                   'request_hash': _initial_request_hash}
    return save(run)


def _node_config(config, choice):
    if choice.get('resource_request'):
        config = {**config, 'resource_request': choice['resource_request']}
    executable = PurePosixPath(choice.get('vasp_executable') or config['vasp_executable']).name
    if executable not in ('vasp_std', 'vasp_ncl'):
        raise ValueError('逐节点程序仅支持 vasp_std 或 vasp_ncl')
    from .workbench_settings import read
    settings=read()
    templates=json.loads(config.get('script_templates') or '{}')
    template=choice.get('template_id') or templates.get(executable)
    if not template and config.get('runtime_source')=='script':
        template=config.get('template_id') if servers.program_name(config)==executable else settings['defaults'].get(config['alias']+':'+executable)
        if not template and config.get('template_id'):
            from . import job_templates
            record=job_templates.get_template(config['template_id'],False)
            if record['alias']==config['alias'] and record['scheduler']==servers.normalize_scheduler(config['scheduler']) and job_templates.fits_program(record,executable):
                template=record['id']
    if template:return servers.with_script(config,template,executable)
    if config.get('runtime_source')=='script':raise ValueError('请为 '+executable+' 选择该服务器的已导入脚本')
    # Authorized registered steps carry an explicit executable; defaults must
    # never rewrite that historical contract or an initialized registered draft.
    explicit = choice.get('vasp_executable', '')
    if explicit.startswith('/'):
        return {**config, 'vasp_executable': explicit, 'runtime_source': 'registered'}
    if config.get('runtime_source') == 'registered' and PurePosixPath(config['vasp_executable']).name == executable:
        return config
    saved=settings['runtimes'].get(config['alias']+':'+executable,{})
    if saved:
        return {**config,**saved,'name':config['name'],'queue':config['queue'],'walltime':config['walltime']}
    if PurePosixPath(config['vasp_executable']).name==executable:return config
    registered=servers.select_profile(config['alias'],variant=executable)
    return {**config,'vasp_executable':registered['vasp_executable']}


def resource_preview(run_id, project_id, config, resources):
    run = get(run_id)
    if run['state'] != 'FROZEN' or run['project_id'] != project_id or set(resources) != set(run['node_ids']):
        raise ValueError('请选择当前冻结计算链的逐节点资源')
    if run.get('reuse_bindings') and config.get('alias') != run.get('source_alias'):
        raise ValueError('执行主机须与冻结的历史来源主机一致')
    configs = {step['node_id']: _node_config(config, resources[step['node_id']]) for step in run['steps']}
    probes = servers.probe_many(list(configs.values()))
    cache, result = {}, {}
    for step in run['steps']:
        key = step['node_id']
        node_config = configs[key]
        if step['requires_ncl'] and PurePosixPath(node_config['vasp_executable']).name != 'vasp_ncl':
            raise ValueError('SOC/非共线节点须使用 vasp_ncl')
        candidates = resources[key].get('candidates', [])
        if not isinstance(candidates, list) or not 1 <= len(candidates) <= 64 or len({item['node'] for item in candidates}) != len(candidates):
            raise ValueError('请选择 1–64 个不重复的候选计算节点')
        result[key] = {}
        for candidate in candidates:
            identity = digest({'config': node_config, 'candidate': candidate})
            if identity not in cache:
                cache[identity] = servers.preview(node_config, probes[servers.config_hash(node_config)]['id'],
                                                  candidate['node'], candidate.get('cores'), candidate.get('reason', ''))
            result[key][candidate['node']] = copy.deepcopy(cache[identity])
    return {'previews': result, 'runtime_combinations': len(probes)}


def _capacity_wait(rendered):
    from .candidate_pool import capacity_wait
    return capacity_wait(rendered)


def pause(run_id, project_id):
    run = get(run_id)
    if run['project_id'] != project_id or run['state'] != 'ACTIVE':
        raise ValueError('当前计算链不能暂停')
    run['state'] = 'PAUSED'
    return save(run)


def _attempt_blocker(attempt):
    if attempt['state']=='UNKNOWN':
        return 'attempt UNKNOWN；提交回执待核查，须核对原作业；不会自动重投'
    return attempt.get('note') or 'attempt '+attempt['state']+'；须核对原作业'


def _recover_blocked_step(step):
    if step['state']!='BLOCKED' or not step.get('attempt_id'):return False
    attempt=ex.get(step['attempt_id'])
    actual=attempt['state']
    if actual=='UNKNOWN':
        step['blocker']=_attempt_blocker(attempt)
        step['blocker_kind']='SUBMISSION_UNKNOWN'
        step.pop('waiting_reason',None)
        return False
    unknown_block=(step.get('blocker_kind')=='SUBMISSION_UNKNOWN' or
                   'UNKNOWN' in str(step.get('blocker')) or
                   '派发前检查未通过，没有调用调度器' in str(step.get('blocker')))
    # Reconcile only positive evidence for the same attempt. Failed/scientific
    # blockers retain their existing review and retry budget.
    if actual=='PREPARED' or (unknown_block and actual in ('SUBMITTED','QUEUED','RUNNING','HELD','EXITING','COMPLETED')):
        step['state'],step['blocker']=actual,None
        step.pop('blocker_kind',None)
        step.pop('waiting_reason',None)
        return True
    return False


def resume(run_id, project_id):
    run = get(run_id)
    recycle.assert_available('chains',run['id'],run)
    run.pop('local_cleanup_blocker',None)
    if run['project_id'] != project_id or run['state'] not in ('PAUSED', 'BLOCKED'):
        raise ValueError('当前计算链不能继续')
    was_blocked = run['state'] == 'BLOCKED'
    changed = False
    for step in run['steps']:
        if step['state'] == 'BLOCKED' and step.get('attempt_id'):
            actual = ex.get(step['attempt_id'])['state']
            if actual == 'UNKNOWN':
                if was_blocked:
                    raise ValueError('仍有 UNKNOWN attempt，须先核对原作业')
                continue
            changed = _recover_blocked_step(step) or changed
        elif (step['state'] == 'BLOCKED' and not step.get('attempt_id') and
              'UNKNOWN' not in str(step.get('blocker'))):
            step['state'] = 'RETRY_PENDING' if step['stage'] in ('relax', 'scf') and len(step['attempts']) == 1 else 'PENDING'
            step['blocker'] = None
            changed = True
    if was_blocked and any(step['state'] == 'BLOCKED' for step in run['steps']):
        raise ValueError('当前阻塞须创建新运行版本或人工处理')
    run['state'] = 'ACTIVE'
    return save(run)


def refresh(run_id, project_id):
    """Read original attempts; never probe capacity, prepare, or submit here."""
    run=get(run_id)
    if run['project_id']!=project_id:raise ValueError('计算链不属于当前项目')
    recycle.assert_available('chains',run_id,run)
    for step in run['steps']:
        if step.get('attempt_id') and step['state']!='DONE':
            attempt=ex.get(step['attempt_id'])
            if attempt['state'] in ('UNKNOWN','SUBMITTED','QUEUED','RUNNING','HELD','EXITING'):
                ex.refresh(attempt['id'])
    # Reload after remote reads, preserving a concurrent pause or authorization.
    run=get(run_id)
    for step in run['steps']:
        _recover_blocked_step(step)
    return save(run)


def convergence(record, stage):
    from .source_contracts import convergence as verdict
    return verdict(record, stage)


def _ionic_evidence(record):
    path = ex.remote_paths.shell_path(ex.attempt_path(record)['shell'])
    command = ('set -e\ncd ' + path + '\n'
               "printf 'FPILOT_IONIC='; grep -c 'reached required accuracy' OUTCAR || true\n"
               "printf 'FPILOT_IONIC_STEP='; awk '/^[[:space:]]*[0-9]+[[:space:]]+F=/{n=$1} END{print n+0}' OSZICAR\n")
    receipt = ex.ssh(record['config']['alias'], command, timeout=30)
    if receipt['exit_code'] != 0:
        return None, None
    match = re.search(r'^FPILOT_IONIC=(\d+)', receipt['stdout'], re.M)
    ionic = bool(match and int(match.group(1)) == 1)
    match = re.search(r'^FPILOT_IONIC_STEP=(\d+)', receipt['stdout'], re.M)
    ionic_step = int(match.group(1)) if match else None
    return ionic, ionic_step


def _band_bundle(run, step, source_id):
    source = ex.get(source_id)
    manifest, _ = ex.bundle(step['bundle_id'], allow_dependencies=True)
    structure_file = manifest.get('source_plan', {}).get('structure_file', 'CONTCAR')
    preview = artifacts.preview(source_id, structure_file, 'head', 262144, True)
    if preview.get('partial') or not preview.get('structure'):
        raise ValueError('SCF 实际结构不完整，能带路径未生成')
    proposal = band_path.generate(preview['structure'])
    project = copy.deepcopy(run['project'])
    node = next(item for item in project['nodes'] if item['id'] == step['node_id'])
    draft = node['data']['inputDraft']
    draft['path'] = proposal['path']
    draft['path_structure_hash'] = proposal['structure_hash']
    draft['path_source_attempt_id'] = source_id
    draft['path_source_file'] = structure_file
    frozen = inputs.freeze(project, step['node_id'], run['reason'], verification_mode=run.get('verification_mode', 'full'),defer_sources=True)
    step['bundle_id'] = frozen['id']
    step['actual_band_path'] = {'source_attempt_id': source_id,
                                'source_job_id': source.get('job_id'),
                                'structure_hash': proposal['structure_hash'],
                                'bundle_id': frozen['id']}


def _quota_available(alias):
    limits = control.settings()
    with closing(ex.db()) as db:
        active = [json.loads(row[0]) for row in db.execute(
            'SELECT body FROM attempts WHERE state IN (' + ','.join('?' for _ in control.ACTIVE) + ')',
            control.ACTIVE)]
    return len(active) < limits['max_inflight_total'] and sum(
        item['config']['alias'] == alias for item in active) < limits['max_inflight_per_host']


def tick():
    with closing(database()) as db:
        ids = [row[0] for row in db.execute(
            "SELECT id FROM chain_runs WHERE state='ACTIVE' ORDER BY rowid")]
    for run_id in ids:
        try:
            _tick_one(run_id)
        except Exception as exc:
            run = get(run_id)
            run['last_worker_error'] = control.redact(str(exc))[:1000]
            try:
                save(run)
            except ValueError:
                pass


def _tick_one(run_id):
    with _dispatch_lock(run_id) as owned:
        if owned:
            return _dispatch_pass(run_id)


def _dispatch_pass(run_id):
    try:
        _advance(run_id)
    except Exception as exc:
        current = get(run_id)
        if current['state'] == 'ACTIVE':
            message = control.redact(str(exc))[:1000]
            current['last_worker_error'] = message
            try:
                save(current)
            except ValueError:
                pass
    finally:
        if get(run_id).get('initial_dispatch', {}).get('state') == 'IN_PROGRESS':
            _update_dispatch(run_id, state='FINISHED', finished_at=ex.now())


def _advance(run_id):
    run = get(run_id)
    if recycle.inactive('chains',run['id'],run) or run.get('local_cleanup_blocker'):return
    if run['state'] != 'ACTIVE':
        return
    by_id = {step['node_id']: step for step in run['steps']}
    authorization = run['authorization']
    probes = {}
    capacity_context = {}
    for step in run['steps']:
        if get(run_id)['state'] != 'ACTIVE':
            return
        if step['state']=='BLOCKED':
            _recover_blocked_step(step)
            run=save(run)
        if step['state'] in ('DONE', 'BLOCKED'):
            continue
        attempt_id = step.get('attempt_id')
        if attempt_id:
            attempt = ex.get(attempt_id)
            evidence = attempt.get('evidence') or {}
            step['attempt_evidence'] = {
                'scheduler_state': evidence.get('scheduler_state'),
                'program_done': bool(evidence.get('vasp_timing')),
                'electronic_converged': evidence.get('ediff_reached'),
                'scientific_validation': evidence.get('scientific_validation', 'NOT_PERFORMED')}
            if attempt['state'] == 'COMPLETED':
                if step['stage'] in ('relax', 'scf'):
                    verdict = convergence(attempt, step['stage'])
                    step['convergence'] = verdict
                    if verdict['status'] == 'CONVERGED':
                        step['state'] = 'DONE'
                        step['final_attempt_id'] = attempt_id
                    elif verdict['status'] == 'EXHAUSTED' and len(step['attempts']) == 1:
                        step['state'] = 'RETRY_PENDING'
                        step['attempt_id'] = None
                    else:
                        step['state'] = 'BLOCKED'
                        step['blocker'] = verdict['reason']
                else:
                    step['state'] = 'DONE'
                    step['final_attempt_id'] = attempt_id
            elif attempt['state'] in ('FAILED', 'UNKNOWN', 'CANCELLED'):
                step['state'] = 'BLOCKED'
                step['blocker'] = '任务已由用户取消；请重新准备并明确授权，未自动重试' if attempt['state'] == 'CANCELLED' else _attempt_blocker(attempt)
                if attempt['state']=='UNKNOWN':step['blocker_kind']='SUBMISSION_UNKNOWN'
                step.pop('waiting_reason',None)
            elif attempt['state'] == 'PREPARED':
                step['state'] = 'PREPARED'
            else:
                step['state'] = attempt['state']
        if step['state'] in ('DONE', 'BLOCKED'):
            run = save(run)
            continue
        if step['state'] not in ('PENDING', 'RETRY_PENDING', 'PREPARED'):
            run = save(run)
            continue
        parent = by_id.get(step['parent_id'])
        if parent and parent['state'] == 'BLOCKED':
            step['state'] = 'BLOCKED'
            step['blocker'] = '上游分支已停止'
            run = save(run)
            continue
        if parent and parent['state'] != 'DONE' and step['state'] != 'RETRY_PENDING':
            step['waiting_kind'] = 'DEPENDENCY'
            step['waiting_reason'] = '等待上游 '+parent['label']+' 完成并通过审核'
            run = save(run)
            continue
        if not _quota_available(authorization['config']['alias']):
            step['waiting_kind'] = 'QUOTA'
            step['waiting_reason'] = '服务器在途任务数或核数预算已达上限，等待额度释放'
            run = save(run)
            continue
        step.pop('waiting_reason', None)
        step.pop('waiting_kind', None)
        from .workbench_settings import assert_current
        try:
            assert_current(authorization.get('settings_snapshots',{}).get(step['node_id']))
        except ValueError as exc:
            step['state']='BLOCKED';step['blocker']=str(exc);run=save(run);continue
        from .project_sync import locally_executable
        if not locally_executable(run['project_id']):
            return
        resource = authorization['resources'][step['node_id']]
        node_config = _node_config(authorization['config'], resource)
        if step['state'] != 'PREPARED':
            retry = step['state'] == 'RETRY_PENDING'
            binding = run.get('reuse_bindings', {}).get(step['parent_id'])
            source_id = step['attempts'][-1] if retry else parent['final_attempt_id'] if parent else binding['attempt_id'] if binding else None
            source_kind = step['stage'] + '-restart' if retry else 'dependency' if source_id else None
            try:
                if binding and not retry:
                    from .source_files import snapshot
                    actual = ex.get(binding['attempt_id'])
                    if (actual['state'] != 'COMPLETED' or actual.get('bundle_id') != binding['bundle_id'] or
                            actual.get('config', {}).get('alias') != binding['alias'] or
                            ex.attempt_path(actual)['shell'] != binding['source_path']):
                        raise ValueError('冻结的历史来源状态或身份已变化')
                    files = snapshot(binding['alias'], binding['source_path'], sorted(binding['files']), ex.ssh, binding['verification_mode'])
                    if files != binding['files']:
                        raise ValueError('冻结的历史来源文件已变化，请重新预览和冻结')
                if step['stage'] == 'bands' and source_id and not retry and not step.get('actual_band_path'):
                    _band_bundle(run, step, source_id)
                    run = save(run)
                probe_key = digest(node_config)
                if probe_key not in probes:
                    probes[probe_key] = servers.capacity_probe(node_config, context=capacity_context)
                probe = probes[probe_key]
                candidates = resource.get('candidates') or [resource]
                from .candidate_pool import select
                choice, waiting_reason = select(node_config, probe, candidates)
                if choice is None:
                    step['waiting_reason'] = waiting_reason
                    run = save(run)
                    continue
                step.pop('waiting_reason', None)
                deterministic_id = hashlib.sha256(
                    (run_id + ':' + step['node_id'] + ':' + str(len(step['attempts']))).encode()).hexdigest()[:32]
                with closing(ex.db()) as db:
                    row = db.execute('SELECT body FROM attempts WHERE id=?', (deterministic_id,)).fetchone()
                if row:
                    prepared = json.loads(row[0])
                    if (prepared['state'] != 'PREPARED' or
                            prepared.get('bundle_id') != step['bundle_id'] or
                            prepared.get('project_id') != run['project_id'] or
                            prepared.get('config', {}).get('alias') != authorization['config']['alias'] or
                            prepared.get('config', {}).get('vasp_executable') != node_config['vasp_executable'] or
                            not any(prepared.get('node') == item['node'] and
                                    prepared.get('cores') == item['cores'] and
                                    prepared.get('reason') == item['reason']
                                    for item in candidates) or
                            prepared.get('chain_run_id', run_id) != run_id or
                            prepared.get('chain_step_id', step['node_id']) != step['node_id'] or
                            (prepared.get('chain_source_snapshot') or {}).get('kind') != source_kind or
                            (prepared.get('chain_source_snapshot') or {}).get('source_attempt_id') != source_id):
                        raise ValueError('确定性 attempt ID 已存在但准备记录与冻结计划不一致；须人工核对')
                else:
                    prepared = ex.prepare(step['bundle_id'], node_config, probe['id'],
                                          choice['node'], choice['cores'], choice['reason'],
                                          project_id=run['project_id'], attempt_id=deterministic_id,
                                          chain_source_id=source_id, chain_source_kind=source_kind)
                if prepared['state'] == 'PREPARED':
                    prepared.update(chain_run_id=run_id, chain_step_id=step['node_id'],
                                    chain_plan_hash=run['plan_hash'])
                    ex.save(prepared)
                step['source_snapshot'] = prepared.get('chain_source_snapshot')
                step['attempt_id'] = prepared['id']
                if prepared['id'] not in step['attempts']:
                    step['attempts'].append(prepared['id'])
                step['state'] = prepared['state']
                run = save(run)
            except Exception as exc:
                message = control.redact(str(exc))[:1000]
                if message.startswith('资源检查未通过：') and _capacity_wait({
                    'blockers': message.split('：', 1)[1].split('; '),
                    'node_status': 'BUSY'}):
                    step['waiting_reason'] = message
                else:
                    step['state'] = 'BLOCKED'
                    step['blocker'] = message
                run = save(run)
                continue
        if get(run_id)['state'] != 'ACTIVE':
            continue
        try:
            submitted = ex.submit(step['attempt_id'])
            step['state'] = submitted['state']
            if submitted['state'] == 'PREPARED':
                step['waiting_reason'] = submitted.get('note') or '派发前检查未通过，等待资源恢复；没有提交任务'
                step['waiting_kind'] = submitted.get('waiting_kind','CAPACITY')
            else:
                step.pop('waiting_reason', None)
                step.pop('waiting_kind',None)
                if submitted['state'] in ('FAILED', 'UNKNOWN'):
                    step['state'] = 'BLOCKED'
                    step['blocker'] = _attempt_blocker(submitted)
                    if submitted['state']=='UNKNOWN':step['blocker_kind']='SUBMISSION_UNKNOWN'
        except ValueError as exc:
            if any(item in str(exc) for item in ('在途任务数已达上限', '核数预算不足',
                                                 '实时资源已变化或不可用')):
                step['state'] = 'PREPARED'
                step['waiting_reason'] = control.redact(str(exc))[:1000]
            else:
                step['state'] = 'BLOCKED'
                step['blocker'] = control.redact(str(exc))[:1000]
        run = save(run)
    states = {step['state'] for step in run['steps']}
    if states == {'DONE'}:
        run['state'] = 'COMPLETE'
        save(run)
    elif states <= {'DONE', 'BLOCKED'}:
        run['state'] = 'BLOCKED'
        save(run)

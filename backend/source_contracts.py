"""One role-based VASP source policy for drafts, frozen inputs and chains."""
import base64
import copy
import hashlib
import json
import math
import re

from . import source_files

MODES = ('new', 'self', 'upstream', 'manual')
FIXED = ('bands', 'dos', 'vasp-wannier')
MODEL_TAGS = ('GGA', 'METAGGA', 'LHFCALC', 'AEXX', 'HFSCREEN', 'ISPIN', 'LDAU',
              'LDAUTYPE', 'LDAUL', 'LDAUU', 'LDAUJ', 'LMAXMIX', 'ENCUT', 'PREC',
              'LSORBIT', 'LNONCOLLINEAR', 'SAXIS', 'GGA_COMPAT', 'MAGMOM')


def tag(raw, name):
    text = raw.decode() if isinstance(raw, bytes) else raw
    matches = re.findall(r'^\s*'+re.escape(name)+r'\s*=\s*([^!#\r\n]+)', text, re.I | re.M)
    return ' '.join(matches[-1].upper().split()) if matches else None


def enabled(value):
    return str(value).upper() in ('TRUE', '.TRUE.', 'T')


def validate_pead(incar, ranks=None, gpu=False):
    if not enabled(tag(incar, 'LCALCPOL')):
        return
    ncore, npar, kpar = tag(incar, 'NCORE'), tag(incar, 'NPAR'), tag(incar, 'KPAR') or '1'
    try:
        if ncore and int(ncore) != 1 and not gpu:
            raise ValueError('LCALCPOL 的 PEAD 例程要求有效 NCORE=1；请在物理参数中修改 NCORE 并重新冻结，原输入保留')
        if npar and ncore:
            raise ValueError('LCALCPOL 不能同时指定 NPAR 和 NCORE；请在物理参数中修改并重新冻结')
        if npar and ranks is not None:
            if not isinstance(ranks, int) or ranks < 1 or ranks % int(kpar) or int(npar) != ranks // int(kpar):
                raise ValueError('LCALCPOL 的 NPAR 必须等于本次 MPI/KPAR 组的核数以得到 NCORE=1；请修改 NPAR 或提交资源并重新预览')
    except (TypeError, ZeroDivisionError):
        raise ValueError('LCALCPOL 并行配置不可解析；请核对 NCORE、NPAR、KPAR 和 MPI 核数')


def wave_start(stage, values, continuing=False):
    value = values.get('ISIF', '2') if isinstance(values, dict) else tag(values, 'ISIF') or '2'
    return 2 if continuing and stage == 'relax' and str(value) in ('3', '4', '5', '6', '7', '8') else 1


def draft(project, node_id):
    nodes = {n['id']: n for n in project['nodes']}
    parent = next((e['source'] for e in project['edges'] if e['target'] == node_id), None)
    parent_stage = nodes[parent]['data']['kind'] if parent else None
    value = {'mode': 'upstream' if parent_stage and parent_stage != 'structure' else 'new',
             'purpose': 'charge', 'files': 'charge', **(nodes[node_id]['data'].get('sourceDraft') or {})}
    if value['mode'] not in MODES or value['purpose'] not in ('charge', 'continue') or value['files'] not in ('charge', 'both'):
        raise ValueError('计算来源配置无效')
    if 'read_charge' in value and type(value['read_charge']) is not bool:
        raise ValueError('电子初值选择无效')
    value.setdefault('read_charge', parent_stage == 'scf')
    return value, parent


def plan(project, node_id, values=None):
    nodes = {n['id']: n for n in project['nodes']}
    stage = nodes[node_id]['data']['kind']
    value, parent = draft(project, node_id)
    parent_stage = nodes[parent]['data']['kind'] if parent else None
    mode = value['mode']
    upstream = mode in ('new', 'upstream') and parent_stage not in (None, 'structure')
    if mode == 'upstream' and parent_stage in (None, 'structure'):
        raise ValueError('上游依赖需要连接 relax 或 SCF；结构节点使用新计算')
    if stage in FIXED and (mode != 'upstream' or parent_stage != 'scf'):
        raise ValueError('本阶段必须读取已连接 SCF 的固定电荷与实际结构')
    if upstream and parent_stage not in ('relax', 'scf'):
        raise ValueError('VASP 来源须为结构、relax 或 SCF')
    if value['purpose'] == 'continue' and stage != 'relax':
        raise ValueError('继续未完成优化仅适用于 relax')
    structure = ('CONTCAR' if parent_stage == 'relax' else 'POSCAR') if upstream else (
        'CONTCAR' if value['purpose'] == 'continue' else 'POSCAR' if mode == 'self' else None)
    charge = stage in FIXED or mode in ('self', 'manual') or (
        mode == 'upstream' and (value['read_charge'] or value['files'] == 'both'))
    wave = mode != 'new' and value['files'] == 'both'
    required = ([structure] if structure else []) + (['CHGCAR'] if charge else []) + (['WAVECAR'] if wave else [])
    initial = {'ISTART': wave_start(stage, values or {}, value['purpose'] == 'continue') if wave else 0,
               'ICHARG': 11 if stage in FIXED else 1 if charge else 2}
    return {'schema': 'fpilot.source-plan.v2', 'mode': mode, 'purpose': value['purpose'],
            'files': value['files'], 'stage': stage, 'parent_id': parent if upstream else None,
            'parent_stage': parent_stage if upstream else None, 'structure_file': structure,
            'required_files': required, 'electronic_usage': 'fixed' if stage in FIXED else 'initial' if charge else 'none',
            'initialization': initial, 'draft': value}


def structure_file(mode, purpose, stage='relax'):
    # Kept for older callers; new callers use the complete source plan.
    return 'CONTCAR' if purpose == 'continue' or mode == 'upstream' and stage == 'relax' else 'POSCAR' if mode in ('self', 'upstream') else None


def input_poscar_hash(record, manifest):
    actual = record.get('effective_input', {}).get('files', {}).get('POSCAR', {}).get('sha256')
    if actual:
        return actual
    for name, info in record.get('source_file_plan', {}).items():
        if info.get('target', 'POSCAR' if name == 'CONTCAR' else name) == 'POSCAR':
            return info.get('sha256')
    return manifest.get('files', {}).get('POSCAR', {}).get('sha256')


def actual_inputs(record):
    from . import execution as ex
    manifest, files = ex.bundle(record['bundle_id'], allow_dependencies=True)
    if record.get('final_incar'):
        files['INCAR'] = record['final_incar'].encode()
    return manifest, files


def electronic(evidence):
    numbers = [evidence.get(k) for k in ('last_delta_e', 'last_delta_eps', 'ediff_value')]
    if any(x is not None for x in numbers):
        return all(type(x) in (float, int) and math.isfinite(x) for x in numbers) and numbers[2] > 0 and all(abs(x) <= numbers[2] for x in numbers[:2])
    # Old receipts without the numerical fields remain readable. A generic OUTCAR
    # marker is explicitly insufficient: it may belong to an earlier ionic step.
    return evidence.get('ediff_reached') is True and evidence.get('ediff_source') != 'OUTCAR_MARKER'


def convergence(record, stage, incar=None):
    evidence = record.get('evidence') or {}
    if record.get('state') != 'COMPLETED' or not evidence.get('vasp_timing') or evidence.get('wrapper_exit') not in (None, 0) or not evidence.get('output_sanity', True):
        return {'status': 'UNVERIFIED', 'reason': '程序未确认正常结束'}
    if incar is None:
        incar = actual_inputs(record)[1]['INCAR']
    try:
        nelm, nsw = int(tag(incar, 'NELM') or 60), int(tag(incar, 'NSW') or 0)
    except ValueError:
        return {'status': 'UNVERIFIED', 'reason': '步数上限不可解析'}
    elec = electronic(evidence)
    ionic = evidence.get('ionic_converged', evidence.get('ionic_accuracy')) if stage == 'relax' else None
    step, ion_step = evidence.get('last_electronic_step'), evidence.get('last_ionic_step')
    if elec and (stage != 'relax' or ionic is True):
        return {'status': 'CONVERGED', 'electronic': True, 'ionic': ionic, 'electronic_step': step, 'ionic_step': ion_step}
    exhausted = isinstance(step, int) and step >= nelm or stage == 'relax' and isinstance(ion_step, int) and ion_step >= nsw > 0
    return {'status': 'EXHAUSTED' if exhausted else 'UNVERIFIED',
            'reason': '达到 NELM/NSW，仍未收敛' if exhausted else '缺少最终电子或离子收敛证据',
            'electronic': elec, 'ionic': ionic, 'electronic_step': step, 'ionic_step': ion_step}


def eligible(record, mode, purpose, source_plan=None):
    reasons = []
    from . import execution as ex, recycle
    if recycle.inactive('attempts', record.get('id', ''), record) or record.get('path_schema') != 'fpilot.remote-attempt-path' or not record.get('job_id'):
        reasons.append('来源已回收或缺少实际运行记录')
    try:
        manifest, files = actual_inputs(record)
        if manifest.get('node_id') != record.get('node_id') or ex.frozen_project(record['bundle_id']).get('id') != record.get('project_id'):
            reasons.append('来源记录与实际冻结输入的项目或节点不同')
    except (ValueError, KeyError, OSError):
        return reasons + ['来源实际冻结输入不可读取']
    if mode == 'upstream':
        stage = manifest.get('stage')
        evidence = record.get('evidence') or {}
        if record.get('state') != 'COMPLETED' or not evidence.get('vasp_timing') or evidence.get('wrapper_exit') not in (None, 0) or not evidence.get('output_sanity', True):
            reasons.append('程序尚未正常完成')
        needs_electronic = stage == 'relax' or not source_plan or source_plan['electronic_usage'] != 'none'
        if needs_electronic and not electronic(evidence):
            reasons.append('缺少最终电子收敛证据')
        if stage == 'relax' and evidence.get('ionic_converged', evidence.get('ionic_accuracy')) is not True:
            reasons.append('缺少离子收敛证据')
        if record.get('scan_batch_id'):
            from . import scans
            if (scans.get(record['scan_batch_id']).get('promotion') or {}).get('attempt_id') != record['id']:
                reasons.append('扫描结果尚未提升')
    elif record.get('state') not in ('COMPLETED', 'FAILED'):
        reasons.append('请先确认本轮已终止，UNKNOWN 不能用作续算来源')
    return reasons


def compatibility(source_plan, record, report):
    if source_plan['electronic_usage'] == 'none' and 'WAVECAR' not in source_plan['required_files']:
        return {'blockers': [], 'differences': [], 'warnings': []}
    _, old = actual_inputs(record)
    return review_inputs(source_plan, old, report)


def review_inputs(source_plan, old, report):
    if source_plan['electronic_usage'] == 'none' and 'WAVECAR' not in source_plan['required_files']:
        return {'blockers': [], 'differences': [], 'warnings': []}
    new = report['files']['INCAR'].encode()
    blockers, differences, warnings = [], [], []
    fixed = source_plan['electronic_usage'] == 'fixed'
    if hashlib.sha256(old['POTCAR']).hexdigest() != report['potcar']['sha256']:
        (blockers if fixed else differences).append('POTCAR 与来源不同')
    for name in MODEL_TAGS:
        a, b = tag(old['INCAR'], name), tag(new, name)
        if a == b:
            continue
        message = f'{name}：来源 {a or "未设置"} → 本次 {b or "未设置"}'
        (blockers if fixed and name in ('GGA', 'METAGGA', 'LHFCALC', 'AEXX', 'HFSCREEN', 'LDAU', 'LDAUTYPE', 'LDAUL', 'LDAUU', 'LDAUJ') else differences).append(message)
    if 'WAVECAR' in source_plan['required_files']:
        if old.get('KPOINTS') != report['files']['KPOINTS'].encode():
            blockers.append('WAVECAR 的 k 点与本次不同；请选择仅 CHGCAR 或新计算')
        a, b = tag(old['INCAR'], 'NBANDS'), tag(new, 'NBANDS')
        if a is not None and (b is None or int(a) != int(b)):
            blockers.append('WAVECAR 的 NBANDS 设置不同；请选择仅 CHGCAR 或新计算')
        old_ncl = any(enabled(tag(old['INCAR'], k)) for k in ('LSORBIT', 'LNONCOLLINEAR'))
        new_ncl = any(enabled(tag(new, k)) for k in ('LSORBIT', 'LNONCOLLINEAR'))
        if source_plan['initialization']['ISTART'] == 2 and any(tag(old['INCAR'], k) != tag(new, k) for k in ('ISIF', 'ENCUT', 'PREC')):
            blockers.append('ISTART=2 仅用于同一次变胞优化的连续续算；ISIF / ENCUT / PREC 改变时请重新选择续算目的')
        if old_ncl and not new_ncl:
            blockers.append('不能将非共线 WAVECAR 直接作为共线波函数续算')
    if any(tag(old['INCAR'], k) != tag(new, k) for k in ('ISPIN', 'LSORBIT', 'LNONCOLLINEAR', 'SAXIS', 'MAGMOM')):
        warnings.append('读取已有磁化密度时 MAGMOM 不会重新设置各原子的磁矩；共线磁化沿 SAXIS 解释，改变 SAXIS 可整体旋转磁化')
    return {'blockers': blockers, 'differences': differences, 'warnings': warnings}


def resolve(project, node_id, execution_scope=None):
    from . import execution as ex, branch_bindings, inputs, artifacts
    report = inputs.generate(project, node_id)
    policy = report.get('source_plan') or plan(project, node_id)
    value, parent = draft(project, node_id)
    binding = branch_bindings.inspect(project['id'], branch_bindings.scopes(project, execution_scope or node_id))
    candidates = []
    upstream = policy['parent_id'] is not None
    for rec in ex.history(None, limit=10000):
        if not policy['required_files']:
            break
        if upstream and (rec.get('project_id') != project['id'] or rec.get('node_id') != parent):
            continue
        if value['mode'] == 'self' and (rec.get('project_id') != project['id'] or rec.get('node_id') != node_id or rec.get('scan_batch_id')):
            continue
        reasons = eligible(rec, 'upstream' if upstream else value['mode'], value['purpose'], policy)
        if binding['alias'] and rec.get('config', {}).get('alias') != binding['alias']:
            reasons.append('来源服务器与本分支不同')
        checked = artifacts.cached_listing(rec)
        verification = 'VERIFIED' if checked else 'UNVERIFIED'
        if checked:
            known = {f['path'] for f in checked['files'] if f.get('size_bytes', 0) > 0}
            missing = set(policy['required_files']) - known
            if missing:
                reasons.append('缺少 ' + '、'.join(sorted(missing)))
                verification = 'MISSING'
        try:
            review = compatibility(policy, rec, report) if not report['errors'] else {'blockers': [], 'differences': [], 'warnings': []}
        except (ValueError, KeyError, OSError):
            review = {'blockers': ['来源输入不可核对'], 'differences': [], 'warnings': []}
        reasons += review['blockers']
        candidates.append({'id': rec['id'], 'alias': rec.get('config', {}).get('alias'), 'node_id': rec.get('node_id'),
                           'state': rec['state'], 'job_id': rec.get('job_id'), 'created_at': rec.get('created_at', ''),
                           'eligible': not reasons, 'blockers': reasons, 'differences': review['differences'],
                           'warnings': review['warnings'], 'structure_file': policy['structure_file'],
                           'required_files': policy['required_files'], 'verification_status': verification,
                           'verified_at': checked.get('refreshed_at') if checked else None,
                           'observed_files': [f for f in checked['files'] if f['path'] in policy['required_files']] if checked else []})
    candidates.sort(key=lambda c: (c['created_at'], c['id']), reverse=True)
    selected = value.get('attempt_id') or next((c['id'] for c in candidates if c['eligible']), None)
    chosen = next((c for c in candidates if c['id'] == selected), None)
    blockers = chosen['blockers'] if chosen else candidates[0]['blockers'] if candidates else ['没有可用来源，请完成相连上游或选择已终止的同节点运行'] if policy['required_files'] else []
    return {'draft': value, 'source_plan': policy, 'candidates': candidates, 'selected_attempt_id': selected,
            'blockers': blockers, 'binding': binding, 'initialization': policy['initialization'], 'required_files': policy['required_files']}


def structure_geometry(raw):
    from .structures import parse_structure
    lines = raw.decode().splitlines()
    count = sum(map(int, lines[6].split()))
    start = 9 if lines[7].strip().lower().startswith('s') else 8
    if len(lines) < start + count:
        raise ValueError('来源结构文件头不完整')
    header = ('\n'.join(lines[:start+count])+'\n').encode()
    return parse_structure('POSCAR', 'poscar', base64.b64encode(header).decode())


def compare_geometry(a, b):
    import numpy as np
    if [x['element'] for x in a['atoms']] != [x['element'] for x in b['atoms']]:
        raise ValueError('来源结构的元素顺序或原子数量不同，不能复用电子文件')
    lattice = float(np.max(np.abs(np.array(a['lattice'])-b['lattice'])))
    delta = np.array([x['fractional'] for x in a['atoms']])-np.array([x['fractional'] for x in b['atoms']])
    delta -= np.rint(delta)
    return lattice <= 1e-6 and float(np.max(np.abs(delta))) <= 1e-6


def bind(policy, record, report, verification_mode):
    from . import execution as ex, artifacts
    mode = 'upstream' if policy['parent_id'] else policy['mode']
    reasons = eligible(record, mode, policy['purpose'], policy)
    if policy['parent_id'] and record.get('node_id') != policy['parent_id']:
        raise ValueError('来源 attempt 与直接上游不符')
    if reasons:
        raise ValueError('来源不可用：'+'；'.join(reasons))
    old_manifest, old_files = actual_inputs(record)
    if policy['parent_id'] and (record.get('node_id') != policy['parent_id'] or old_manifest.get('stage') != policy['parent_stage']):
        raise ValueError('来源 attempt 与直接上游不符')
    review = compatibility(policy, record, report)
    if review['blockers']:
        raise ValueError('来源不兼容：'+'；'.join(review['blockers']))
    if review['differences'] and not policy.get('acknowledge_differences', policy.get('draft', {}).get('acknowledge_differences')):
        raise ValueError('来源输入存在差异，请审阅并确认：'+'；'.join(review['differences']))
    path, alias = ex.attempt_path(record)['shell'], record['config']['alias']
    observed_files = source_files.snapshot(alias, path, policy['required_files'], ex.ssh, verification_mode)
    structure_name = policy['structure_file']
    if structure_name:
        observed = artifacts.preview(record['id'], structure_name, 'head', 262144, True)
        if observed.get('partial') or not observed.get('structure'):
            raise ValueError('来源 '+structure_name+' 不完整或不可解析')
        geometry = observed['structure']
        if geometry['source']['sha256'] != observed_files[structure_name]['sha256']:
            raise ValueError(structure_name+' 在审查期间发生变化')
        if structure_name == 'POSCAR' and input_poscar_hash(record, old_manifest) != observed_files['POSCAR']['sha256']:
            raise ValueError('来源实际 POSCAR 与该次运行保存的输入身份不符，请核对原任务；不会退回项目原始结构')
        configured = report['files'].get('POSCAR', report['files'].get('POSCAR.reference', ''))
        compare_geometry(geometry, structure_geometry(configured.encode()))  # checks ordered species
        raw = base64.b64decode(geometry['source']['base64'], validate=True)
        if hashlib.sha256(raw).hexdigest() != observed_files[structure_name]['sha256']:
            raise ValueError('来源结构原文与解析身份不符')
        from .structure_edit import geometry_hash
        report['files'].pop('POSCAR.reference', None)
        report['files']['POSCAR'] = raw.decode()
        report['geometry_sha256'], report['source_sha256'] = geometry_hash(geometry), geometry['source']['sha256']
        report['structure_source'] = {'attempt_id': record['id'], 'job_id': record.get('job_id'), 'file': structure_name,
                                    'sha256': geometry['source']['sha256'], 'volume_A3': geometry['volume']}
    else:
        geometry = structure_geometry(report['files']['POSCAR'].encode())
    if 'CHGCAR' in observed_files:
        header = source_files.structure_header(alias, path, 'CHGCAR', ex.ssh)
        charge_geometry = structure_geometry(header)
        same = compare_geometry(geometry, charge_geometry)
        if not same:
            assert_no_permutation(geometry, charge_geometry)
        if not same and policy['electronic_usage'] == 'fixed':
            raise ValueError('固定电荷 CHGCAR 与本次实际几何不同')
        if not same:
            review['warnings'].append('CHGCAR 与目标几何不同，将作为自洽初值外推')
    if report['stage'] == 'bands' and (report.get('band_path_source_attempt_id') != record['id'] or report.get('band_path_source_sha256') != observed_files[structure_name]['sha256']):
        raise ValueError('能带路径必须依据所选 SCF 实际结构生成')
    wave = None
    if 'WAVECAR' in observed_files:
        wave = source_files.wave_header(alias, path, ex.ssh)
        requested_bands = tag(report['files']['INCAR'], 'NBANDS')
        if requested_bands is not None and int(requested_bands) != wave['nbands']:
            raise ValueError('WAVECAR 实际 NBANDS 与本次不同；请选择仅 CHGCAR 或新计算')
        if int(tag(old_files['INCAR'], 'ISPIN') or 1) != wave['spin_channels'] and not any(enabled(tag(old_files['INCAR'], k)) for k in ('LSORBIT', 'LNONCOLLINEAR')):
            raise ValueError('WAVECAR 自旋通道与来源实际输入不同')
        review['warnings'].append('WAVECAR 实际 k 点 '+str(wave['nkpoints'])+'、NBANDS '+str(wave['nbands'])+'；准备阶段继续核对 MPI 带并行，参数差异确认不能绕过不兼容')
    roles = {}
    for name, info in observed_files.items():
        role = {'POSCAR': 'structure', 'CONTCAR': 'structure', 'CHGCAR': 'charge', 'WAVECAR': 'wavefunction'}[name]
        roles[role] = {'attempt_id': record['id'], 'alias': alias, 'node_id': record['node_id'], 'file': name,
                       'source_path': path, 'metadata': info, 'source_bundle_id': record['bundle_id'],
                       'differences': review['differences']}
    return {'schema': 'fpilot.source-contract.v2', 'mode': policy['mode'], 'purpose': policy['purpose'],
            'files': policy['files'], 'structure': 'source' if structure_name else 'configured',
            'verification_mode': verification_mode, 'roles': roles, 'source_plan': policy,
            'initialization': policy['initialization'], 'warnings': review['warnings'], 'wavefunction_header': wave}


def freeze(project, node_id, report, verification_mode, execution_scope=None):
    from . import execution as ex, branch_bindings
    policy = report['source_plan']
    if not policy['required_files']:
        contract = {'schema': 'fpilot.source-contract.v2', 'mode': policy['mode'], 'purpose': policy['purpose'],
                    'files': policy['files'], 'structure': 'configured', 'verification_mode': verification_mode,
                    'roles': {}, 'source_plan': policy, 'initialization': policy['initialization']}
    else:
        selection = resolve(project, node_id, execution_scope)
        if selection['blockers']:
            raise ValueError('来源不可用：'+'；'.join(selection['blockers']))
        record = ex.get(selection['selected_attempt_id'])
        if policy['mode'] == 'self' and (record.get('project_id') != project['id'] or record.get('node_id') != node_id):
            raise ValueError('同节点续算只能读取当前节点的历史运行')
        contract = bind(policy, record, report, verification_mode)
    branch_bindings.check(project, node_id, source_contract=contract, scope=execution_scope)
    return contract


def materialize(contract, alias):
    from . import execution as ex, recycle
    files = {}
    for role in contract['roles'].values():
        record = ex.get(role['attempt_id'])
        if recycle.inactive('attempts', record['id'], record) or role['alias'] != alias or record.get('config', {}).get('alias') != alias:
            raise ValueError('冻结来源已回收或服务器不同，请返回计算配置重新审查')
        names = [r['file'] for r in contract['roles'].values()]
        policy = contract.get('source_plan') or {'electronic_usage': 'initial' if 'CHGCAR' in names else 'none'}
        reasons = eligible(record, 'upstream' if contract['mode'] in ('new', 'upstream') else contract['mode'], contract['purpose'], policy)
        if reasons:
            raise ValueError('冻结来源不可用：'+'；'.join(reasons))
        fresh = source_files.snapshot(alias, role['source_path'], [role['file']], ex.ssh, contract['verification_mode']).get(role['file'])
        if fresh != role['metadata']:
            raise ValueError('冻结来源文件已变化，请重新审查')
        files[role['file']] = {**fresh, 'source_path': role['source_path']}
    if contract.get('wavefunction_header'):
        role = contract['roles']['wavefunction']
        if source_files.wave_header(alias, role['source_path'], ex.ssh) != contract['wavefunction_header']:
            raise ValueError('冻结 WAVECAR 实际头部已变化，请重新审查')
    if not files:
        return None
    first = next(iter(contract['roles'].values()))
    return {'schema': 'fpilot.role-source.v1', 'files': files, 'source_path': first['source_path'],
            'source_attempt_id': first['attempt_id'], 'mode': contract['mode']}


def draft_differences(project, node_id, bundle_id):
    from . import execution as ex, inputs
    manifest, files = ex.bundle(bundle_id, allow_dependencies=True)
    if manifest['node_id'] != node_id or ex.frozen_project(bundle_id)['id'] != project['id']:
        raise ValueError('冻结输入不属于当前项目节点')
    report = inputs.generate(project, node_id)
    current = report.get('incar_tags', {})
    # Even invalid drafts must explain changes such as a PEAD conflict.
    if not current:
        node = next(n for n in project['nodes'] if n['id'] == node_id)
        current = {k: {'value': str(v)} for k, v in {**project.get('parameters', {}).get('shared', {}).get('values', {}),
                                                   **node['data'].get('parameters', {}).get('values', {})}.items() if v is not None}
    differences = []
    for name in sorted(set(current) | set(manifest.get('incar_tags', {}))):
        before = tag(files['INCAR'], name)
        generated = report.get('files', {}).get('INCAR')
        after = tag(generated, name) if generated else str(current[name]['value']).upper() if name in current else None
        booleans = {'TRUE': '.TRUE.', 'T': '.TRUE.', 'FALSE': '.FALSE.', 'F': '.FALSE.'}
        if booleans.get(before, before) != booleans.get(after, after):
            differences.append({'field': name, 'frozen': before, 'draft': after})
    frozen_policy = manifest.get('source_plan') or manifest.get('source_contract') or manifest.get('source_draft')
    def signature(policy):
        return {k: policy.get(k) for k in ('mode', 'purpose', 'parent_id', 'structure_file', 'required_files', 'electronic_usage', 'initialization')} if policy else {}
    if signature(frozen_policy) != signature(report.get('source_plan')):
        differences.append({'field': '来源与初始化', 'frozen': frozen_policy, 'draft': report.get('source_plan')})
    for name in ('KPOINTS', 'POTCAR'):
        before = manifest['files'].get(name, {}).get('sha256')
        after = report.get('potcar', {}).get('sha256') if name == 'POTCAR' else hashlib.sha256(report.get('files', {}).get(name, '').encode()).hexdigest()
        if before != after:
            differences.append({'field': name, 'frozen': '冻结文件', 'draft': '当前文件已改变'})
    return {'bundle_id': bundle_id, 'differences': differences, 'errors': report['errors']}


def validate_wave_parallel(contract, incar, ranks, gpu=False):
    header = (contract or {}).get('wavefunction_header')
    if not header:
        return
    kpar = int(tag(incar, 'KPAR') or 1)
    ncore = 1 if gpu else int(tag(incar, 'NCORE') or 1)
    if not isinstance(ranks, int) or ranks < 1 or ranks % (kpar*ncore):
        raise ValueError('WAVECAR 续算的实际 MPI 带并行不可核对')
    band_groups = int(tag(incar, 'NPAR') or ranks // kpar // ncore)
    if band_groups < 1 or header['nbands'] % band_groups:
        raise ValueError('本次 MPI 带并行会改变 WAVECAR 的实际 NBANDS；请修改并行资源或选择仅 CHGCAR')


def assert_no_permutation(a, b):
    import numpy as np
    if np.max(np.abs(np.array(a['lattice'])-b['lattice'])) > 1e-6:
        return
    def positions(geometry):
        return sorted((atom['element'], *np.round(np.mod(atom['fractional'], 1), 6)) for atom in geometry['atoms'])
    if positions(a) == positions(b):
        raise ValueError('CHGCAR 与实际 POSCAR 的原子顺序不同，不能通过确认差异绕过')

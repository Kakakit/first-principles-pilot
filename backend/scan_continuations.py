"""Create a frozen SCF batch from explicitly selected relaxation attempts."""
import copy
import difflib
import hashlib
import json
import re
import uuid
from contextlib import ExitStack

from filelock import FileLock
from . import execution as ex, inputs, scans, source_contracts as sources, recycle, parameter_catalog
from .calculation_nodes import owner
from .extension_protocol import atomic_json
from .incar_text import parse
from .storage import validate_workspace
from . import attempt_operations


def source_batch(batch_id, project_id):
    batch = scans.get(batch_id)
    if batch['project_id'] != project_id or batch.get('stage') != 'relax':
        raise ValueError('请选择当前项目的结构优化批次')
    recycle.assert_available('scans', batch_id, batch)
    return batch


def source_record(batch, case, key):
    if not key or key not in [case.get('attempt_id'), *(case.get('attempt_history') or [])]:
        raise ValueError('所选运行不属于此 case 的历史')
    record = ex.get(key)
    if (record.get('project_id') != batch['project_id'] or owner(record) != batch['scan_node_id'] or
            record.get('scan_batch_id') != batch['id'] or record.get('scan_case_id') != case['id']):
        raise ValueError('所选 case、批次与运行身份不一致')
    recycle.assert_available('attempts', key, record)
    reasons = sources.eligible(record, 'upstream', 'charge', {'electronic_usage': 'none'})
    if reasons:
        raise ValueError('；'.join(reasons))
    if sources.actual_inputs(record)[0].get('stage') != 'relax':
        raise ValueError('来源冻结输入须为结构优化')
    return record


def choices(batch_id, project_id, attempt_id=None):
    batch = source_batch(batch_id, project_id)
    if attempt_id and not any(attempt_id in [case.get('attempt_id'), *(case.get('attempt_history') or [])] for case in batch['cases']):
        raise ValueError('所选历史运行不属于来源批次')
    rows = []
    for case in sorted(batch['cases'], key=lambda item: item['index']):
        selected = attempt_id if attempt_id and attempt_id in [case.get('attempt_id'), *(case.get('attempt_history') or [])] else case.get('attempt_id')
        row = {'case_id': case['id'], 'index': case['index'], 'parameters': case.get('parameters') or [],
               'attempt_id': selected, 'state': case['state'], 'eligible': False, 'reason': ''}
        try:
            record = source_record(batch, case, selected)
            row.update(eligible=True, alias=record['config']['alias'], state=record['state'],
                       job_id=record.get('job_id'), evidence=record.get('evidence') or {})
        except (ValueError, OSError, KeyError) as exc:
            row['reason'] = str(exc)
        rows.append(row)
    children = [result(existing) for existing in scans.history(project_id)
                if (existing.get('continuation') or {}).get('source_batch_id') == batch_id]
    return {'cases': rows, 'continuations': children}


def _directory(key):
    if not isinstance(key, str) or not re.fullmatch(r'[a-f0-9]{32}', key):
        raise ValueError('后续批次预览 ID 无效')
    return ex.ROOT / 'scan-continuations' / key


def _case(request, batch, selection):
    case = next((item for item in batch['cases'] if item['id'] == selection['case_id']), None)
    if case is None:
        raise ValueError('所选 case 不属于来源批次')
    record = source_record(batch, case, selection['attempt_id'])
    project = copy.deepcopy(ex.frozen_project(record['bundle_id']))
    _, files = sources.actual_inputs(record)
    for field in ('remoteWorkspaceRoot', 'remotePathPolicy', 'remoteDirectories', 'remoteSlug', 'name', 'stagePolicy'):
        if field in request['project']:
            project[field] = copy.deepcopy(request['project'][field])
    if not any(node['id'] == batch['scan_node_id'] for node in project['nodes']):
        raise ValueError('来源冻结项目缺少对应迭代节点')
    node = copy.deepcopy(request['node'])
    options = request['options']
    old_tags = parse(files['INCAR'].decode())[0]
    omitted = parameter_catalog.MANAGED | parameter_catalog.CONTRACT_LOCKED | {'ISTART', 'ICHARG', 'NSW', 'IBRION', 'ISIF', 'EDIFFG', 'POTIM'}
    extra = '\n'.join(tag + ' = ' + value for tag, value in old_tags.items()
                      if tag not in parameter_catalog.DEFINITIONS and tag not in omitted)
    node['data']['parameters']['manualIncarText'] = extra + '\n' + node['data']['parameters']['manualIncarText']
    kpoints = files['KPOINTS'].decode().splitlines()
    if options['grid']:
        grid, center = options['grid'], options['center']
    elif len(kpoints) >= 5 and kpoints[1].strip() == '0' and kpoints[2].strip() in ('Gamma', 'Monkhorst-Pack'):
        grid, center = kpoints[3].strip(), kpoints[2].strip()
    else:
        raise ValueError('来源没有可继承的规则 k 网格，请明确填写后续网格')
    potcar_id = inputs.digest(files['POTCAR'])
    inputs.put_file(inputs.ROOT / 'potcars' / potcar_id, files['POTCAR'])
    node['data']['inputDraft'] = {'grid': grid, 'center': center, 'potcar_id': potcar_id}
    node['data']['sourceDraft'] = {'mode': 'upstream', 'purpose': 'charge', 'files': options['files'],
                                  'read_charge': options['files'] != 'structure',
                                  'attempt_id': record['id'], 'scan_batch_id': batch['id'], 'scan_case_id': case['id'],
                                  'acknowledge_differences': options['acknowledge_differences']}
    project['nodes'].append(node)
    project['edges'].append(copy.deepcopy(request['edge']))
    report = inputs.generate(project, node['id'])
    if report['errors']:
        raise ValueError('；'.join(report['errors']))
    contract = sources.bind(report['source_plan'], record, report, 'lightweight')
    from . import branch_bindings
    branch_bindings.check(project, node['id'], record['config']['alias'], contract, node['id'])
    hashes = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in report['files'].items()}
    mapping = {'batch_id': batch['id'], 'case_id': case['id'], 'attempt_id': record['id'],
               'job_id': record.get('job_id'), 'alias': record['config']['alias'],
               'required_files': report['source_plan']['required_files'],
               'index': case['index'], 'parameters': case.get('parameters') or []}
    fingerprint = scans.digest({'source': mapping, 'files': hashes, 'roles': contract['roles'],
                                'potcar': potcar_id, 'alias': record['config']['alias']})
    differences = {name: '\n'.join(difflib.unified_diff(files.get(name, b'').decode().splitlines(),
                    report['files'].get(name, '').splitlines(), fromfile='优化/' + name,
                    tofile='后续SCF/' + name, lineterm='')) for name in ('INCAR', 'KPOINTS')}
    row = {'case_id': case['id'], 'attempt_id': record['id'], 'index': case['index'],
           'alias': record['config']['alias'], 'source': mapping, 'fingerprint': fingerprint,
           'input_hashes': hashes, 'input_diff': differences, 'warnings': report['warnings'] + contract['warnings'], 'errors': [],
           'structure_source': report['structure_source'], 'input_files': {k: report['files'][k] for k in ('INCAR', 'KPOINTS')}}
    return project, report, contract, row, record


def _inspect(request):
    batch = source_batch(request['source_batch_id'], request['project']['id'])
    rows, aliases = [], set()
    for selection in request['selections']:
        try:
            _, _, _, row, _ = _case(request, batch, selection)
            aliases.add(row['alias'])
        except (ValueError, OSError, KeyError) as exc:
            row = {**selection, 'errors': [str(exc)], 'warnings': []}
        rows.append(row)
    if len(aliases) > 1:
        for row in rows:
            row['errors'].append('本批次使用同一服务器，请按来源主机分别选择 case 创建')
    definition = scans.digest({'request': request, 'cases': [{k: row.get(k) for k in ('case_id', 'attempt_id', 'fingerprint', 'errors')} for row in rows]})
    return {'preview_id': request['id'], 'definition_hash': definition, 'node': request['node'],
            'cases': rows, 'ready': all(not row['errors'] for row in rows)}


def preview(project, source_batch_id, selections, name='后续 SCF', incar='', grid='', center='Gamma',
            files='structure', acknowledge_differences=False):
    validate_workspace({'activeId': project['id'], 'projects': [project]})
    batch = source_batch(source_batch_id, project['id'])
    parent = next((node for node in project['nodes'] if node['id'] == batch['scan_node_id']), None)
    if parent is None or len(project['nodes']) >= 500 or len(project['edges']) >= 2000:
        raise ValueError('请在来源项目中保留迭代节点，并为后续节点留出位置')
    if (not isinstance(selections, list) or not 1 <= len(selections) <= 64 or
            any(not isinstance(item, dict) or not item.get('case_id') or not item.get('attempt_id') for item in selections) or
            len({item['case_id'] for item in selections}) != len(selections)):
        raise ValueError('请选择 1–64 个不重复的 case 与明确运行版本')
    if files not in ('structure', 'charge', 'both') or center not in ('Gamma', 'Monkhorst-Pack'):
        raise ValueError('后续计算的输入选项无效')
    if not isinstance(name, str) or not name.strip() or len(name) > 200 or not isinstance(incar, str) or len(incar) > 20000:
        raise ValueError('请输入有效批次名称和 INCAR 覆盖')
    tags = parse(incar)[0]
    if tags.get('NSW', '0') != '0' or tags.get('IBRION', '-1') != '-1':
        raise ValueError('后续 SCF 使用 NSW=0、IBRION=-1，请核对覆盖参数')
    key = uuid.uuid4().hex
    node_id = uuid.uuid4().hex
    node = {'id': node_id, 'type': 'step', 'position': {'x': parent['position']['x'] + 320, 'y': parent['position']['y']},
            'data': {'kind': 'scan', 'label': name.strip(), 'note': '', 'params': {}, 'executionMode': 'scan',
                     'stageSequence': 8 if project.get('stagePolicy') == 'kind-fixed-v1' else max(n['data']['stageSequence'] for n in project['nodes']) + 1,
                     'stageSlug': 'scf-followup', 'scanSpec': {'calculationKind': 'scf'}, 'continuationBatchId': key,
                     'sourceDraft': {'mode': 'upstream', 'purpose': 'charge', 'files': files, 'read_charge': files != 'structure'},
                     'parameters': {'preset': 'basic', 'values': {'NSW': '0', 'IBRION': '-1'}, 'manualIncarText': incar}}}
    request = {'id': key, 'project': project, 'source_batch_id': source_batch_id, 'selections': selections,
               'node': node, 'edge': {'id': uuid.uuid4().hex, 'source': parent['id'], 'target': node_id},
               'options': {'grid': grid.strip(), 'center': center, 'files': files, 'acknowledge_differences': acknowledge_differences}}
    report = _inspect(request)
    atomic_json(_directory(key) / 'preview.json', {'request': request, 'report': report})
    return report


def result(batch):
    data = batch['continuation']
    return {'project_id': batch['project_id'], 'node': data['node'], 'edge': data['edge'],
            'batch': {'id': batch['id'], 'state': batch['state'], 'case_count': len(batch['cases'])}}


def create(project_id, preview_id, definition_hash):
    folder = _directory(preview_id)
    with FileLock(str(folder / 'create.lock'), timeout=10), ExitStack() as operations:
        value = json.loads((folder / 'preview.json').read_text())
        request = value['request']
        if request['project']['id'] != project_id or value['report']['definition_hash'] != definition_hash:
            raise ValueError('项目或预览身份已变化，请重新预览')
        try:
            existing = scans.get(preview_id)
        except ValueError:
            existing = None
        if existing:
            recycle.assert_available('scans', existing['id'], existing)
            return result(existing)
        operations.enter_context(attempt_operations.locked({selection['attempt_id'] for selection in request['selections']}))
        report = _inspect(request)
        if not report['ready'] or report['definition_hash'] != definition_hash:
            raise ValueError('来源或输入发生变化，请重新预览并排除不可用项')
        source = source_batch(request['source_batch_id'], project_id)
        cases = []
        source_config = None
        requires_ncl = False
        for index, selection in enumerate(request['selections']):
            project, generated, contract, row, record = _case(request, source, selection)
            if row['fingerprint'] != report['cases'][index]['fingerprint']:
                raise ValueError('来源在准备期间发生变化，请重新预览')
            frozen = inputs.freeze(project, request['node']['id'], request['node']['data']['label'],
                                   report=generated, prepared_source=contract, execution_scope=request['node']['id'])
            source_config = record['config']
            requires_ncl |= sources.enabled(sources.tag(generated['files']['INCAR'], 'LSORBIT')) or sources.enabled(sources.tag(generated['files']['INCAR'], 'LNONCOLLINEAR'))
            cases.append({'id': scans.digest({'batch': preview_id, 'source': row['source']})[:24], 'index': index,
                          'bundle_id': frozen['id'], 'parameters': [{'kind': 'source', 'key': '来源 ' + p['key'], 'value': p['value']} for p in row['source']['parameters']],
                          'continued_from': row['source'], 'input_hashes': row['input_hashes'], 'input_diff': row['input_diff'],
                          'structure_source': row['structure_source'],
                          'warnings': row['warnings'], 'errors': [], 'state': 'PENDING', 'attempt_id': None,
                          'attempt_history': [], 'retry_count': 0})
        batch = {'schema': 'fpilot.vasp-scan.v2', 'id': preview_id, 'project_id': project_id,
                 'scan_node_id': request['node']['id'], 'base_node_id': request['node']['id'], 'stage': 'scf',
                 'state': 'FROZEN', 'created_at': ex.now(), 'reason': request['node']['data']['label'],
                 'dimensions': [], 'cases': cases, 'requires_ncl': requires_ncl, 'source_config': source_config,
                 'definition_hash': definition_hash, 'continuation': {'source_batch_id': source['id'],
                 'node': request['node'], 'edge': request['edge']}}
        batch['case_table_hash'] = scans.digest([{k: case[k] for k in ('id', 'bundle_id', 'parameters', 'input_hashes')} for case in cases])
        scans.save(batch)
        return result(batch)

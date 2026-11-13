"""Export batch membership, exact attempts and recorded results without remote I/O."""
import csv
import io
import json
import zipfile
from datetime import datetime, timezone

from . import artifact_refs, execution as ex, result_overview, scans


def _attempt(project_id, key, batch_id, case):
    record = ex.get(key)
    if record.get('project_id') != project_id:
        raise ValueError('attempt 不属于当前项目')
    reuse = case.get('reuse') or {}
    reused = reuse.get('attempt_id') == key
    expected_batch = reuse.get('batch_id') if reused else batch_id
    expected_case = reuse.get('case_id') if reused else case['id']
    if (record.get('scan_batch_id', expected_batch) != expected_batch or
            record.get('scan_case_id', expected_case) != expected_case):
        raise ValueError('attempt 与 case 的批次身份不一致')
    evidence = record.get('evidence') or {}
    cached = result_overview.read_cache(record)
    row = (cached or {}).get('row') or {}
    wannier = record.get('kind') == 'wannier90'
    if wannier:
        identity = scans.digest({'attempt': key, 'bundle': record.get('bundle_id'),
                                 'updated': record.get('updated_at'), 'evidence': record.get('evidence')})
        path = ex.ROOT / 'window-summaries' / key / (identity + '.json')
        try:
            row = {'metrics': json.loads(path.read_text())}
            cached = {'checked_at': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()}
        except (OSError, ValueError):
            pass
    result = {'attempt_id': key, 'bundle_id': record.get('bundle_id'), 'state': record['state'],
              'job_id': record.get('job_id'), 'host': record.get('config', {}).get('alias'),
              'created_at': record.get('created_at'), 'remote_path': None,
              'sources': result_overview.sources(record), 'input': result_overview.frozen_input(record),
              'convergence': {'scheduler_state': evidence.get('scheduler_state'),
                              'program_done': evidence.get('wannier_all_done') if wannier else evidence.get('vasp_timing'),
                              'electronic_converged': evidence.get('ediff_reached'),
                              'ionic_converged': (row.get('metrics') or {}).get('ionic_converged'),
                              'disentanglement_converged': evidence.get('disentanglement_converged'),
                              'localization_converged': evidence.get('localization_converged'),
                              'scientific_validation': evidence.get('scientific_validation', 'NOT_PERFORMED')},
              'metrics': row.get('metrics'), 'metrics_checked_at': (cached or {}).get('checked_at'),
              'metrics_error': row.get('error'), 'artifacts': [], 'warnings': []}
    contract = record.get('source_contract') or {}
    for name, role in contract.get('roles', {}).items():
        result['sources'].append({'type': 'source_contract', 'role': name,
                                  'attempt_id': role.get('attempt_id'), 'file': role.get('file'),
                                  'batch_id': role.get('scan_batch_id'), 'case_id': role.get('scan_case_id')})
    for source in result['sources']:
        source_id = source.get('attempt_id')
        if not source_id:
            continue
        try:
            parent = ex.get(source_id)
            if parent.get('project_id') != project_id:
                raise ValueError('来源记录不属于当前项目')
            source.update(batch_id=parent.get('scan_batch_id'), case_id=parent.get('scan_case_id'))
        except (ValueError, OSError) as exc:
            source['error'] = str(exc)
    try:
        result['remote_path'] = ex.attempt_path(record)['shell']
        catalog = artifact_refs.catalog_record(record, 'attempt')
        result.update(artifacts=catalog['items'], files_observed_at=catalog['observed_at'],
                      files_status=catalog['verification_status'], warnings=catalog['warnings'])
    except (ValueError, OSError, KeyError) as exc:
        result['warnings'].append(str(exc))
        result['files_status'] = 'UNAVAILABLE'
    return result


def manifest(batch_id, project_id):
    batch = scans.get(batch_id)
    if batch['project_id'] != project_id:
        raise ValueError('批次不属于当前项目')
    cases = []
    for case in batch['cases']:
        keys = list(dict.fromkeys([*(case.get('attempt_history') or []), case.get('attempt_id')]))
        attempts = []
        for key in keys:
            if not key:
                continue
            try:
                attempts.append(_attempt(project_id, key, batch_id, case))
            except (ValueError, OSError) as exc:
                attempts.append({'attempt_id': key, 'error': str(exc)})
        current = next((row for row in attempts if row['attempt_id'] == case.get('attempt_id')), None)
        cases.append({'case_id': case['id'], 'case_number': case['index'] + 1,
                      'parameters': case.get('parameters') or [], 'geometry': case.get('geometry'),
                      'relative_windows': case.get('relative_windows'),
                      'absolute_windows': case.get('absolute_windows'),
                      'derived_from_case_id': case.get('derived_from_case_id'),
                      'derived_from_batch_id': batch['id'] if case.get('derived_from_case_id') else None,
                      'continued_from': case.get('continued_from'),
                      'bundle_id': case.get('bundle_id'), 'input_hashes': case.get('input_hashes') or {},
                      'state': 'FAILED' if case.get('dispatch_error') else (current or {}).get('state', case.get('state')),
                      'dispatch_error': case.get('dispatch_error'), 'note': case.get('note'),
                      'retry_count': case.get('retry_count', 0),
                      'current_attempt_id': case.get('attempt_id'), 'attempts': attempts,
                      'reused_from': case.get('reuse')})
    return {'schema': 'fpilot.batch-manifest.v1', 'batch_id': batch['id'], 'project_id': project_id,
            'name': batch.get('reason') or batch.get('stage') or '参数迭代',
            'stage': batch.get('stage'), 'scan_node_id': batch.get('scan_node_id'),
            'source': batch.get('source'), 'source_frozen_id': batch.get('source_frozen_id'),
            'continuation': batch.get('continuation'),
            'state': batch['state'], 'case_table_hash': batch.get('case_table_hash'),
            'revision': batch.get('revision'), 'created_at': batch.get('created_at'),
            'collected_at': ex.now(), 'remote_checked': False, 'cases': cases,
            'units': {'energy': 'eV/cell', 'energy_per_atom': 'eV/atom',
                      'magnetization': 'mu_B/cell', 'elapsed': 's', 'volume': 'angstrom^3'},
            'notes': ['状态和文件清单来自本机保存的回执，远端实际文件在取用前需要核验。',
                      'null 表示未记录或尚未核验；程序结束、电子收敛、离子收敛和物理有效性分别报告。',
                      '每个 case 明确记录 current_attempt_id，attempts 保留该 case 的历史运行。',
                      '能窗单位为 eV，relative_windows 相对费米能，absolute_windows 使用原始能量零点；参考见 source.energy_reference。',
                      'derived_from_case_id 与 derived_from_batch_id 记录派生父 case，reused_from 记录复用已有运行。',
                      'metrics 仅采用与当前记录身份一致的已提取摘要，能量可比较性需结合输入设置判断。']}


def cases_csv(data):
    columns = ['case_number', 'case_id', 'parameters', 'state', 'attempt_id', 'is_current',
               'relative_windows', 'absolute_windows', 'source', 'source_frozen_id',
               'derived_from_case_id', 'derived_from_batch_id',
               'continued_from',
               'bundle_id', 'host', 'remote_path', 'job_id', 'program_done', 'electronic_converged',
               'ionic_converged', 'disentanglement_converged', 'localization_converged', 'scheduler_state',
               'scientific_validation', 'energy_eV_cell', 'energy_per_atom_eV',
               'sources', 'artifacts', 'files_status', 'files_observed_at', 'error']
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    for case in data['cases']:
        for attempt in case['attempts'] or [{}]:
            convergence = attempt.get('convergence') or {}
            metrics = attempt.get('metrics') or {}
            row = {'case_number': case['case_number'], 'case_id': case['case_id'],
                   'parameters': json.dumps(case['parameters'], ensure_ascii=False),
                   'relative_windows': json.dumps(case.get('relative_windows'), ensure_ascii=False),
                   'absolute_windows': json.dumps(case.get('absolute_windows'), ensure_ascii=False),
                   'source': json.dumps(data.get('source'), ensure_ascii=False),
                   'source_frozen_id': data.get('source_frozen_id'),
                   'derived_from_case_id': case.get('derived_from_case_id'),
                   'derived_from_batch_id': case.get('derived_from_batch_id'),
                   'continued_from': json.dumps(case.get('continued_from'), ensure_ascii=False),
                   'state': attempt.get('state', case['state']), 'attempt_id': attempt.get('attempt_id'),
                   'is_current': bool(attempt.get('attempt_id') and attempt['attempt_id'] == case['current_attempt_id']),
                   'energy_eV_cell': (metrics.get('energy') or {}).get('value'),
                   'energy_per_atom_eV': (metrics.get('energy_per_atom') or {}).get('value'),
                   'sources': json.dumps(attempt.get('sources') or [], ensure_ascii=False),
                   'artifacts': json.dumps([item['reference'] for item in attempt.get('artifacts') or []], ensure_ascii=False),
                   'error': attempt.get('error') or case.get('dispatch_error') or case.get('note') or ''}
            row.update({name: attempt.get(name) for name in ('bundle_id', 'host', 'remote_path', 'job_id', 'files_status', 'files_observed_at')})
            row.update(convergence)
            # Keep free text literal when opened by spreadsheet applications.
            row = {key: "'" + value if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')) else value
                   for key, value in row.items()}
            writer.writerow(row)
    return stream.getvalue()


def export(batch_id, project_id, format):
    if format not in ('json', 'csv', 'zip'):
        raise ValueError('清单格式无效')
    data = manifest(batch_id, project_id)
    document = (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
    if format == 'json':
        return document, 'application/json', 'batch.json'
    table = ('\ufeff' + cases_csv(data)).encode()
    if format == 'csv':
        return table, 'text/csv; charset=utf-8', 'cases.csv'
    readme = '\n'.join(['批次数据清单', '收集时间 ' + data['collected_at'], *data['notes'],
                        'cases.csv 每个 attempt 一行；未派发的 case 也保留一行。',
                        '能量单位 eV/cell 与 eV/atom；缺失数值在 CSV 中留空。',
                        '产物引用用 POST /api/artifact-refs/resolve 解析，提交 project_id 与 reference。',
                        'JSON 中的产物位置由宿主解析自冻结记录，文件内容仍位于列出的服务器或本机。']) + '\n'
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('batch.json', document)
        archive.writestr('cases.csv', table)
        archive.writestr('README.txt', readme)
    return stream.getvalue(), 'application/zip', 'batch-manifest-' + data['batch_id'] + '.zip'

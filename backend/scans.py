from .calculation_nodes import kind as calculation_kind, standalone
"""Frozen VASP parameter scans and batch dispatch."""
import copy
import difflib
import hashlib
import itertools
import json
import re
import uuid
from contextlib import closing
from collections import Counter
from decimal import Decimal
from pathlib import Path, PurePosixPath

from . import execution as ex
from . import recycle, execution_control as control, inputs, parameter_catalog, servers, candidate_pool
from .storage import validate_workspace
from .platform_runtime import exclusive_lock

SCHEMA = 'fpilot.vasp-scan.v1'


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(packed(value).encode()).hexdigest()


def decimal(raw):
    if not isinstance(raw, str) or not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', raw.strip()):
        raise ValueError('扫描值须为有限十进制数')
    value = Decimal(raw)
    if not value.is_finite() or abs(value) > Decimal('1e12'):
        raise ValueError('扫描数值超出范围')
    return value


def incar_tag(raw):
    if not isinstance(raw, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:/[A-Za-z][A-Za-z0-9_]*)*', raw.strip()) or len(raw.strip()) > 200:
        raise ValueError('INCAR tag 须为字母开头的参数名，可包含数字、下划线或层级斜线')
    return raw.strip().upper()


def incar_value(tag, raw):
    """Use the existing INCAR parser; one list item is exactly one assignment."""
    if not isinstance(raw, str) or not raw.strip() or len(raw.encode()) > 20000 or any(c in raw for c in '\r\n\0'):
        raise ValueError('INCAR 列表项须为非空的单行原文（最多 20 KiB）')
    from .incar_text import statements
    value = raw.strip()
    entries, malformed = statements(tag + ' = ' + value + '\n')
    if malformed or len(entries) != 1 or entries[0]['tag'] != tag or entries[0]['value'] != value:
        raise ValueError('每个列表项只能包含当前 tag 的值，不能另写赋值、注释或未闭合的引号')
    return value


def axis(dimension, stage):
    kind = dimension.get('kind')
    if kind == 'kmesh':
        meshes = dimension.get('meshes')
        if not isinstance(meshes, list) or not 1 <= len(meshes) <= 64 or any(not isinstance(m, list) or len(m) != 3 or any(type(x) is not int or not 1 <= x <= 1000 for x in m) for m in meshes):
            raise ValueError('k 网格须为 1–64 个整数三元组')
        values = [' '.join(map(str, m)) for m in meshes]
    else:
        if kind == 'incar':
            tag = incar_tag(dimension.get('tag'))
            definition = parameter_catalog.DEFINITIONS.get(tag)
        elif kind in ('u', 'j'):
            if not re.fullmatch(r'[A-Z][a-z]?', str(dimension.get('element', ''))):
                raise ValueError('U/J 元素无效')
        elif kind == 'poscar':
            from .scan_geometry import MODES
            if dimension.get('mode', 'volume_ratio') not in MODES:
                raise ValueError('POSCAR 扫描方式无效')
        else:
            raise ValueError('扫描维度类型无效')
        sampling = dimension.get('sampling', 'step')
        if sampling == 'list':
            raw_values = dimension.get('values')
            if not isinstance(raw_values, list) or not 1 <= len(raw_values) <= 64:
                raise ValueError('取值列表须包含 1–64 个值，用逗号分隔')
            if kind == 'incar':
                values = []
                for index, raw in enumerate(raw_values, 1):
                    try: values.append(incar_value(tag, raw))
                    except ValueError as error: raise ValueError(f'INCAR 列表第 {index} 项：{error}') from error
                if len(values) != len(set(values)): raise ValueError('扫描维度值重复')
                return values
            grid = []
            for index, value in enumerate(raw_values, 1):
                try: grid.append(decimal(value))
                except ValueError as error:
                    raise ValueError(f'数值列表第 {index} 项：{error}') from error
        else:
            start, end = (decimal(dimension.get(k)) for k in ('start', 'end'))
            if sampling == 'count':
                if kind != 'poscar': raise ValueError('按点数取样目前仅用于 POSCAR')
                count = dimension.get('point_count')
                if type(count) is not int or not 2 <= count <= 64:
                    raise ValueError('取样点数须为 2–64 的整数（包含起点和终点）')
                if start == end: raise ValueError('按点数取样的起点和终点不能相同')
                # Compute each point independently; a rounded recurring step must
                # never drop the endpoint or accumulate into an extra point.
                grid = [start + (end-start)*Decimal(i)/Decimal(count-1) for i in range(count)]
                grid[0], grid[-1] = start, end
            elif sampling == 'step':
                step = decimal(dimension.get('step'))
                if step == 0 or (end-start)*step < 0: raise ValueError('扫描步长方向错误')
                grid, current = [], start
                while (current <= end if step > 0 else current >= end) and len(grid) <= 64:
                    grid.append(current)
                    current += step
            else: raise ValueError('取样方式无效')
        values = []
        for current in grid:
            value = format(current.normalize(), 'f')
            if value == '-0': value = '0'
            if kind == 'incar' and definition and definition['type'] == 'integer' and current != current.to_integral_value():
                raise ValueError(tag + ' 的数值范围必须生成整数；原文取值可使用列表')
            if kind in ('u', 'j') and current < 0: raise ValueError('U/J 不得为负')
            if kind == 'poscar' and current <= 0: raise ValueError('POSCAR 扫描值必须大于零；目标体积填写正数，输出为负 scale')
            values.append(value)
        if not values or len(values) > 64: raise ValueError('单维最多 64 个值')
    if len(values) != len(set(values)): raise ValueError('扫描维度值重复')
    return values


def geometry_baseline(project, base_id, scope):
    from . import source_contracts
    report = inputs.generate(project, base_id)
    if report['errors']:
        raise ValueError('请先修正基础输入：' + '；'.join(report['errors']))
    source_contracts.validate_pead(report['files']['INCAR'])
    from .scan_geometry import validate_source
    validate_source(report['source_plan'])
    contract = source_contracts.freeze(project, base_id, report, 'lightweight', scope)
    raw = report['files']['POSCAR']
    return {'text': raw, 'sha256': hashlib.sha256(raw.encode()).hexdigest(),
            'source_contract': contract, 'source': report.get('structure_source')}


def case_project(project, base_id, dimensions, values, baseline=None):
    clone = copy.deepcopy(project)
    node = next(n for n in clone['nodes'] if n['id'] == base_id)
    layer = node['data'].setdefault('parameters', {'values': {}})
    from .calculation_nodes import inheritance
    inherited=inheritance(project,node) or {}
    base_hubbard = {**project.get('parameters', {}).get('shared', {}).get('hubbard', {}), **inherited.get('hubbard', {})}
    if any(d['kind'] in ('u','j') for d in dimensions):
        from .incar_text import parse
        report = inputs.generate(project,base_id)
        if report['errors']: raise ValueError('基础输入无法用于 U/J 扫描：'+'；'.join(report['errors']))
        tags = parse(report['files']['INCAR'])[0]
        arrays = [tags.get(k,'').split() for k in ('LDAUL','LDAUU','LDAUJ')]
        if any(len(a) != len(report['species']) for a in arrays): raise ValueError('最终 INCAR 的 LDAUL/U/J 无法按元素映射')
        base_hubbard = {s:dict(zip(('l','u','j'),v)) for s,v in zip(report['species'],zip(*arrays))}
        # A saved form may be shadowed by raw text. Start the cloned table from
        # the effective input, so scanning U cannot restore an obsolete L/J.
        layer['hubbard'] = copy.deepcopy(base_hubbard)
    for dimension, value in zip(dimensions, values):
        kind = dimension['kind']
        if kind == 'incar':
            from .incar_text import parse, replace
            tag = incar_tag(dimension['tag'])
            value = incar_value(tag, value)
            definition = parameter_catalog.DEFINITIONS.get(tag)
            if definition and definition['type'] in ('number','integer','ediffg') and len(value) <= 200:
                layer.setdefault('values', {})[tag] = value
            layer['manualIncarText'] = replace(layer.get('manualIncarText',''),tag,value)
        elif kind in ('u', 'j'):
            element = dimension['element']
            row = layer.setdefault('hubbard', {}).setdefault(element, copy.deepcopy(base_hubbard.get(element, {'l':'-1','u':'0','j':'0'})))
            row[kind] = value
            from .incar_text import parse, replace
            tag = 'LDAUU' if kind == 'u' else 'LDAUJ'
            raw = layer.get('manualIncarText','')
            if tag in parse(raw)[0]:
                fields = [layer['hubbard'].get(s,base_hubbard[s])[kind] for s in report['species']]
                layer['manualIncarText'] = replace(raw,tag,' '.join(fields))
        elif kind == 'kmesh': node['data'].setdefault('inputDraft', {})['grid'] = value
        elif kind == 'poscar':
            from .structure_edit import edit_poscar, input_geometry
            from .scan_geometry import transform
            nodes = {n['id']: n for n in clone['nodes']}
            current = base_id
            visited = set()
            while nodes[current]['data']['kind'] != 'structure':
                if current in visited: raise ValueError('存在循环依赖')
                visited.add(current)
                parents = [e['source'] for e in clone['edges'] if e['target'] == current]
                if len(parents) != 1: raise ValueError('POSCAR 扫描需要唯一上游结构')
                current = parents[0]
            document = nodes[current]['data']['structure']
            raw = baseline['text'] if baseline else input_geometry(document)['poscar']
            text, summary = transform(raw, dimension.get('mode', 'volume_ratio'), value)
            nodes[current]['data']['structure'] = edit_poscar(document, text)['structure']
            node['data']['poscarScan'] = summary
            if baseline and baseline.get('source'):
                summary['baseline_source'] = baseline['source']
            # Bind every case to the same reviewed attempt, never to whichever
            # attempt happens to become latest while the batch is freezing.
            if baseline and baseline['source_contract']['roles'].get('structure'):
                source = baseline['source_contract']['roles']['structure']
                node['data']['sourceDraft'] = {**node['data'].get('sourceDraft', {}), 'attempt_id': source['attempt_id']}
    return clone


def source_project(project,scan_node_id,base_node_id):
    """The scan's electronic source belongs to its own execution identity."""
    scan=next((n for n in project['nodes'] if n['id']==scan_node_id),None)
    if scan and scan['data'].get('sourceDraft'):
        project=copy.deepcopy(project)
        base=next(n for n in project['nodes'] if n['id']==base_node_id)
        base['data']['sourceDraft']=copy.deepcopy(scan['data']['sourceDraft'])
    return project


def preview(project, scan_node_id, base_node_id, dimensions=None, mode='vasp', frozen_id=None, window_search=None, source_draft=None, *, _include_baseline=False):
    if mode == 'wannier90-window':
        from . import wannier_scans
        return {k:v for k,v in wannier_scans.preview(project,scan_node_id,base_node_id,frozen_id,window_search,source_draft).items() if not k.startswith('_')}
    if mode != 'vasp':raise ValueError('迭代模式无效')
    project=source_project(project,scan_node_id,base_node_id)
    validate_workspace({'activeId':project['id'], 'projects':[project]})
    nodes = {n['id']:n for n in project['nodes']}
    scan, base = nodes.get(scan_node_id), nodes.get(base_node_id)
    independent = bool(scan and standalone(scan) and base_node_id == scan_node_id)
    if not scan or scan['data']['kind'] != 'scan' or not base or (not independent and (base['data']['kind'] not in ('relax','scf') or not any(e['source']==base_node_id and e['target']==scan_node_id for e in project['edges']))) or independent and calculation_kind(base, project) not in ('relax','scf','bands','dos','vasp-wannier'):
        raise ValueError('迭代节点须直接连接一个 relax 或 SCF 基础节点')
    if not isinstance(dimensions, list) or not 1 <= len(dimensions) <= 4 or any(not isinstance(d, dict) for d in dimensions):
        raise ValueError('请输入 1–4 个扫描维度')
    dimensions = [{**d, 'tag':incar_tag(d.get('tag'))} if d.get('kind') == 'incar' else d for d in dimensions]
    keys = [(d.get('kind'), None if d.get('kind') == 'poscar' else d.get('tag') or d.get('element')) for d in dimensions]
    if len(keys) != len(set(keys)): raise ValueError('扫描维度重复')
    for kind, tag in (('u','LDAUU'), ('j','LDAUJ')):
        if any(d['kind'] == kind for d in dimensions) and any(d['kind'] == 'incar' and d['tag'] == tag for d in dimensions):
            raise ValueError('不能同时迭代 '+tag+' 原文与对应的元素 '+kind.upper()+'，两者会覆盖同一个值')
    if any(d['kind'] in ('u','j') for d in dimensions):
        effective = inputs.generate(project,base_node_id)['parameters']
        if effective.get('LDAU',{}).get('value') != 'true': raise ValueError('U/J 扫描要求基础计算启用 LDAU')
    axes = [axis(d, calculation_kind(base, project)) for d in dimensions]
    count = 1
    for values in axes: count *= len(values)
    if count > 64: raise ValueError('扫描超过 64 个 case')
    baseline = geometry_baseline(project, base_node_id, scan_node_id) if any(d['kind'] == 'poscar' for d in dimensions) else None
    cases = []
    seen_inputs = set()
    base_files = inputs.generate(project, base_node_id)['files']
    if baseline:
        base_files = {**base_files, 'POSCAR': baseline['text']}
        base_files.pop('POSCAR.reference', None)
    for index, values in enumerate(itertools.product(*axes)):
        case = case_project(project, base_node_id, dimensions, values, baseline)
        report = inputs.generate(case, base_node_id)
        for dimension in dimensions:
            if dimension['kind'] == 'incar' and dimension['tag'] not in parameter_catalog.KNOWN and dimension['tag'] not in parameter_catalog.DEFINITIONS:
                report['warnings'].append(dimension['tag']+' 为自定义 INCAR tag，已按原文写入；目标 VASP 是否识别需自行确认')
        geometry_scan = next(n for n in case['nodes'] if n['id'] == base_node_id)['data'].get('poscarScan') if baseline else None
        if geometry_scan and report['files']:
            report['files']['POSCAR'] = report['files'].pop('POSCAR.reference', report['files'].get('POSCAR', ''))
            from .scan_geometry import warnings
            report['warnings'] += warnings(report['files']['INCAR'], report['stage'])
        hashes={name:hashlib.sha256(raw.encode()).hexdigest() for name,raw in report['files'].items() if name in ('INCAR','KPOINTS','POSCAR','POSCAR.reference')}
        fingerprint=(hashes.get('INCAR'),hashes.get('KPOINTS'),hashes.get('POSCAR') or hashes.get('POSCAR.reference'))
        errors=list(report['errors'])
        if fingerprint in seen_inputs: errors.append('此 case 的实际 INCAR/KPOINTS/POSCAR 与另一 case 相同')
        seen_inputs.add(fingerprint)
        differences = {}
        for name in ('INCAR', 'KPOINTS', 'POSCAR'):
            before = base_files.get(name, '').splitlines()
            after = report['files'].get(name, '').splitlines()
            if before != after:
                differences[name] = '\n'.join(difflib.unified_diff(before, after, fromfile='base/'+name, tofile='case/'+name, lineterm=''))
        from .scan_geometry import KEYS
        cases.append({'index':index, 'parameters':[{'kind':d['kind'],'key':KEYS[d.get('mode', 'volume_ratio')] if d['kind'] == 'poscar' else d.get('tag') or d.get('element') or 'grid','value':v} for d,v in zip(dimensions,values)], 'input_hashes':hashes, 'input_diff':differences, 'errors':errors, 'warnings':report['warnings'], 'dependencies':report['dependencies'], **({'geometry': geometry_scan} if geometry_scan else {})})
    definition = {'project':project,'scan_node_id':scan_node_id,'base_node_id':base_node_id,'dimensions':dimensions}
    if baseline:
        definition['geometry_baseline'] = {k:v for k,v in baseline.items() if k != 'text'}
    result = {'project_id':project['id'], 'scan_node_id':scan_node_id, 'base_node_id':base_node_id, 'stage':calculation_kind(base, project), 'dimensions':dimensions, 'case_count':count, 'cases':cases, 'ready':all(not c['errors'] for c in cases), 'definition_hash':digest(definition)}
    if _include_baseline: result['_geometry_baseline'] = baseline
    return result


def database():
    db = ex.db()
    db.execute('CREATE TABLE IF NOT EXISTS scan_batches (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, state TEXT NOT NULL, body TEXT NOT NULL)')
    db.commit()
    return db


def save(batch):
    if recycle.inactive('scans',batch['id'],batch):return batch
    with closing(database()) as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT body FROM scan_batches WHERE id=?',(batch['id'],)).fetchone()
        revision=json.loads(row[0]).get('revision',0) if row else 0
        if revision!=batch.get('revision',0):
            db.rollback();raise ValueError('批次状态已由其他操作更新；请刷新')
        batch['revision']=revision+1
        batch['updated_at']=ex.now()
        db.execute('INSERT OR REPLACE INTO scan_batches VALUES(?,?,?,?)',(batch['id'],batch['project_id'],batch['state'],packed(batch)))
        db.commit()
    return batch


def get(batch_id):
    with closing(database()) as db:
        row = db.execute('SELECT body FROM scan_batches WHERE id=?',(batch_id,)).fetchone()
    if not row: raise ValueError('扫描批次不存在')
    return json.loads(row[0])


def status(batch_id, project_id):
    """Read local attempt progress without SSH or changing the batch revision."""
    batch = get(batch_id)
    if batch['project_id'] != project_id:
        raise ValueError('批次不属于当前项目')
    for case in batch['cases']:
        if not case.get('attempt_id'):
            continue
        try:
            record = ex.get(case['attempt_id'])
        except ValueError:
            continue
        if (record.get('project_id') != project_id or
                record.get('scan_batch_id', batch_id) != batch_id):
            continue
        case['state'] = 'FAILED' if case.get('dispatch_error') else record['state']
        case['job_id'] = record.get('job_id')
        if record['state'] == 'UNKNOWN' and record.get('note'):
            case['note'] = record['note']
    for case in batch['cases']:
        if case.get('dispatch_error'):case['note']=case['dispatch_error']
        if case.get('recovery_operation_id'):
            from .task_recovery import reference
            op=reference(case['recovery_operation_id'])
            info=next((i for i in op['items'] if case['id'] in (i['id'],i.get('attached_id'))),None)
            if info and info['dispatch_state']=='FAILED':case.update(state='FAILED',note=info.get('error'))
            elif info and info['dispatch_state']=='PENDING':case.update(state='RECOVERY_PENDING',note=info.get('waiting_reason') or '已授权，等待本次恢复派发')
    batch['counts']={state:sum(c.get('state')==state for c in batch['cases']) for state in ('PENDING','RECOVERY_PENDING','PREPARED','SUBMITTED','QUEUED','RUNNING','HELD','EXITING','COMPLETED','FAILED','UNKNOWN','CANCELLED')}
    return batch


def _dispatch_lock(batch_id):
    return exclusive_lock(ex.ROOT/'scan-locks'/(digest(batch_id)+'.lock'))


def _finish_initial_dispatch(batch_id):
    # A pause may win while SSH is in flight. Merge only dispatch metadata.
    for _ in range(3):
        batch = get(batch_id)
        if batch.get('initial_dispatch', {}).get('state') != 'IN_PROGRESS':
            return
        batch['initial_dispatch'].update(state='FINISHED', finished_at=ex.now())
        try:
            save(batch)
            return
        except ValueError:
            if get(batch_id)['revision'] == batch['revision']:
                raise


def authorize_and_dispatch(batch_id, **payload):
    request_hash = digest(payload)
    with _dispatch_lock(batch_id) as owned:
        current = get(batch_id)
        if (current['project_id'] == payload['project_id'] and current.get('authorization') and
                current.get('initial_dispatch', {}).get('request_hash') == request_hash):
            return status(batch_id, payload['project_id'])
        if not owned:
            raise ValueError('扫描批次正在核对或派发，请读取当前状态后重试')
        batch = authorize(batch_id, **payload)
        batch['initial_dispatch'] = {'state': 'IN_PROGRESS', 'started_at': ex.now(),
                                     'request_hash': request_hash}
        save(batch)
        _dispatch_pass(batch_id, {})
        return status(batch_id, payload['project_id'])


def history(project_id):
    with closing(database()) as db:
        rows = db.execute('SELECT body FROM scan_batches WHERE project_id=? ORDER BY rowid DESC',(project_id,)).fetchall()
    from .recycle import is_recycled
    return [item for row in rows if not recycle.inactive('scans',(item := json.loads(row[0]))['id'],item)]


def freeze(project, scan_node_id, base_node_id, dimensions, reason, expected_definition_hash=None, mode='vasp', frozen_id=None, window_search=None, source_draft=None):
    if mode == 'wannier90-window':
        from . import wannier_scans
        return wannier_scans.freeze(project,scan_node_id,base_node_id,frozen_id,window_search,reason,expected_definition_hash,source_draft)
    if mode != 'vasp':raise ValueError('迭代模式无效')
    project=source_project(project,scan_node_id,base_node_id)
    if not isinstance(reason,str) or len(reason)>2000: raise ValueError('备注最多 2000 字')
    report = preview(project,scan_node_id,base_node_id,dimensions,_include_baseline=True)
    dimensions = report['dimensions']
    baseline = report.pop('_geometry_baseline')
    if expected_definition_hash is not None and report['definition_hash']!=expected_definition_hash:
        raise ValueError('基础输入或扫描定义已变化，请重新预览')
    if not report['ready']: raise ValueError('存在输入错误的 case，不能冻结')
    batch_id = uuid.uuid4().hex
    cases = []
    axes = [axis(d,report['stage']) for d in dimensions]
    for item, values in zip(report['cases'],itertools.product(*axes)):
        case=case_project(project,base_node_id,dimensions,values,baseline)
        base=next(n for n in case['nodes'] if n['id']==base_node_id)
        if not base['data'].get('sourceDraft'):
            from .source_contracts import draft
            base['data']['sourceDraft']=draft(case,base_node_id)[0]
        frozen = inputs.freeze(case,base_node_id,reason,execution_scope=scan_node_id)
        cases.append({**item,'id':hashlib.sha256((batch_id+':'+str(item['index'])+':'+packed(item['parameters'])).encode()).hexdigest()[:24], 'bundle_id':frozen['id'], 'state':'PENDING', 'attempt_id':None, 'attempt_history':[], 'retry_count':0})
    batch = {'schema':'fpilot.vasp-scan.v2' if base_node_id == scan_node_id else SCHEMA,'id':batch_id,'project_id':project['id'],'scan_node_id':scan_node_id,'base_node_id':base_node_id,'stage':report['stage'],'definition_hash':report['definition_hash'],'dimensions':dimensions,'cases':cases,'state':'FROZEN','created_at':ex.now(),'reason':reason.strip()}
    manifest,_=ex.bundle(cases[0]['bundle_id'],allow_dependencies=True)
    batch['requires_ncl']=any(str(manifest.get('incar_tags',{}).get(tag,{}).get('value','')).strip().upper() in ('.TRUE.','TRUE','T') for tag in ('LSORBIT','LNONCOLLINEAR'))
    batch['case_table_hash'] = digest([{k:c[k] for k in ('id','bundle_id','parameters','input_hashes')} for c in cases])
    return save(batch)


def resource_preview(batch_id, project_id, config, candidates):
    from .execution_modes import require_batch_scheduler
    require_batch_scheduler(config)
    batch = get(batch_id)
    if batch['project_id'] != project_id or batch['state'] != 'FROZEN':
        raise ValueError('请选择当前项目的冻结批次')
    from . import wannier_scans
    if wannier_scans.is_window(batch):return wannier_scans.resources(batch,config,candidates)
    candidates = candidate_pool.validate(candidates)
    probe = servers.submission_probe(config)
    return {'previews': candidate_pool.previews(config, probe, candidates)}


def authorize(batch_id, project_id, case_table_hash, config, node='', cores=None, reason='', approved_script='', approved_profile=None, template_id=None, dependency_attempt_id=None, dependency_reason='', candidates=None, approved_previews=None, max_parallel=None):
    from .execution_modes import require_batch_scheduler
    require_batch_scheduler(config)
    batch = get(batch_id)
    from . import wannier_scans
    if wannier_scans.is_window(batch):
        return wannier_scans.authorize(batch,project_id,case_table_hash,config,candidates,approved_previews,max_parallel)
    recycle.assert_available('scans',batch['id'],batch)
    batch.pop('local_cleanup_blocker',None)
    if batch['project_id'] != project_id or batch['state'] != 'FROZEN' or batch['case_table_hash'] != case_table_hash: raise ValueError('冻结 case 表或批次状态已变化；未授权')
    if not isinstance(reason,str) or len(reason)>2000: raise ValueError('资源选择理由无效')
    if template_id is None:
        template_id=servers.selected_template(config)
    if config.get('runtime_source')=='script' and template_id:config=servers.with_script(config,template_id,'vasp_ncl' if batch.get('requires_ncl') else 'vasp_std')
    from . import branch_bindings
    for case in batch['cases']:
        manifest,_=ex.bundle(case['bundle_id'],allow_dependencies=True)
        branch_bindings.check(ex.frozen_project(case['bundle_id']),batch['base_node_id'],config['alias'],manifest.get('source_contract'),batch['scan_node_id'])
    probe = servers.submission_probe(config)
    checked = None
    if candidates is not None:
        candidates = candidate_pool.validate(candidates)
        checked = candidate_pool.approve(candidates, candidate_pool.previews(config, probe, candidates), approved_previews)
        if max_parallel is None: max_parallel = len(checked)
        if type(max_parallel) is not int or not 1 <= max_parallel <= 100000:
            raise ValueError('本批在途上限须为正整数')
    else:
        # Already frozen older clients/authorizations keep their single-node contract.
        rendered = servers.preview(config,probe['id'],node,cores,reason)
        if not rendered['resource_ready']: raise ValueError('资源检查未通过：'+'; '.join(rendered['blockers']))
        if rendered['script'] != approved_script or rendered['profile'] != approved_profile:
            raise ValueError('脚本或资源预览已变化；请重新预览后授权')
    from .workbench_settings import snapshot as settings_snapshot
    batch['authorization']={'settings_snapshot':settings_snapshot(config['alias'],servers.program_name(config),config,template_id),'at':ex.now(),'case_table_hash':case_table_hash,'config':config,'reason':reason,'template_id':template_id,'dependency_attempt_id':dependency_attempt_id,'dependency_reason':dependency_reason,'approved_case_count':len(batch['cases'])}
    if checked is not None:
        batch['authorization'].update(candidates=checked, max_parallel=max_parallel)
    else:
        batch['authorization'].update(node=node,cores=cores,preview_profile=rendered['profile'],approved_script_sha256=hashlib.sha256(rendered['script'].encode()).hexdigest())
    batch['state']='ACTIVE'
    return save(batch)


def pause(batch_id, project_id):
    batch=get(batch_id)
    if batch['project_id']!=project_id or batch['state'] not in ('ACTIVE','BLOCKED'): raise ValueError('批次不能暂停')
    batch.update(state='PAUSED',dispatch_pause_at=ex.now())
    return save(batch)


def resume(batch_id, project_id):
    batch=get(batch_id)
    recycle.assert_available('scans',batch['id'],batch)
    batch.pop('local_cleanup_blocker',None)
    if batch['project_id']!=project_id or batch['state'] not in ('PAUSED','BLOCKED'): raise ValueError('批次不能继续')
    from . import wannier_scans
    if wannier_scans.is_window(batch):
        for case in batch['cases']:
            if case.get('attempt_id'):
                record=ex.get(case['attempt_id']);recycle.assert_available('attempts',record['id'],record);case['state']=record['state']
    if any(c['state']=='UNKNOWN' for c in batch['cases']): raise ValueError('仍有 UNKNOWN case，须先核对原作业')
    batch['state']='ACTIVE'
    batch.pop('blocker',None)
    return save(batch)


def case_scopes():
    """Read execution ownership of legacy case bundles without rewriting them."""
    if not (ex.ROOT/'execution.sqlite3').exists():return {}
    result={}
    with closing(ex.db()) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='scan_batches'").fetchone():return result
        for (body,) in c.execute('SELECT body FROM scan_batches'):
            batch=json.loads(body)
            for case in batch['cases']:
                result.setdefault(case['bundle_id'],set()).add(batch['scan_node_id'])
    return {key:sorted(values) for key,values in result.items()}


def retry_case(batch_id, project_id, case_id):
    from . import task_recovery
    op=task_recovery.preview(project_id,'scans',batch_id,[case_id])
    task_recovery.confirm(op['id'],project_id)
    return status(batch_id,project_id)


def tick(capacity_context=None):
    """Fill available candidates within one cycle; keep exact receipts on recovery."""
    with closing(database()) as db:
        ids=[r[0] for r in db.execute("SELECT id FROM scan_batches WHERE state IN ('ACTIVE','BLOCKED','PAUSED') ORDER BY rowid")]
    capacity_context={} if capacity_context is None else capacity_context
    for batch_id in ids:
        with _dispatch_lock(batch_id) as owned:
            if owned:
                _dispatch_pass(batch_id, capacity_context)


def _dispatch_pass(batch_id, capacity_context):
    dispatched=set()
    try:
        for _ in range(64):
            if not _tick_batch_once(batch_id,capacity_context,dispatched):break
        batch = get(batch_id)
        if batch.get('last_worker_error'):
            batch.pop('last_worker_error')
            save(batch)
    except Exception as exc:
        batch = get(batch_id)
        if batch['state'] == 'ACTIVE':
            batch['last_worker_error'] = control.redact(str(exc))[:1000]
            try: save(batch)
            except ValueError: pass
    finally:
        _finish_initial_dispatch(batch_id)


def _tick_batch_once(batch_id,capacity_context,dispatched):
    batch=get(batch_id)
    if recycle.inactive('scans',batch['id'],batch) or batch.get('local_cleanup_blocker'):return False
    from . import wannier_scans
    window_mode=wannier_scans.is_window(batch)
    for case in batch['cases']:
        try:
            if window_mode:recycle.assert_available('wannier-inputs',case['bundle_id'])
            if case.get('attempt_id'):
                record=ex.get(case['attempt_id'])
                recycle.assert_available('attempts',record['id'],record)
                case['state']='FAILED' if case.get('dispatch_error') else record['state']
        except (ValueError,OSError) as exc:
            case.update(state='FAILED',dispatch_error=str(exc))
    if batch['state'] in ('BLOCKED','PAUSED'): save(batch);return False
    from .project_sync import locally_executable
    if not locally_executable(batch['project_id']):return False
    waiting=next((c for c in batch['cases'] if c['state'] in ('PENDING','PREPARED') and not c.get('dispatch_error') and not c.get('recovery_operation_id')),None)
    if not waiting:
        if all(c['state'] in ('COMPLETED','FAILED','CANCELLED') for c in batch['cases']): batch['state']='COMPLETE'
        save(batch);return False
    if waiting['id'] in dispatched:return False
    auth=batch['authorization'];limits=control.settings()
    render=(lambda config,probe,node,cores,reason: wannier_scans.render(config,probe,node,cores,reason,auth['template_id'])) if window_mode else None
    purpose='wannier90' if window_mode else 'vasp'
    with closing(ex.db()) as db:
        active=[json.loads(r[0]) for r in db.execute('SELECT body FROM attempts WHERE state IN ('+','.join('?' for _ in control.ACTIVE)+')',control.ACTIVE)]
    from .extension_runs import active_usage
    active += active_usage()
    same = [r for r in active if r['config']['alias']==auth['config']['alias']]
    batch_active = sum(c.get('attempt_id') in {r['id'] for r in active if not r.get('quota_released')} for c in batch['cases'])
    active=[r for r in active if not r.get('quota_released')]
    same=[r for r in same if not r.get('quota_released')]
    if ((limits['max_inflight_total'] is not None and len(active)>=limits['max_inflight_total']) or (limits['max_inflight_per_host'] is not None and len(same)>=limits['max_inflight_per_host']) or
            batch_active>=auth.get('max_parallel',len(batch['cases']))):
        batch['waiting_reason']='达到同时运行上限，已有任务结束后继续派发'
        save(batch);return False
    try:
        from .workbench_settings import assert_current
        assert_current(auth.get('settings_snapshot'))
        choices=auth.get('candidates') or [{'node':auth['node'],'cores':auth['cores'],'reason':auth['reason'],'profile':auth['preview_profile'],'script_sha256':auth['approved_script_sha256']}]
        deterministic_id=hashlib.sha256((waiting['id']+':'+str(waiting['retry_count'])).encode()).hexdigest()[:32]
        if waiting['state']=='PENDING':
            # Recover the exact attempt if the process stopped after its save.
            try: existing=ex.get(deterministic_id)
            except ValueError: existing=None
            if existing:
                allowed=next((c for c in choices if c['node']==existing.get('node')),None)
                if (not allowed or existing.get('bundle_id')!=waiting['bundle_id'] or
                        existing.get('project_id')!=batch['project_id'] or existing.get('config')!=auth['config'] or
                        existing.get('profile')!=allowed['profile'] or
                        existing.get('execution_scope')!=batch['scan_node_id'] or
                        existing.get('scan_case_id',waiting['id'])!=waiting['id']):
                    raise ValueError('原 case attempt 与授权不一致，须核查原记录')
                waiting.update(attempt_id=existing['id'],state=existing['state']);save(batch)
                if existing['state']!='PREPARED':
                    return existing['state'] not in ('UNKNOWN','DISPATCHING')
        probe=None
        if waiting['state']=='PENDING' or auth.get('candidates'):
            probe=(servers.dispatch_probe(auth['config'],context=capacity_context,**({'purpose':purpose} if window_mode else {})) if auth.get('candidates')
                   else servers.submission_probe(auth['config']))
        if waiting['state']=='PENDING':
            occupied=Counter(r['node'] for r in same if r.get('node'))
            choice,message=candidate_pool.select(auth['config'],probe,choices,occupied,**({'render':render} if window_mode else {}))
            if choice is None:
                batch['waiting_reason']=message;save(batch);return False
            cap=limits['max_cores_per_host']
            counts=[r.get('profile',{}).get('ppn',r.get('profile',{}).get('ntasks')) for r in same]
            required=choice['profile'].get('ppn',choice['profile'].get('ntasks'))
            if cap is not None and (type(required) is not int or any(type(n) is not int for n in counts) or sum(counts)+required>cap):
                batch['waiting_reason']='每主机在途核数预算不足，等待已有任务结束';save(batch);return False
            prepared=wannier_scans.prepare_case(batch,waiting,auth,choice,probe,deterministic_id) if window_mode else ex.prepare(waiting['bundle_id'],auth['config'],probe['id'],choice['node'],choice['cores'],choice['reason'],auth['template_id'],dependency_attempt_id=auth['dependency_attempt_id'],dependency_reason=auth['dependency_reason'],project_id=batch['project_id'],attempt_id=deterministic_id,execution_scope=batch['scan_node_id'])
            prepared.update(scan_batch_id=batch['id'],scan_case_id=waiting['id'],scan_case_table_hash=batch['case_table_hash'])
            ex.save(prepared)
            waiting['attempt_id']=prepared['id'];waiting['state']=prepared['state'];save(batch)
        if waiting['state']=='PREPARED':
            prepared=ex.get(waiting['attempt_id'])
            allowed=next((c for c in choices if c['node']==prepared.get('node')),None)
            if auth.get('candidates') and (not allowed or prepared.get('profile')!=allowed['profile']):
                raise ValueError('准备记录已偏离本批候选池；请核查准确 attempt')
            if allowed and auth.get('candidates'):
                _,message=candidate_pool.select(auth['config'],probe,[allowed],Counter(r['node'] for r in same if r.get('node')),**({'render':render} if window_mode else {}))
                if message: batch['waiting_reason']=message;save(batch);return False
            cap=limits['max_cores_per_host']
            counts=[r.get('profile',{}).get('ppn',r.get('profile',{}).get('ntasks')) for r in same+[prepared]]
            if cap is not None and (any(type(n) is not int or n<1 for n in counts) or sum(counts)>cap):
                batch['waiting_reason']='每主机在途核数预算不足，等待已有任务结束';save(batch);return False
        if get(batch_id)['state']!='ACTIVE': return False
        batch.pop('waiting_reason',None)
        dispatched.add(waiting['id'])
        waiting['state']=ex.submit(waiting['attempt_id'])['state']
        save(batch)
        return True  # one failed/uncertain case never stops independent cases
    except Exception as exc:
        if waiting['state']=='PENDING' and str(exc).startswith('资源检查未通过：'):
            blockers=str(exc).split('：',1)[1].split('; ')
            if candidate_pool.capacity_wait({'node_status':'BUSY','blockers':blockers}):
                batch['waiting_reason']=control.redact(str(exc))[:1000];save(batch);return False
        if waiting['attempt_id']: waiting['state']=ex.get(waiting['attempt_id'])['state']
        if waiting['state']=='PREPARED' and str(exc).startswith(('在途任务数已达上限','每主机在途核数预算不足')):
            batch['waiting_reason']=str(exc);save(batch);return False
        if waiting['state']=='PREPARED' and str(exc).startswith(('实时资源已变化或不可用','Wannier90 实时资源已变化')):
            # Capacity may change between selection and the final submit gate.
            # Keep the exact unsubmitted attempt; other contract changes block.
            prepared=ex.get(waiting['attempt_id'])
            try:
                fresh=servers.dispatch_probe(auth['config'],**({'purpose':purpose} if window_mode else {}))
                current=(render or servers.preview)(auth['config'],fresh['id'],prepared['node'],prepared['cores'],prepared['reason'])
                if candidate_pool.capacity_wait(current) and current['profile']==prepared['profile']:
                    batch['waiting_reason']='原准备节点暂时占用，等待空闲后提交同一个 attempt';save(batch);return False
            except (ValueError,KeyError,OSError):pass
        batch.pop('waiting_reason',None)
        if waiting['state']=='UNKNOWN':
            waiting['note']=control.redact(str(exc))[:1000];save(batch);return True
        waiting['dispatch_error']=control.redact(str(exc))[:1000]
        if not waiting.get('attempt_id'):waiting['state']='FAILED'
        save(batch)
        return True

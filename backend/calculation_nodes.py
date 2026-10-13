"""Calculation type and case ownership, independent of canvas container identity."""
import hashlib
import re

VASP = ('relax', 'scf', 'bands', 'dos', 'vasp-wannier')
KINDS = (*VASP, 'wannier90')


def standalone(node):
    return node.get('data', {}).get('kind') == 'scan' and bool(node['data'].get('scanSpec'))


def kind(node, project=None):
    data = node.get('data', {})
    if data.get('kind') != 'scan': return data.get('kind')
    spec = data.get('scanSpec')
    if spec:
        if not isinstance(spec, dict) or spec.get('calculationKind') not in KINDS:
            raise ValueError('迭代节点计算类型无效')
        return spec['calculationKind']
    if project:
        parent = next((n for n in project['nodes'] if any(e['source'] == n['id'] and e['target'] == node['id'] for e in project['edges'])), None)
        if parent and parent['data']['kind'] in KINDS: return parent['data']['kind']
    return 'scan'


def owner(record):
    from .branch_bindings import owner as original
    return original(record)


def case_info(record):
    if not record.get('scan_batch_id'): return {}
    from . import scans
    try:
        batch = scans.get(record['scan_batch_id'])
        case = next((c for c in batch['cases'] if c.get('id') == record.get('scan_case_id') or
                     record['id'] in [c.get('attempt_id'), *(c.get('attempt_history') or [])]), None)
        if not case: return {}
        parameters = case.get('parameters') or [{'kind':'window','key':k,'value':v} for k,v in case.get('relative_windows',{}).items() if v is not None]
        parts = []
        for p in parameters:
            key, raw = p['key'], str(p['value'])
            try: value = format(float(raw), '.10g')
            except ValueError: value = raw
            label = {'V / Å³': '目标体积 / Å³', 'grid': 'k 网格'}.get(key, key)
            if p['kind'] in ('u', 'j'): label = key + ' ' + p['kind'].upper() + ' / eV'
            if p['kind'] == 'window': label += ' / E−E_F (eV)'
            parts.append(label + '=' + value)
        seed=batch.get('source',{}).get('seed','')
        if seed.endswith(('.1','.2')): parts.append('通道 '+seed[-1])
        return {'scan_batch_id': batch['id'], 'scan_case_id': case['id'], 'case_index': case['index'],
                'case_parameters': parameters, 'scan_geometry': case.get('geometry'), 'parameter_summary': ' · '.join(parts),
                'batch_created_at': batch.get('created_at'), 'batch_reason': batch.get('reason', ''),
                'calculation_kind': batch.get('stage'), 'owner_node_id': batch['scan_node_id']}
    except (ValueError, KeyError, OSError): return {}


def validate_case(record, draft, parent_id=None):
    info = case_info(record)
    if not info: raise ValueError('所选迭代 case 的历史身份不可核对')
    if parent_id and owner(record) != parent_id: raise ValueError('所选 case 不属于相连迭代节点')
    for field in ('scan_batch_id', 'scan_case_id'):
        if draft.get(field) and draft[field] != info[field]: raise ValueError('case、批次与所选运行版本不一致')
    return info


def inheritance(project, node):
    draft = node['data'].get('sourceDraft') or {}
    parent_id = next((e['source'] for e in project['edges'] if e['target'] == node['id']), None)
    parent = next((n for n in project['nodes'] if n['id'] == parent_id), None)
    if not parent or parent['data']['kind'] != 'scan' or draft.get('mode', 'upstream') not in ('new','upstream'): return None
    key = draft.get('attempt_id')
    if not key: return None  # Selection must be explicit, never latest case.
    from . import execution as ex, source_contracts as sources, parameter_catalog as catalog
    record = ex.get(key); info = validate_case(record, draft, parent_id)
    if record.get('project_id') != project['id']: raise ValueError('case 不属于当前项目')
    manifest, files = sources.actual_inputs(record)
    if 'INCAR' not in files: return None
    text = files['INCAR'].decode()
    values = {}
    omitted = catalog.MANAGED | catalog.CONTRACT_LOCKED | {'ISTART','ICHARG','LCHARG','LWAVE','NSW','IBRION','ISIF','EDIFFG','POTIM'}
    for tag, definition in catalog.DEFINITIONS.items():
        if tag in omitted or definition.get('applies_to') and kind(node, project) not in definition['applies_to']: continue
        raw = sources.tag(text, tag)
        if raw is None: continue
        if definition['type'] == 'boolean' or {c[0] for c in definition.get('choices', [])} == {'true', 'false'}: raw = 'true' if raw.upper().strip('.') in ('T','TRUE') else 'false'
        elif definition.get('choices'):
            raw = next((c[0] for c in definition['choices'] if c[0].upper() == raw.upper()), raw)
        values[tag] = raw
    old_project = ex.frozen_project(record['bundle_id'])
    old_node = next(n for n in old_project['nodes'] if n['id'] == record['node_id'])
    function = old_node['data'].get('parameters', {}).get('values', {}).get('XC_FUNCTIONAL') or old_project.get('parameters', {}).get('shared', {}).get('values', {}).get('XC_FUNCTIONAL')
    if function: values['XC_FUNCTIONAL'] = function
    species = manifest.get('species') or list(dict.fromkeys(a['element'] for a in sources.structure_geometry(files.get('POSCAR', files.get('POSCAR.reference')))['atoms']))
    def sequence(tag):
        result = []
        for token in (sources.tag(text, tag) or '').split():
            match = re.fullmatch(r'(\d+)\*(.+)', token)
            if match:
                count = int(match[1])
                if count > 100000: raise ValueError('case 数组参数重复次数异常')
                result.extend([match[2]] * count)
            else: result.append(token)
        return result
    hubbard = {}
    arrays = [sequence(t) for t in ('LDAUL', 'LDAUU', 'LDAUJ')]
    if values.get('LDAU') == 'true':
        if any(len(a) != len(species) for a in arrays): raise ValueError('case Hubbard 数组与元素顺序不符')
        hubbard = {element: dict(zip(('l','u','j'), (a[i] for a in arrays))) for i,element in enumerate(species)}
    atoms = sources.structure_geometry(files.get('POSCAR', files.get('POSCAR.reference')))['atoms']
    raw_moments = sequence('MAGMOM')
    ncl = values.get('LSORBIT') == 'true' or values.get('LNONCOLLINEAR') == 'true'
    width = 3 if ncl else 1
    if raw_moments and len(raw_moments) != len(atoms)*width: raise ValueError('case 磁矩与原子顺序不符')
    return {'values': values, 'hubbard': hubbard, 'elements': [a['element'] for a in atoms],
            'moments': [' '.join(raw_moments[i:i+width]) for i in range(0,len(raw_moments),width)],
            'snapshot': {**info, 'attempt_id': key, 'job_id': record.get('job_id'),
                         'incar_sha256': hashlib.sha256(files['INCAR']).hexdigest(), 'values': values, 'hubbard': hubbard, 'elements':[a['element'] for a in atoms], 'moments':[' '.join(raw_moments[i:i+width]) for i in range(0,len(raw_moments),width)]}}

"""Allowlisted local post-processing bound to one remote attempt."""
import hashlib
import json
import subprocess
import sys
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import artifacts, execution, extension_sdk
from .recycle import is_recycled
from .storage import ROOT

def _manifest(bundle_id):
    receipt = extension_sdk.status(bundle_id)
    root = receipt['root']
    if (root / 'extension.json').is_file():
        value = json.loads((root / 'extension.json').read_text(encoding='utf-8'))
        row = {key: value.get(key) for key in ('name', 'version', 'status', 'domain', 'description', 'module_type', 'requires', 'provides')}
    else:
        contract = receipt['contract']
        row = {'name': contract['name'], 'version': contract['version'], 'status': 'ready',
               'domain': 'structure-analysis', 'description': contract['description'],
               'module_type': 'postprocessor', 'requires': ['structure.poscar-or-contcar'],
               'provides': [contract['outputs']['schema']]}
    return {**row, 'sdk_revision': receipt['revision'], 'sdk_adapter': receipt['contract']['adapter']}


def catalog(attempt_id=None, listing=None):
    files = []
    record = None
    if attempt_id:
        record = execution.get(attempt_id)
        files = (listing if listing is not None else artifacts.listing(attempt_id))['files']
    names = {item['name']: item for item in files if item['path'] == item['name']}
    rows = []
    for key in extension_sdk.available_ids():
        try:
            sdk = extension_sdk.status(key)
            item = _manifest(key)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            rows.append({'id': key, 'name': key, 'status': 'invalid', 'ready': False, 'installed': False,
                         'enabled': False, 'missing': ['扩展校验失败：' + str(exc)[:300]]})
            continue
        missing = []
        ready = False
        if sdk['missing_dependencies']:
            missing.append('缺少运行依赖：' + ', '.join(sdk['missing_dependencies']))
        if not sdk['installed']:
            missing.append('尚未安装或代码已变化，需要重新安装')
        elif not sdk['enabled']:
            missing.append('尚未启用')
        if key.startswith('crystal-') or sdk['contract']['adapter'] == 'cli-v1':
            structures = [names[name] for name in ('CONTCAR', 'POSCAR') if name in names]
            if not structures: missing.append('CONTCAR 或 POSCAR')
            elif not any(item['size_bytes'] <= artifacts.MAX_PULL_BYTES for item in structures): missing.append('结构文件超过 10 MiB')
            ready = not missing
        else:
            structure = names.get('POSCAR') or names.get('CONTCAR')
            outcar = names.get('OUTCAR')
            if not structure: missing.append('POSCAR 或 CONTCAR')
            if not outcar: missing.append('OUTCAR')
            if outcar and outcar['size_bytes'] > artifacts.MAX_PULL_BYTES: missing.append('OUTCAR 超过 10 MiB，需要服务器侧摘要适配器')
            if record and (record.get('state') != 'COMPLETED' or not (record.get('evidence') or {}).get('vasp_timing')):
                missing.append('缺少 COMPLETED 与 VASP timing 证据')
            ready = not missing
        rows.append({**item, 'id': key, 'ready': ready, 'missing': missing,
                     'installed': sdk['installed'], 'enabled': sdk['enabled'], 'valid': sdk['valid'],
                     'requires_configuration': key.startswith('crystal-') or sdk['contract']['adapter'] == 'cli-v1'})
    rows.extend([
        {'id':'pyw90','name':'pyw90','status':'experimental','module_type':'analysis_gate','ready':False,'missing':['当前尚无 Wannier 候选结果与 checkpoint lineage'],'description':'Wannier 候选结果后的人工分析关卡。'},
        {'id':'local-crystal-field-analysis','name':'local-crystal-field-analysis','status':'ready','module_type':'postprocessor','ready':False,'missing':['当前节点没有 Wannier90 Hamiltonian bundle'],'description':'局域晶场与 Hamiltonian 分析。'},
        {'id':'tb2j','name':'tb2j','status':'experimental','module_type':'postprocessor','ready':False,'missing':['当前节点没有 Hamiltonian、费米能和磁性选择'],'description':'交换作用后处理。'},
    ])
    return rows


def _write_record(directory, record):
    (directory / 'analysis.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return record


def _distortion_options(options):
    if not isinstance(options, dict) or set(options) != {'structure_source', 'center_element', 'ligand_element', 'coordination', 'search_radius_angstrom'}:
        raise ValueError('请明确结构来源、中心元素、配体、配位数与搜索半径')
    source = options['structure_source']
    center, ligand = options['center_element'], options['ligand_element']
    if source not in ('POSCAR', 'CONTCAR') or not all(isinstance(s, str) and re.fullmatch(r'[A-Z][a-z]?', s) for s in (center, ligand)):
        raise ValueError('结构来源或元素符号无效')
    if type(options['coordination']) is not int or options['coordination'] not in (4, 6):
        raise ValueError('配位数仅支持 4 或 6')
    radius = options['search_radius_angstrom']
    if isinstance(radius, bool) or not isinstance(radius, (float, int)) or not 0 < radius <= 20:
        raise ValueError('搜索半径须在 0–20 Å 范围内')
    if center == ligand:
        raise ValueError('中心元素与配体元素须不同')
    return {'centers': {center: {'coordination': options['coordination'], 'ligand': ligand}},
            'search_radius_angstrom': float(radius), 'shell_gap_warning_angstrom': 0.15}


def _cli_options(contract, options):
    if not isinstance(options, dict) or len(json.dumps(options, ensure_ascii=False)) > 4096:
        raise ValueError('扩展参数必须是有限 JSON 对象')
    selectors = contract['input_selectors']
    if len(selectors) != 1:
        raise ValueError('当前 CLI 适配器只接受一个输入选择器')
    source_name = options.get('structure_source')
    if source_name not in selectors[0]['artifact_names']:
        raise ValueError('结构来源不在扩展输入选择器中')
    return options


def _source_bytes(attempt_id, source_name, max_bytes=artifacts.MAX_PULL_BYTES):
    listing = artifacts.refresh(attempt_id)['files']
    entry = next((item for item in listing if item['path'] == source_name), None)
    if not entry:
        return None, f'没有 {source_name} 结构文件'
    if entry['size_bytes'] > max_bytes:
        return None, '结构文件超过扩展输入上限'
    raw, _ = artifacts.pull(attempt_id, source_name)
    return raw, None


def _cli_request(directory, source_name, options, output_name):
    request = directory / 'request.json'
    request.write_text(json.dumps({'source_file': str(directory / source_name), 'source_name': source_name,
                                   'options': options, 'output_file': str(directory / output_name)}), encoding='utf-8')
    return request


def local_structure_symmetry(poscar, angle_tolerance_degree=5):
    """Analyze the displayed normalized geometry without an attempt or registry state."""
    if not isinstance(poscar, str) or not poscar or len(poscar.encode('utf-8')) > 256 * 1024:
        raise ValueError('当前结构几何必须非空且不超过 256 KiB')
    sdk = extension_sdk.validate('structure-symmetry')
    if not sdk['valid']:
        raise ValueError('对称性分析缺少已登记 Python 依赖')
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp).resolve()
        (directory / 'POSCAR').write_text(poscar, encoding='utf-8')
        request = _cli_request(directory, 'POSCAR',
                               {'structure_source': 'POSCAR', 'angle_tolerance_degree': angle_tolerance_degree},
                               'symmetry.json')
        result = subprocess.run([sys.executable, str(sdk['root'] / sdk['contract']['entrypoint']),
                                 'run', '--request', str(request)], cwd=directory, capture_output=True,
                                text=True, timeout=sdk['contract']['runtime']['timeout_seconds'])
        if result.returncode or not (directory / 'symmetry.json').is_file():
            raise ValueError('对称性分析失败：' + (result.stderr or result.stdout).strip()[-500:])
        payload = json.loads((directory / 'symmetry.json').read_text(encoding='utf-8'))
        if payload.get('schema') != sdk['contract']['outputs']['schema']:
            raise ValueError('对称性结果格式无效')
        return payload


def check_applicability(attempt_id, extension_id, options):
    sdk = extension_sdk.status(extension_id)
    if not sdk['enabled']:
        raise ValueError('请先安装并启用扩展')
    if sdk['contract']['adapter'] == 'cli-v1':
        parsed = _cli_options(sdk['contract'], options)
        source_name = parsed['structure_source']
        raw, missing = _source_bytes(attempt_id, source_name, sdk['contract']['input_selectors'][0]['max_bytes'])
        if missing:
            return {'applicability_state': 'NOT_APPLICABLE', 'reason': missing}
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp).resolve()
            (directory / source_name).write_bytes(raw)
            request = _cli_request(directory, source_name, parsed, sdk['contract']['outputs']['files'][0])
            result = subprocess.run([sys.executable, str(sdk['root'] / sdk['contract']['entrypoint']),
                                     'check', '--request', str(request)], cwd=directory,
                                    capture_output=True, text=True, timeout=sdk['contract']['runtime']['timeout_seconds'])
        if result.returncode:
            return {'applicability_state': 'NOT_APPLICABLE', 'reason': (result.stderr or result.stdout).strip()[-1000:],
                    'source_sha256': hashlib.sha256(raw).hexdigest()}
        payload = json.loads(result.stdout)
        if payload.get('schema') != sdk['contract']['applicability']['result_schema'] or payload.get('applicability_state') != 'APPLICABLE':
            raise ValueError('扩展适用性回执不符合 SDK 合同')
        return {**payload, 'reason': payload.get('reason', '扩展适用性检查通过'),
                'source_name': source_name, 'source_bytes': raw, 'options': parsed}
    if extension_id != 'crystal-structure-distortion-analysis':
        raise ValueError('该扩展暂不提供配置适用性检查')
    config = _distortion_options(options)
    source_name = options['structure_source']
    raw, missing = _source_bytes(attempt_id, source_name)
    if missing:
        return {'applicability_state': 'NOT_APPLICABLE', 'reason': missing}
    with tempfile.TemporaryDirectory() as temp:
        source = Path(temp) / source_name
        setting = Path(temp) / 'config.json'
        source.write_bytes(raw)
        setting.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
        result = subprocess.run([sys.executable, str(sdk['root'] / sdk['contract']['entrypoint']),
                                 str(source), '--config', str(setting), '--check-applicability'],
                                capture_output=True, text=True, timeout=120)
    if result.returncode:
        return {'applicability_state': 'NOT_APPLICABLE', 'reason': (result.stderr or result.stdout).strip()[-1000:],
                'source_sha256': hashlib.sha256(raw).hexdigest()}
    payload = json.loads(result.stdout)
    return {'applicability_state': 'APPLICABLE', 'reason': f"{payload['sites']} 个中心位点满足所选配位条件",
            'source_sha256': hashlib.sha256(raw).hexdigest(), 'formula': payload['formula'],
            'warnings': payload['warnings'], 'config': config, 'source_name': source_name, 'source_bytes': raw}


def run(attempt_id, extension_id, confirmed=False, options=None):
    eligible = next((item for item in catalog(attempt_id,listing=artifacts.refresh(attempt_id)) if item['id'] == extension_id), None)
    if not eligible or extension_id not in extension_sdk.available_ids():
        raise ValueError('该扩展未安装到本项目')
    if not eligible['ready']:
        raise ValueError('后处理不可运行：' + '；'.join(eligible['missing']))
    if eligible['status'] == 'experimental' and not confirmed:
        raise ValueError('实验性扩展需要在界面显式确认')
    applicability = None
    sdk = extension_sdk.status(extension_id)
    if extension_id.startswith('crystal-') or sdk['contract']['adapter'] == 'cli-v1':
        applicability = check_applicability(attempt_id, extension_id, options)
        if applicability['applicability_state'] != 'APPLICABLE':
            raise ValueError('后处理不适用：' + applicability['reason'])
    analysis_id = uuid.uuid4().hex
    directory = ROOT / 'analyses' / analysis_id
    directory.mkdir(parents=True, exist_ok=False)
    source_files = {}
    manifest = _manifest(extension_id)
    record = {'schema':'fpilot.analysis-record.v1','id':analysis_id,'attempt_id':attempt_id,
              'extension':manifest,'created_at':datetime.now(timezone.utc).isoformat(),'state':'RUNNING',
              'scientific_validation':'NOT_PERFORMED', 'applicability_state':
              applicability['applicability_state'] if applicability else 'NOT_CHECKED',
              'options':options if applicability else None}
    try:
        listing = artifacts.refresh(attempt_id)['files']
        names = {item['name']:item['path'] for item in listing if item['path'] == item['name']}
        if sdk['contract']['adapter'] == 'cli-v1':
            source_name = applicability['source_name']
            (directory / source_name).write_bytes(applicability['source_bytes'])
            request = _cli_request(directory.resolve(), source_name, applicability['options'], sdk['contract']['outputs']['files'][0])
            argv = [sys.executable, str(sdk['root'] / sdk['contract']['entrypoint']), 'run', '--request', str(request)]
        elif extension_id.startswith('crystal-'):
            source_name = applicability['source_name']
            raw = applicability['source_bytes']
            source = directory / source_name; source.write_bytes(raw)
            setting = directory / 'config.json'
            setting.write_text(json.dumps(applicability['config'], ensure_ascii=False, indent=2), encoding='utf-8')
            prefix = directory / 'polyhedral_distortion'
            argv = [sys.executable, str(sdk['root'] / sdk['contract']['entrypoint']), str(source),
                    '--config', str(setting), '--output-prefix', str(prefix)]
        else:
            source_name = 'POSCAR' if 'POSCAR' in names else 'CONTCAR'
            for name, remote_name in ((source_name,names[source_name]),('OUTCAR',names['OUTCAR'])):
                raw, _ = artifacts.pull(attempt_id, remote_name)
                local_name = 'POSCAR' if name == 'CONTCAR' else name
                (directory/local_name).write_bytes(raw)
                source_files[local_name] = hashlib.sha256(raw).hexdigest()
            argv = [sys.executable, str(sdk['root'] / sdk['contract']['entrypoint'])]
        for path in directory.iterdir():
            if path.is_file() and path.name != 'analysis.json': source_files.setdefault(path.name, hashlib.sha256(path.read_bytes()).hexdigest())
        result = subprocess.run(argv, cwd=directory, capture_output=True, text=True,
                                timeout=sdk['contract']['runtime']['timeout_seconds'])
        if sdk['contract']['adapter'] == 'cli-v1' and result.returncode == 0:
            expected = sdk['contract']['outputs']['files']
            if any(not (directory / name).is_file() for name in expected):
                raise ValueError('扩展返回成功但缺少合同声明的输出文件')
            payload = json.loads((directory / expected[0]).read_text(encoding='utf-8'))
            if payload.get('schema') != sdk['contract']['outputs']['schema']:
                raise ValueError('扩展输出 schema 与 SDK 合同不一致')
            receipt = json.loads(result.stdout)
            if receipt.get('schema') != sdk['contract']['outputs']['schema']:
                raise ValueError('扩展运行回执 schema 与 SDK 合同不一致')
            record['summary'] = receipt.get('summary')
        record.update(state='COMPLETED' if result.returncode == 0 else 'FAILED', returncode=result.returncode,
                      stdout=result.stdout[-131072:], stderr=result.stderr[-32768:], input_sha256=source_files,
                      outputs=sorted(path.name for path in directory.iterdir() if path.is_file() and path.name not in source_files))
    except Exception as exc:
        record.update(state='FAILED', error=str(exc)[:1000])
    return _write_record(directory, record)


def history(attempt_id):
    root = ROOT / 'analyses'
    rows = []
    for path in root.glob('*/analysis.json') if root.exists() else []:
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if value.get('attempt_id') == attempt_id and not is_recycled('local-analyses',value['id']): rows.append(value)
        except (OSError, ValueError):
            continue
    return sorted(rows, key=lambda item:item.get('created_at',''), reverse=True)


def output_file(attempt_id, analysis_id, name):
    if not re.fullmatch(r'[a-f0-9]{32}', analysis_id) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', name):
        raise ValueError('分析文件标识无效')
    directory = ROOT / 'analyses' / analysis_id
    record = json.loads((directory / 'analysis.json').read_text(encoding='utf-8'))
    if record.get('attempt_id') != attempt_id or name not in record.get('outputs', []):
        raise ValueError('该文件不属于所选分析记录')
    path = directory / name
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError('分析文件不可下载或超过 10 MiB')
    return path.read_bytes()

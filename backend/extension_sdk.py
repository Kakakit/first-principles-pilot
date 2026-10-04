"""Bundled extension contract, validation, and explicit install/enable registry.

The SDK describes local adapters only. Remote artifacts remain under the
attempt-bound pull policy in postprocessing; enabling never starts a run.
"""
import hashlib
import importlib.util
import json
import os
import re
import tempfile
from pathlib import Path

from .storage import ROOT

BUNDLES = Path(__file__).resolve().parents[1] / 'resources' / 'extensions'
SCHEMA = 'fpilot.extension-sdk.v1'
REGISTRY_SCHEMA = 'fpilot.extension-registry.v1'
ID = re.compile(r'[a-z][a-z0-9-]{1,79}')
FILE = re.compile(r'[A-Za-z0-9_.-]{1,128}')
ADAPTERS = {'legacy-distortion', 'legacy-lcalcpol', 'cli-v1'}
SIDE_EFFECTS = {'local_read', 'local_write'}
_VALIDATION_CACHE = {}


def _registry_path():
    return ROOT / 'extensions' / 'registry.json'


def _read_registry():
    path = _registry_path()
    if not path.exists():
        return {'schema': REGISTRY_SCHEMA, 'installed': {}}
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('schema') != REGISTRY_SCHEMA or not isinstance(value.get('installed'), dict):
        raise ValueError('扩展登记文件格式无效')
    return value


def _save_registry(value):
    path = _registry_path(); path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as temp:
        json.dump(value, temp, ensure_ascii=False, indent=2); temp.flush(); os.fsync(temp.fileno()); name = temp.name
    os.replace(name, path)


def available_ids():
    return sorted(path.name for path in BUNDLES.iterdir() if path.is_dir() and ID.fullmatch(path.name) and (path / 'extension.sdk.json').is_file())


def _inside(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('扩展文件路径必须位于扩展目录内')
    path = root / relative
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'扩展文件不存在或越界：{relative}')
    return path


def validate(bundle_id, refresh=False):
    if not isinstance(bundle_id, str) or not ID.fullmatch(bundle_id) or bundle_id not in available_ids():
        raise ValueError('扩展未打包到本项目')
    root = BUNDLES / bundle_id
    raw = (root / 'extension.sdk.json').read_bytes()
    if len(raw) > 64 * 1024:
        raise ValueError('SDK 合同超过 64 KiB')
    value = json.loads(raw)
    required = {'schema', 'id', 'name', 'version', 'description', 'adapter', 'input_selectors',
                'applicability', 'outputs', 'dependencies', 'runtime', 'side_effects', 'fixtures', 'entrypoint'}
    if not isinstance(value, dict) or set(value) != required or value['schema'] != SCHEMA or value['id'] != bundle_id:
        raise ValueError('SDK 合同字段或标识无效')
    if value['adapter'] not in ADAPTERS or not all(isinstance(value[k], str) and value[k].strip() for k in ('name', 'version', 'description')):
        raise ValueError('SDK 适配器或元数据无效')
    selectors = value['input_selectors']
    if not isinstance(selectors, list) or not selectors or len(selectors) > 8:
        raise ValueError('输入选择器必须为非空有限列表')
    for row in selectors:
        if not isinstance(row, dict) or set(row) != {'id', 'artifact_names', 'max_bytes'} or not isinstance(row['id'], str) or not ID.fullmatch(row['id']):
            raise ValueError('输入选择器格式无效')
        if not isinstance(row['artifact_names'], list) or not row['artifact_names'] or any(not isinstance(name, str) or not FILE.fullmatch(name) for name in row['artifact_names']):
            raise ValueError('输入文件名称无效')
        if type(row['max_bytes']) is not int or not 0 < row['max_bytes'] <= 10 * 1024 * 1024:
            raise ValueError('输入选择器超出受控回传上限')
    if len({row['id'] for row in selectors}) != len(selectors):
        raise ValueError('输入选择器标识重复')
    if not isinstance(value['applicability'], dict) or set(value['applicability']) != {'result_schema'} or not isinstance(value['applicability']['result_schema'], str):
        raise ValueError('适用性检查合同无效')
    outputs = value['outputs']
    if not isinstance(outputs, dict) or set(outputs) != {'schema', 'files'} or not isinstance(outputs['schema'], str) or not isinstance(outputs['files'], list) or any(not isinstance(name, str) or not FILE.fullmatch(name) for name in outputs['files']):
        raise ValueError('输出合同无效')
    deps = value['dependencies']
    if not isinstance(deps, dict) or set(deps) != {'python_modules'} or not isinstance(deps['python_modules'], list) or any(not isinstance(module, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]*', module) for module in deps['python_modules']):
        raise ValueError('Python 依赖声明无效')
    runtime = value['runtime']
    if not isinstance(runtime, dict) or set(runtime) != {'interpreter', 'timeout_seconds'} or runtime['interpreter'] != 'registered-python' or type(runtime['timeout_seconds']) is not int or not 1 <= runtime['timeout_seconds'] <= 600:
        raise ValueError('运行环境合同无效')
    if not isinstance(value['side_effects'], list) or not set(value['side_effects']) <= SIDE_EFFECTS or 'local_read' not in value['side_effects']:
        raise ValueError('扩展声明了未受控副作用')
    if value['adapter'] == 'cli-v1' and (len(selectors) != 1 or len(outputs['files']) != 1 or not outputs['schema'] or 'local_write' not in value['side_effects']):
        raise ValueError('CLI v1 需一个输入选择器、一个 JSON 输出及本地写入声明')
    if not isinstance(value['fixtures'], list) or not value['fixtures']:
        raise ValueError('请提供测试 fixture')
    files = [_inside(root, value['entrypoint'])]
    files.extend(_inside(root, item) for item in value['fixtures'])
    if value['adapter'].startswith('legacy-'):
        files.append(_inside(root, 'extension.json'))
        legacy = json.loads(files[-1].read_text(encoding='utf-8'))
        if legacy.get('schema') != 'fpilot.extension.v1' or legacy.get('name') != bundle_id:
            raise ValueError('旧扩展清单与 SDK 适配器不匹配')
    signature = tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in [root / 'extension.sdk.json', *files])
    cached = _VALIDATION_CACHE.get(bundle_id)
    if not refresh and cached and cached[0] == signature:
        return cached[1]
    revision = hashlib.sha256(raw + b''.join(hashlib.sha256(path.read_bytes()).digest() for path in files)).hexdigest()
    missing = [module for module in deps['python_modules'] if importlib.util.find_spec(module) is None]
    result = {'contract': value, 'revision': revision, 'valid': not missing, 'missing_dependencies': missing, 'root': root}
    _VALIDATION_CACHE[bundle_id] = (signature, result)
    return result


def status(bundle_id, refresh=False):
    receipt = validate(bundle_id, refresh=refresh)
    item = _read_registry()['installed'].get(bundle_id, {})
    installed = item.get('revision') == receipt['revision']
    return {**receipt, 'installed': installed, 'enabled': bool(installed and receipt['valid'] and item.get('enabled'))}


def install(bundle_id):
    _VALIDATION_CACHE.pop(bundle_id, None)
    receipt = validate(bundle_id)
    if not receipt['valid']:
        raise ValueError('扩展缺少运行依赖：' + ', '.join(receipt['missing_dependencies']))
    registry = _read_registry()
    registry['installed'][bundle_id] = {'revision': receipt['revision'], 'enabled': False}
    _save_registry(registry)
    return public_status(bundle_id)


def enable(bundle_id, enabled):
    if type(enabled) is not bool:
        raise ValueError('启用状态必须为布尔值')
    receipt = status(bundle_id)
    if not receipt['installed'] or not receipt['valid']:
        raise ValueError('请先安装并通过当前版本校验')
    registry = _read_registry()
    registry['installed'][bundle_id]['enabled'] = enabled
    _save_registry(registry)
    return public_status(bundle_id)


def public_status(bundle_id):
    receipt = status(bundle_id)
    return {'id': bundle_id, 'name': receipt['contract']['name'], 'description': receipt['contract']['description'],
            'adapter': receipt['contract']['adapter'], 'revision': receipt['revision'], 'valid': receipt['valid'],
            'missing_dependencies': receipt['missing_dependencies'], 'installed': receipt['installed'],
            'enabled': receipt['enabled'], 'input_selectors': receipt['contract']['input_selectors'],
            'output_schema': receipt['contract']['outputs']['schema']}

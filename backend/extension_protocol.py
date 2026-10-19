"""Versioned file protocol and recoverable package lifecycle for optional CLIs.

The host knows input roles, schemas and execution locations; it has no extension
IDs or scientific algorithms. The v1 SDK remains a historical compatibility API.
"""
import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from jsonschema import Draft7Validator
from .storage import ROOT

MAX_SOURCES = 64
BUNDLES = Path(__file__).resolve().parents[1] / 'resources/extensions'
PROTOCOL = 'fpilot.extension.v2'
REQUEST = 'fpilot.extension-request.v2'
RESULT = 'fpilot.extension-result.v2'
ID = re.compile(r'[a-z][a-z0-9-]{1,79}')
MAX_PACKAGE = 20 * 1024 * 1024
MODES = {'local', 'ssh', 'scheduler'}
_cache = {}
_doctor_cache = {}
_lock = threading.RLock()
_doctor_executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix='extension-doctor')
_doctor_pending = set()
_doctor_locks = {}


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno()); name = stream.name
    os.replace(name, path)


def registry():
    path = ROOT / 'extensions-v2/registry.json'
    return json.loads(path.read_text()) if path.exists() else {'schema':'fpilot.extension-registry.v2','installed':{}}


def _save_registry(value):
    atomic_json(ROOT / 'extensions-v2/registry.json', value)


def inside(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('文件路径必须位于独立目录内')
    target = root / relative
    if target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('文件缺失、符号链接或路径越界：' + relative)
    return target


def _local_refs(value):
    if isinstance(value, dict):
        if '$ref' in value and not str(value['$ref']).startswith('#'):
            raise ValueError('参数 schema 只接受包内定义，不下载外部引用')
        for item in value.values(): _local_refs(item)
    elif isinstance(value, list):
        for item in value: _local_refs(item)


def inspect_package(root):
    root = Path(root).resolve()
    files = sorted(path for path in root.rglob('*') if (path.is_file() or path.is_symlink()) and '__pycache__' not in path.parts and path.suffix != '.pyc')
    if not files or len(files) > 1000 or any(p.is_symlink() for p in files) or sum(p.stat().st_size for p in files) > MAX_PACKAGE:
        raise ValueError('扩展包须为普通文件，最多 1000 项、20 MiB')
    signature = tuple((str(p.relative_to(root)), p.stat().st_mtime_ns, p.stat().st_size) for p in files)
    key = str(root)
    if key in _cache and _cache[key][0] == signature: return _cache[key][1]
    manifest = json.loads(inside(root, 'extension.manifest.json').read_text())
    required = {'schema','id','name','version','description','entrypoint','contexts','stages','sources','inputs','evidence','execution','dependencies','options_schema','ui_schema','outputs','fixtures'}
    if not isinstance(manifest, dict) or not required <= set(manifest) or set(manifest)-required-{'presentation'} or manifest['schema'] != PROTOCOL or not ID.fullmatch(manifest.get('id','')):
        raise ValueError('扩展 v2 清单字段或 ID 无效')
    if any(not isinstance(manifest[k],str) or not manifest[k].strip() for k in ('name','version','description')):
        raise ValueError('名称、版本和说明不能为空')
    inside(root, manifest['entrypoint'])
    if 'presentation' in manifest:
        view=manifest['presentation']
        if not isinstance(view,dict) or set(view)!={'entrypoint'}:raise ValueError('展示配置须声明独立 entrypoint')
        inside(root,view['entrypoint'])
    if not isinstance(manifest['fixtures'],list) or not manifest['fixtures']: raise ValueError('扩展需提供 fixture')
    for name in manifest['fixtures']: inside(root,name)
    if not isinstance(manifest['contexts'],list) or not manifest['contexts'] or not set(manifest['contexts']) <= {'structure','attempt'}:
        raise ValueError('来源上下文须为结构或计算结果')
    if not isinstance(manifest['stages'],list) or any(not isinstance(s,str) for s in manifest['stages']): raise ValueError('阶段列表无效')
    bounds = manifest['sources']
    if not isinstance(bounds,dict) or set(bounds) != {'min','max'} or any(type(bounds[k]) is not int for k in bounds) or not 1 <= bounds['min'] <= bounds['max'] <= MAX_SOURCES:
        raise ValueError(f'输入来源数量须为 1–{MAX_SOURCES}')
    roles = manifest['inputs']
    if not isinstance(roles,list) or not roles or len(roles)>16: raise ValueError('输入角色须为非空有限列表')
    for role in roles:
        if not isinstance(role,dict) or not {'role','title','names','max_bytes'} <= set(role) or set(role) - {'role','title','names','max_bytes','required'} or not ID.fullmatch(role.get('role','')):
            raise ValueError('输入角色定义无效')
        if type(role.get('required',True)) is not bool: raise ValueError('输入 required 须为 boolean')
        if not isinstance(role['names'],list) or not role['names'] or any(not isinstance(n,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',n) for n in role['names']): raise ValueError('输入产物名称无效')
        if type(role['max_bytes']) is not int or not 0 < role['max_bytes'] <= 1024**4: raise ValueError('输入大小上限无效')
    if len({r['role'] for r in roles}) != len(roles): raise ValueError('输入角色重复')
    if not isinstance(manifest['evidence'],list) or any(not isinstance(s,str) for s in manifest['evidence']): raise ValueError('证据声明无效')
    execution = manifest['execution']
    if not isinstance(execution,dict) or not {'locations','timeout_seconds'} <= set(execution) or set(execution) - {'locations','timeout_seconds','preferred_location'} or not execution['locations'] or not set(execution['locations']) <= MODES or type(execution['timeout_seconds']) is not int or not 1 <= execution['timeout_seconds'] <= 86400:
        raise ValueError('运行位置或超时无效')
    if execution.get('preferred_location','local') not in MODES or ('preferred_location' in execution and execution['preferred_location'] not in execution['locations']):
        raise ValueError('优先运行位置须在支持列表中')
    deps = manifest['dependencies']
    if not isinstance(deps,list) or any(not isinstance(d,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]*',d) for d in deps): raise ValueError('依赖模块名称无效')
    schema = manifest['options_schema']; _local_refs(schema)
    try:Draft7Validator.check_schema(schema)
    except Exception as exc:raise ValueError('参数 JSON Schema 无效：'+str(exc)[:1000]) from exc
    if schema.get('type') != 'object' or not isinstance(manifest['ui_schema'],dict): raise ValueError('选项需为 object JSON Schema')
    if not isinstance(manifest['outputs'],list) or not set(manifest['outputs']) <= {'table','curve','image','file'}: raise ValueError('输出类型无效')
    revision = hashlib.sha256(b''.join(str(p.relative_to(root)).encode() + b'\0' + hashlib.sha256(p.read_bytes()).digest() for p in files)).hexdigest()
    result = {'manifest':manifest,'revision':revision,'root':root}
    _cache[key] = (signature,result)
    return result


def options_for(manifest, options):
    if not isinstance(options,dict) or len(json.dumps(options,allow_nan=False)) > 65536: raise ValueError('参数须为有限 JSON 对象')
    errors = sorted(Draft7Validator(manifest['options_schema']).iter_errors(options),key=lambda e:str(e.path))
    if errors: raise ValueError('参数不符合清单：' + '; '.join('.'.join(map(str,e.path)) + ': ' + e.message for e in errors[:8]))
    return options


def invoke(root, action, request=None, python=None, timeout=30):
    manifest = inspect_package(root)['manifest']
    argv = [python or sys.executable, str(inside(root,manifest['entrypoint'])),action]
    if request: argv += ['--request',str(request)]
    from .analysis_queue import subprocess_environment
    result = subprocess.run(argv,cwd=str(root),capture_output=True,text=True,timeout=timeout,env=subprocess_environment())
    if result.returncode: raise ValueError((result.stderr or result.stdout or '扩展非零退出')[-2000:])
    try: payload = json.loads(result.stdout)
    except ValueError: raise ValueError('扩展 CLI 须在 stdout 返回单个 JSON 回执')
    return payload


def doctor(receipt, refresh=False, python=None, background=False):
    from .extension_environments import resolve
    python=python or resolve(receipt['manifest']['id'])['python']
    try:modified=Path(python).stat().st_mtime_ns
    except OSError:modified=None
    key = (str(ROOT),receipt['revision'],python,modified)
    if not refresh and key in _doctor_cache: return _doctor_cache[key]
    if background:
        with _lock:
            if key not in _doctor_pending:
                _doctor_pending.add(key)
                def check():
                    try:doctor(receipt,python=python)
                    finally:
                        with _lock:_doctor_pending.discard(key)
                _doctor_executor.submit(check)
        return None
    with _lock:lock=_doctor_locks.setdefault(key,threading.Lock())
    with lock:
        if not refresh and key in _doctor_cache:return _doctor_cache[key]
        try:
            result = invoke(receipt['root'],'doctor',python=python,timeout=30)
            if result.get('schema') != 'fpilot.extension-doctor.v2' or type(result.get('ready')) is not bool: raise ValueError('doctor 回执格式无效')
            result = doctor_result(result,python)
        except Exception as exc: result = {'schema':'fpilot.extension-doctor.v2','ready':False,'missing':[],'python':python,'reason':str(exc)[:1500]}
        _doctor_cache[key] = result
        return result


def warm_doctors():
    """Warm local environments once, off the UI request and without remote access."""
    from .extension_environments import resolve
    for row in catalog():
        if row.get('enabled') and 'local' in row.get('execution',{}).get('locations',[]):
            try:doctor(installed(row['id']),python=resolve(row['id'])['python'],background=True)
            except (ValueError,OSError):pass


def doctor_result(result,python):
    # Older portable helpers put the Python version in the python field.
    result=dict(result)
    old=result.get('python')
    if isinstance(old,str) and re.fullmatch(r'\d+\.\d+\.\d+',old):result.setdefault('python_version',old)
    result['python']=python
    return result


def bundle_ids():
    return sorted(p.name for p in BUNDLES.iterdir() if p.is_dir() and ID.fullmatch(p.name) and (p/'extension.manifest.json').is_file())


def installed(key):
    if not isinstance(key,str) or not ID.fullmatch(key): raise ValueError('扩展 ID 无效')
    entry = registry()['installed'].get(key)
    if not entry: raise ValueError('扩展尚未安装或已卸载')
    root = ROOT / 'extensions-v2/packages' / entry['package']
    receipt = inspect_package(root)
    if receipt['manifest']['id'] != key or receipt['revision'] != entry['revision']: raise ValueError('安装包已变化，请重新导入')
    return {**receipt,'enabled':entry['enabled'],'doctor':entry['doctor']}


def public(receipt, item=None):
    return {**receipt['manifest'],'revision':receipt['revision'],'installed':bool(item),'enabled':bool(item and item['enabled']),
            'doctor':item['doctor'] if item else None}


def catalog():
    rows = {}
    for key in bundle_ids():
        try: rows[key] = public(inspect_package(BUNDLES/key))
        except Exception as exc: rows[key] = {'id':key,'name':key,'installed':False,'enabled':False,'error':str(exc)}
    for key, item in registry()['installed'].items():
        try: rows[key] = public(installed(key),item)
        except Exception as exc: rows[key] = {'id':key,'name':key,'installed':True,'enabled':False,'error':str(exc)}
    return list(rows.values())


def _recycle(item):
    old = ROOT/'extensions-v2/packages'/item['package']
    if old.exists():
        dest = ROOT/'extensions-v2/recycle'/uuid.uuid4().hex; dest.parent.mkdir(parents=True,exist_ok=True); shutil.move(str(old),str(dest))


def install_directory(path):
    receipt = inspect_package(Path(path))
    with _lock:
        key = receipt['manifest']['id']; name = key+'-'+uuid.uuid4().hex
        dest = ROOT/'extensions-v2/packages'/name; dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(receipt['root'],dest,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        copied = inspect_package(dest)
        environment = doctor(copied)
        value = registry(); old = value['installed'].get(key)
        value['installed'][key] = {'package':name,'revision':copied['revision'],'enabled':True,'doctor':environment}
        _save_registry(value)
        if old: _recycle(old)
    return public(copied,value['installed'][key])


def install_builtin(key):
    if key not in bundle_ids(): raise ValueError('没有此内置扩展')
    return install_directory(BUNDLES/key)


def import_zip(encoded):
    try: raw = base64.b64decode(encoded,validate=True)
    except Exception: raise ValueError('ZIP 编码无效')
    if len(raw)>MAX_PACKAGE: raise ValueError('ZIP 超过 20 MiB')
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries)>1000 or sum(item.file_size for item in entries)>MAX_PACKAGE: raise ValueError('解压包超过限制')
            for item in entries:
                name = Path(item.filename)
                if name.is_absolute() or '..' in name.parts or (item.external_attr >> 16) & 0o170000 == 0o120000: raise ValueError('ZIP 含越界路径或符号链接')
            archive.extractall(root)
        manifests = list(root.rglob('extension.manifest.json'))
        if len(manifests)!=1: raise ValueError('ZIP 需包含唯一扩展清单')
        return install_directory(manifests[0].parent)


def enable(key, enabled):
    if type(enabled) is not bool: raise ValueError('启用状态须为布尔值')
    receipt = installed(key)
    with _lock:
        value=registry(); value['installed'][key]['enabled']=enabled; _save_registry(value)
    return public(receipt,value['installed'][key])


def recheck(key):
    receipt=installed(key); environment=doctor(receipt,refresh=True)
    with _lock:
        value=registry();value['installed'][key]['doctor']=environment
        _save_registry(value)
    return public(receipt,value['installed'][key])


def uninstall(key):
    if not isinstance(key,str) or not ID.fullmatch(key) or key not in registry()['installed']:
        raise ValueError('扩展尚未安装')
    with _lock:
        value=registry();item=value['installed'].pop(key);_save_registry(value);_recycle(item)
    return {'id':key,'uninstalled':True,'history_preserved':True}

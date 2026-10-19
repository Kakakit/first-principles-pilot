"""Machine-local defaults and per-package Python overrides; no package coupling."""
import copy
import json
import sys
import threading
from pathlib import Path

_lock = threading.RLock()


def settings():
    from . import extension_protocol as p
    path = p.ROOT/'extensions-v2/runtime-bindings.json'
    return json.loads(path.read_text()) if path.exists() else {'local_python':None,'overrides':{}}


def _save(value):
    from . import extension_protocol as p
    p.atomic_json(p.ROOT/'extensions-v2/runtime-bindings.json',value)


def _python(value, scope):
    if not isinstance(value,str) or not value.strip() or any(c in value for c in '\n\r\0'):
        raise ValueError('请输入已有 Python 的单行绝对路径')
    if not (Path(value).is_absolute() if scope=='local' else value.startswith('/')):
        raise ValueError('请输入已有 Python 的绝对路径')
    return value


def local_default():
    saved=settings()['local_python']
    return {'python':saved or sys.executable,'inherited':not bool(saved),'scope':'local'}


def save_local(value):
    if not isinstance(value,dict) or set(value)!={'python'}:raise ValueError('本地环境字段无效')
    python=None if value['python'] is None else _python(value['python'],'local')
    with _lock:
        current=settings();current['local_python']=python;_save(current)
    return local_default()


def save_override(key,value):
    from . import extension_protocol as p, extension_remote as remote
    receipt=p.installed(key)
    if not isinstance(value,dict) or not {'scope','python'}<=set(value) or set(value)-{'scope','python','alias'}:
        raise ValueError('扩展环境字段无效')
    scope=value['scope'];alias=value.get('alias','')
    if scope not in ('local','remote') or scope=='local' and alias:raise ValueError('运行位置无效')
    modes=receipt['manifest']['execution']['locations']
    if scope=='local' and 'local' not in modes or scope=='remote' and not set(modes)&{'ssh','scheduler'}:
        raise ValueError('清单不支持此运行位置')
    if scope=='remote' and alias not in remote.runtimes():raise ValueError('请先登记服务器默认环境')
    python=None if value['python'] is None else _python(value['python'],scope)
    with _lock:
        current=settings();binding=current['overrides'].setdefault(key,{})
        target=binding if scope=='local' else binding.setdefault('remote',{})
        field='local' if scope=='local' else alias
        if python is None:target.pop(field,None)
        else:target[field]=python
        if not binding.get('remote'):binding.pop('remote',None)
        if not binding:current['overrides'].pop(key,None)
        _save(current)
    return resolve(key,'local' if scope=='local' else 'ssh',alias)


def resolve(key,location='local',alias=''):
    from . import extension_remote as remote
    if location not in ('local','ssh','scheduler'):raise ValueError('运行位置无效')
    binding=settings()['overrides'].get(key,{})
    if location=='local':
        result=local_default();override=binding.get('local')
    else:
        result=copy.deepcopy(remote.runtimes().get(alias))
        if not result:raise ValueError('请先保存来源服务器的已有 Python 默认环境')
        result['scope']='remote';override=binding.get('remote',{}).get(alias)
    if override:result['python']=override
    result['inherited']=not bool(override)
    return result


def expected(runtime,payload):
    if 'runtime_python' in payload and payload['runtime_python']!=runtime['python']:
        raise ValueError('Python 环境已变化，请重新检查或预览')


def check(key,target=None):
    from . import extension_protocol as p, extension_remote as remote
    receipt=p.installed(key);target=target or {}
    if not isinstance(target,dict) or set(target)-{'location','alias'}:raise ValueError('环境检查目标无效')
    location=target.get('location','local')
    if target and location not in receipt['manifest']['execution']['locations']:raise ValueError('清单不支持此运行位置')
    runtime=resolve(key,location,target.get('alias',''))
    result=p.doctor(receipt,refresh=True,python=runtime['python']) if location=='local' else remote.doctor(receipt,runtime,refresh=True)
    return {'runtime':runtime,'doctor':result}

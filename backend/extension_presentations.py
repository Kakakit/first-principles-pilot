"""Optional, package-owned result views. No scientific formats or package IDs."""
import io
import json
import os
import shlex
import shutil
import subprocess
import tarfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from . import extension_protocol as p, execution as ex, remote_paths, artifacts
from .platform_runtime import exclusive_lock,process_alive

_executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='extension-view')
_pending=set();_lock=threading.RLock()


def root(key):
    from .extension_runs import directory
    return directory(key)/'presentations'


def current(key):
    path=root(key)/'current.json'
    return json.loads(path.read_text()) if path.is_file() else None


def describe(record):
    view=current(record['id']);supported=bool((record.get('manifest') or {}).get('presentation'))
    if not supported and record['state']=='COMPLETED':
        try:
            receipt=p.installed(record['extension_id']);supported=bool(receipt['enabled'] and receipt['manifest'].get('presentation'))
        except (ValueError,OSError,KeyError):pass
    return {'presentation':view,'presentation_supported':supported}


def package_for(record,frozen):
    from .extension_runs import directory
    if frozen:receipt=p.inspect_package(directory(record['id'])/'package')
    else:
        receipt=p.installed(record['extension_id'])
        if not receipt['enabled']:raise ValueError('扩展已停用；已有展示与数据仍可查看')
    if not receipt['manifest'].get('presentation'):raise ValueError('扩展未声明独立结果展示入口')
    return receipt


def schedule(key,frozen=False):
    from . import extension_runs as runs
    record=runs.get(key)
    if record['state']!='COMPLETED':raise ValueError('请先核对原分析结束状态，再更新展示')
    receipt=package_for(record,frozen)
    root(key).mkdir(parents=True,exist_ok=True)
    with exclusive_lock(root(key)/'schedule.lock') as owned:
        previous=current(key)
        if not owned:return previous
        if previous and previous.get('status')=='GENERATING' and process_alive(previous.get('owner_pid')):return previous
        with _lock:
            if key in _pending:return previous
            _pending.add(key)
        version=uuid.uuid4().hex;dest=root(key)/version;dest.mkdir()
        view={'id':version,'status':'GENERATING','version':receipt['manifest']['version'],
              'created_at':runs.now(),'owner_pid':os.getpid(),'python':(record.get('runtime') or {}).get('python') or __import__('sys').executable}
        if previous and previous.get('status')=='READY':
            view['previous_id']=previous['id']
            old_state=state(key,previous['id'])
            if old_state['revision']:p.atomic_json(dest/'state.json',old_state)
        p.atomic_json(dest/'view.json',view);p.atomic_json(root(key)/'current.json',view)
    try:
        shutil.copytree(receipt['root'],dest/'package',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        def work():
            try:generate(record,view,dest,receipt['manifest']['presentation']['entrypoint'])
            except Exception as exc:
                view.update(status='FAILED',error=str(exc)[-2000:])
                p.atomic_json(dest/'view.json',view);p.atomic_json(root(key)/'current.json',view)
            finally:
                with _lock:_pending.discard(key)
        _executor.submit(work)
        return view
    except Exception as exc:
        view.update(status='FAILED',error=str(exc)[-2000:])
        p.atomic_json(dest/'view.json',view);p.atomic_json(root(key)/'current.json',view)
        with _lock:_pending.discard(key)
        raise


def validate(manifest,dest,sizes=None):
    if not isinstance(manifest,dict) or manifest.get('schema')!='fpilot.extension-presentation.v1':raise ValueError('展示回执协议无效')
    assets=manifest.get('assets')
    if not isinstance(assets,list) or not 1<=len(assets)<=1000:raise ValueError('展示文件清单无效')
    names=set()
    for item in assets:
        if not isinstance(item,dict) or set(item)!={'path','media_type'} or not isinstance(item['media_type'],str):raise ValueError('展示产物定义无效')
        name=item['path']
        if not isinstance(name,str) or not name or Path(name).is_absolute() or '..' in Path(name).parts or name in names:raise ValueError('展示文件越界或重复')
        names.add(name)
        size=sizes[name] if sizes is not None else p.inside(dest,name).stat().st_size
        if not 0<=size<=artifacts.MAX_PULL_BYTES:raise ValueError('展示单文件超过 10 MiB；请由扩展分片')
    if manifest.get('entry_html') not in names or not manifest['entry_html'].endswith('.html'):raise ValueError('展示缺少 HTML 入口')
    return manifest


def generate(record,view,dest,entry):
    from .extension_runs import directory,now
    output=dest/'outputs';output.mkdir()
    request={'schema':'fpilot.extension-presentation-request.v1','analysis_id':record['id'],
             'source_dir':str(directory(record['id'])/'outputs'),'output_dir':str(output)}
    if record['location']=='local':
        p.atomic_json(dest/'request.json',request)
        response=subprocess.run([view['python'],str(p.inside(dest/'package',entry)),'render','--request',str(dest/'request.json')],
            cwd=dest,capture_output=True,text=True,timeout=180,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
        if response.returncode:raise ValueError((response.stderr or response.stdout)[-2000:])
        manifest=validate(json.loads(response.stdout),output)
    else:
        manifest=remote_generate(record,view,dest,entry,request)
    view.update(status='READY',manifest=manifest,updated_at=now())
    p.atomic_json(dest/'view.json',view);p.atomic_json(root(record['id'])/'current.json',view)


def remote_generate(record,view,dest,entry,request):
    remote_dir=record['remote_dir'].rstrip('/')+'/presentations/'+view['id']
    request.update(source_dir=record['remote_dir'].rstrip('/')+'/outputs',output_dir=remote_dir+'/outputs')
    p.atomic_json(dest/'request.json',request)
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w:gz') as archive:
        archive.add(dest/'package',arcname='package',filter=lambda item:None if '__pycache__' in item.name else item)
        archive.add(dest/'request.json',arcname='request.json')
    command='set -e\ndest='+remote_paths.shell_path(remote_dir)+'\ntest ! -e "$dest"\nmkdir -p "$dest/outputs"\ntar -xzf - -C "$dest"\ncd "$dest"\nexport PYTHONDONTWRITEBYTECODE=1\n'+shlex.quote(view['python'])+' '+shlex.quote('package/'+entry)+' render --request request.json'
    response=ex.ssh(record['runtime']['alias'],command,stream.getvalue(),timeout=180,mutating=True)
    if response.get('exit_code')!=0:raise ValueError('服务器展示生成未完成：'+response.get('stderr','')[-1500:]+'；原计算不会重跑')
    manifest=json.loads(response['stdout'])
    assets=manifest.get('assets') or []
    if not isinstance(assets,list) or len(assets)>1000:raise ValueError('展示清单无效')
    names=[item['path'] for item in assets]
    code="import json,pathlib,sys; root=pathlib.Path('.').resolve(); out={}\nfor name in json.loads(sys.argv[1]):\n p=root/name\n if not name or pathlib.Path(name).is_absolute() or '..' in pathlib.Path(name).parts or p.is_symlink() or not p.is_file() or root not in p.resolve().parents: raise ValueError('invalid view asset')\n out[name]=p.stat().st_size\nprint(json.dumps(out))"
    response=ex.ssh(record['runtime']['alias'],'set -e\ncd '+remote_paths.shell_path(remote_dir+'/outputs')+'\n'+shlex.quote(view['python'])+' -c '+shlex.quote(code)+' '+shlex.quote(json.dumps(names)),timeout=30)
    if response.get('exit_code')!=0:raise ValueError('无法读取展示产物大小')
    sizes=json.loads(response['stdout']);validate(manifest,dest/'outputs',sizes)
    from .safe_files import pull_many
    pull_many(record['runtime']['alias'],[{'remote_path':remote_dir+'/outputs/'+name,'destination':str(dest/'outputs'/name)} for name in names],timeout=180)
    validate(manifest,dest/'outputs')
    view['remote_dir']=remote_dir
    return manifest


def selected(key,version=None):
    view=current(key) if version is None else json.loads(p.inside(root(key),version+'/view.json').read_text())
    if not view:raise ValueError('尚未生成结果展示')
    if view['status']!='READY':raise ValueError('展示尚未就绪，原结果与下载仍可使用')
    return view


def asset(key,version,name):
    view=selected(key,version)
    item=next((item for item in view['manifest']['assets'] if item['path']==name),None)
    if not item:raise ValueError('文件未在展示清单中声明')
    return p.inside(root(key)/view['id']/'outputs',name),item['media_type']


def state(key,version):
    selected(key,version);path=root(key)/version/'state.json'
    return json.loads(path.read_text()) if path.exists() else {'revision':0,'value':{}}


def save_state(key,version,value):
    from .extension_runs import now
    selected(key,version)
    if not isinstance(value,dict) or len(json.dumps(value,allow_nan=False).encode())>65536:raise ValueError('展示设置须为 64 KiB 内的有限 JSON 对象')
    dest=root(key)/version
    with exclusive_lock(dest/'state.lock') as owned:
        if not owned:raise ValueError('正在保存展示设置，请稍后重试')
        old=state(key,version);revision=old['revision']+1
        updated={'revision':revision,'saved_at':now(),'value':value}
        p.atomic_json(dest/'states'/(str(revision)+'.json'),updated)
        p.atomic_json(dest/'state.json',updated)
    return updated

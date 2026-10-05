"""Generic source resolver, asynchronous analyses and immutable run snapshots."""
import copy
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from jsonschema import Draft7Validator
from . import artifacts, execution as ex, extension_protocol as p, extension_remote as remote
from .recycle import is_recycled, inactive, assert_available
from .storage import ROOT
from .platform_runtime import exclusive_lock, process_alive, stop_child

TERMINAL={'COMPLETED','FAILED','NOT_APPLICABLE','UNKNOWN'}
ACTIVE_REMOTE={'DISPATCHING','SUBMITTED','UNKNOWN'}
_executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='extension')
_local_processes={};_process_lock=threading.RLock()
_threads=set();_thread_lock=threading.RLock()
RESULT_SCHEMA=json.loads((Path(__file__).resolve().parents[1]/'resources/extension-protocol/result.schema.json').read_text())


def now():return datetime.now(timezone.utc).isoformat()


def directory(key):
    if not isinstance(key,str) or not __import__('re').fullmatch(r'[a-f0-9]{32}',key):raise ValueError('analysis ID 无效')
    return ROOT/'analyses-v2'/key


def get(key):
    return json.loads((directory(key)/'analysis.json').read_text())


def save(record):
    if inactive('analyses',record['id'],record):return record
    record['updated_at']=now();p.atomic_json(directory(record['id'])/'analysis.json',record);return record


def history(project_id=None,attempt_id=None):
    base=ROOT/'analyses-v2';rows=[]
    for path in base.glob('*/analysis.json') if base.exists() else []:
        try:
            record=json.loads(path.read_text())
            if project_id and record['project_id']!=project_id:continue
            if attempt_id and not any(s.get('attempt_id')==attempt_id for s in record['sources']):continue
            if not inactive('analyses',record['id'],record):
                rows.append(record)
        except (OSError,ValueError,KeyError):continue
    return sorted(rows,key=lambda r:r['created_at'],reverse=True)


def active_usage():
    return [{'id':r['id'],'config':r['runtime']['config'],'profile':{'ppn':r.get('requested_cores')},'state':r['state']} for r in history()
            if r['location']=='scheduler' and r['state'] in ACTIVE_REMOTE]


@contextmanager
def lock(key):
    with exclusive_lock(directory(key)/'run.lock') as owned:yield owned


def _stage(record):
    if record.get('kind')=='wannier90':
        # Only this attempt's frozen bundle/snapshot supplies analysis lineage.
        snapshot=record.get('source_snapshot') or {};bundle={}
        key=record.get('bundle_id')
        if isinstance(key,str) and __import__('re').fullmatch(r'[a-f0-9]{64}',key):
            try:bundle=json.loads((ex.ROOT/'wannier-bundles'/(key+'.json')).read_text())
            except (OSError,ValueError):pass
        parameters=bundle.get('parameters') or snapshot.get('parameters') or {}
        frozen_win=bundle.get('win') or snapshot.get('win') or ''
        count=re.search(r'^\s*num_wann\s*[:=]\s*(\d+)',frozen_win,re.M|re.I)
        num_wann=parameters.get('num_wann') if parameters.get('num_wann') is not None else int(count.group(1)) if count else None
        model={'bundle_id':key,'seed':record.get('seed') or bundle.get('seed') or snapshot.get('seed'),
               'parent_attempt_id':record.get('parent_attempt_id') or bundle.get('parent_attempt_id') or snapshot.get('source_attempt_id'),
               'spinors':bundle.get('spinors',snapshot.get('spinors')),
               'num_wann':num_wann,
               'energy_reference':copy.deepcopy(bundle.get('energy_reference') or record.get('live_energy_reference') or {})}
        return 'wannier90',model
    if record.get('stage'):return record['stage'],{}
    key=record.get('bundle_id')
    if key:
        try:
            with zipfile.ZipFile(ex.ROOT/'inputs'/(key+'.zip')) as archive:
                manifest=json.loads(archive.read('manifest.json'))
                incar=record.get('final_incar') or archive.read('INCAR').decode()
                return manifest['stage'],{'bundle_id':key,'incar':incar,'files':manifest.get('files',{})}
        except (OSError,KeyError,ValueError):pass
    return record.get('stage_slug','unknown'),{}


def describe_sources(raw,project_id=None):
    if not isinstance(raw,list) or not 1<=len(raw)<=8:raise ValueError('请选择 1–8 个来源')
    rows=[]
    for value in raw:
        if not isinstance(value,dict):raise ValueError('来源描述无效')
        if value.get('kind')=='structure':
            poscar=value.get('poscar')
            if not isinstance(poscar,str) or not poscar or len(poscar.encode())>256*1024:raise ValueError('结构草稿须非空且不超过 256 KiB')
            sha=hashlib.sha256(poscar.encode()).hexdigest()
            rows.append({'id':'geometry-'+sha[:16],'kind':'structure','stage':'structure','name':value.get('name') or '当前结构',
                         'sha256':sha,'original_sha256':value.get('original_sha256'),'files':[{'name':'POSCAR','path':'POSCAR','size_bytes':len(poscar.encode())}],
                         'poscar':poscar,'chosen_files':value.get('files') or {},'evidence':{},'state':'DRAFT'})
        elif value.get('kind')=='attempt':
            record=ex.get(value.get('attempt_id'))
            if record.get('recycled') or record.get('path_schema')!='fpilot.remote-attempt-path':raise ValueError('来源已回收或路径不受支持')
            if project_id and record.get('project_id')!=project_id:raise ValueError('来源不属于当前项目')
            stage,model=_stage(record);listing=artifacts.listing(record['id'])
            rows.append({'id':record['id'],'kind':'attempt','attempt_id':record['id'],'node_id':record.get('node_id'),
                         'stage':stage,'name':record.get('job_id') or record['id'][:12],'alias':record['config']['alias'],
                         'state':record['state'],'evidence':record.get('evidence') or {},'input_summary':model,
                         'remote_root':ex.attempt_path(record)['shell'],'files':[f for f in listing['files'] if f['path']==f['name']],
                         'chosen_files':value.get('files') or {}})
        else:raise ValueError('来源必须为结构草稿或明确 attempt')
    if len({r['id'] for r in rows})!=len(rows):raise ValueError('来源重复，请选择不同结果')
    return rows


def selected_input(role,source):
    """Absent optional roles stay absent; explicit invalid names never fall back."""
    files={f['name']:f for f in source['files']};chosen=source.get('chosen_files') or {}
    if role['role'] in chosen:
        name=chosen[role['role']]
        if name=='' and not role.get('required',True):return None
        if name not in role['names'] or name not in files:raise ValueError('输入文件选择无效：'+role['title'])
        return files[name]
    return next((files[n] for n in role['names'] if n in files),None)


def blockers(manifest,sources,location='local'):
    reasons=[];bounds=manifest['sources']
    if not bounds['min']<=len(sources)<=bounds['max']:reasons.append(f"需要 {bounds['min']}–{bounds['max']} 个来源")
    if location not in manifest['execution']['locations']:reasons.append('清单不支持此运行位置')
    for source in sources:
        if source['kind'] not in manifest['contexts']:reasons.append(source['name']+'：来源类型不适用')
        if manifest['stages'] and source['stage'] not in manifest['stages']:reasons.append(source['name']+'：计算阶段不适用')
        for required in manifest['evidence']:
            if not (source['state']=='COMPLETED' if required=='COMPLETED' else source['evidence'].get(required) is True):
                reasons.append(source['name']+'：缺少 '+required+' 证据')
        for role in manifest['inputs']:
            try:candidate=selected_input(role,source)
            except ValueError as exc:reasons.append(source['name']+'：'+str(exc));continue
            if candidate is None and not role.get('required',True):continue
            if not candidate or not 0<candidate['size_bytes']<=role['max_bytes']:
                reasons.append(source['name']+'：缺少有效 '+role['title'])
            elif location=='local' and candidate['size_bytes']>artifacts.MAX_PULL_BYTES:reasons.append(source['name']+'：'+candidate['name']+' 超过 10 MiB，请在远端运行')
    return reasons


def context(payload):
    sources=describe_sources(payload['sources'],payload.get('project_id'))
    rows=[]
    for item in p.catalog():
        if not item.get('enabled'):continue
        # Stage/role filtering comes only from the manifest, never an ID branch.
        if any(s['kind'] not in item['contexts'] or item['stages'] and s['stage'] not in item['stages'] for s in sources):continue
        reasons=blockers(item,sources,payload.get('location','local'))
        if payload.get('location','local')=='local' and not (item.get('doctor') or {}).get('ready'):
            reasons.append('本机缺依赖：'+', '.join((item.get('doctor') or {}).get('missing') or ['请重新检查环境']))
        rows.append({**item,'blockers':reasons})
    return {'extensions':rows,'sources':[{k:v for k,v in s.items() if k!='poscar'} for s in sources]}


def _input_request(record,dest):
    inputs={r['role']:[] for r in record['manifest']['inputs']}
    for index,source in enumerate(record['sources']):
        for role in record['manifest']['inputs']:
            fact=selected_input(role,source)
            if fact is None:
                if role.get('required',True):raise ValueError('缺少 '+role['title'])
                continue
            name=fact['name'];target=dest/'inputs'/(str(index)+'-'+role['role']+'-'+name);target.parent.mkdir(parents=True,exist_ok=True)
            item={'name':name,'source_id':source['id'],'path':str(target),'size_bytes':fact['size_bytes']}
            if source['kind']=='structure':target.write_text(source['poscar'])
            elif record['location']=='local':
                raw=remote.source_pull(source,name,target)
                if len(raw)!=fact['size_bytes']:raise ValueError('来源大小已变化，请重新选择后运行')
                target.write_bytes(raw);item['sha256']=hashlib.sha256(raw).hexdigest()
            else:
                item['remote_path']=source['remote_root'].rstrip('/')+'/'+name
                item['mtime_epoch']=int(datetime.fromisoformat(fact['modified_at']).timestamp())
            inputs[role['role']].append(item)
    sources=[{k:v for k,v in s.items() if k not in ('poscar','chosen_files')} for s in record['sources']]
    return {'schema':p.REQUEST,'analysis_id':record['id'],'inputs':{k:v for k,v in inputs.items() if v},'sources':sources,'options':record['options'],
            'output_dir':str(dest/'outputs'),'result_file':str(dest/'outputs/result.json')}


def start(payload):
    receipt=p.installed(payload['extension_id']);manifest=receipt['manifest']
    if not receipt['enabled']:raise ValueError('扩展已停用')
    options=p.options_for(manifest,payload.get('options') or {})
    location=payload.get('location','local');sources=describe_sources(payload['sources'],payload.get('project_id'))
    reasons=blockers(manifest,sources,location)
    if reasons:raise ValueError('；'.join(reasons))
    runtime=None
    if location!='local':
        runtime=remote.runtimes().get(payload.get('alias'))
        if not runtime:raise ValueError('请先在扩展管理保存此主机的已有 Python 运行时')
        approval=json.loads((ROOT/'analysis-previews'/(payload.get('preview_id','')+'.json')).read_text()) if __import__('re').fullmatch(r'[a-f0-9]{32}',payload.get('preview_id','')) else None
        binding={'revision':receipt['revision'],'sources':sources,'options':options,'location':location,'runtime':runtime}
        if not approval or approval['binding']!=binding or (datetime.now(timezone.utc)-datetime.fromisoformat(approval['created_at'])).total_seconds()>600:raise ValueError('请重新预览当前来源、选项和远端运行脚本')
        runtime=copy.deepcopy(runtime)
        if location=='scheduler':
            runtime['cores']=approval['resources']['selection']['requested_cores']
            runtime['reason']=runtime['reason'] or '沿用用户已审阅脚本的核数，不扩大资源'
        if any(s.get('alias') and s['alias']!=runtime['alias'] for s in sources):raise ValueError('远端来源需与运行主机相同')
    if location=='local' and not p.doctor(receipt)['ready']:raise ValueError('本地环境未满足，请检查依赖')
    key=uuid.uuid4().hex;dest=directory(key);dest.mkdir(parents=True)
    shutil.copytree(receipt['root'],dest/'package',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    record={'schema':'fpilot.analysis.v2','id':key,'project_id':payload.get('project_id') or 'geometry',
            'extension_id':manifest['id'],'manifest':manifest,'revision':receipt['revision'],'sources':sources,'options':options,
            'location':location,'runtime':runtime,'state':'QUEUED','created_at':now(),'result':None,'outputs':[],
            'scientific_validation':'NOT_PERFORMED','verification':'小型本地输入内容身份；远端输入来源与大小/修改时间核验'}
    save(record);schedule(key)
    return {'analysis_id':key,'state':'QUEUED'}


def schedule(key):
    with _thread_lock:
        if key in _threads:return
        _threads.add(key)
    def task():
        try:_execute(key)
        finally:
            with _thread_lock:_threads.discard(key)
    _executor.submit(task)


def _complete(record):
    assert_available('analyses',record['id'],record)
    dest=directory(record['id']);path=dest/'outputs/result.json'
    if not path.is_file() or path.is_symlink() or path.stat().st_size>artifacts.MAX_PULL_BYTES:raise ValueError('缺少有界结果 JSON')
    result=json.loads(path.read_text());errors=list(Draft7Validator(RESULT_SCHEMA).iter_errors(result))
    if errors:raise ValueError('结果协议无效：'+errors[0].message)
    json.dumps(result,allow_nan=False)
    request=json.loads((dest/'request.json').read_text())
    result['sources']=request['sources'] # Host-frozen lineage is authoritative.
    outputs=[]
    for item in result['artifacts']:
        if record.get('remote_outputs') is not None:
            info=record['remote_outputs'].get(item['path'])
            if not info:raise ValueError('远端产物未记录')
            size=info['size_bytes']
            if size<=artifacts.MAX_PULL_BYTES:p.inside(dest/'outputs',item['path'])
        else:size=p.inside(dest/'outputs',item['path']).stat().st_size
        outputs.append({**item,'size_bytes':size,'available':size<=artifacts.MAX_PULL_BYTES})
    # Downloads and the on-screen record must carry the same frozen lineage.
    p.atomic_json(path,result)
    record.update(state=result['status'],result=result,outputs=outputs,scientific_validation=result['scientific_validation'])
    save(record)


def check(payload):
    receipt=p.installed(payload['extension_id']);manifest=receipt['manifest']
    if not receipt['enabled']:raise ValueError('扩展已停用')
    options=p.options_for(manifest,payload.get('options') or {})
    sources=describe_sources(payload['sources'],payload.get('project_id'))
    reasons=blockers(manifest,sources,payload.get('location','local'))
    if reasons:return {'status':'NOT_APPLICABLE','reason':'；'.join(reasons)}
    if payload.get('location','local')!='local':return {'status':'PENDING_REMOTE','reason':'文件清单适用；远端环境和内容检查将在独立分析目录执行'}
    with tempfile.TemporaryDirectory(prefix='fpilot-analysis-check-') as temporary:
        dest=Path(temporary);record={'id':'check','manifest':manifest,'sources':sources,'options':options,'location':'local'}
        request=_input_request(record,dest);p.atomic_json(dest/'request.json',request)
        return _check_result(p.invoke(receipt['root'],'check',dest/'request.json',timeout=min(manifest['execution']['timeout_seconds'],60)))


def _check_result(value):
    if value.get('schema')!='fpilot.extension-check.v2' or value.get('status') not in ('APPLICABLE','NOT_APPLICABLE') or not isinstance(value.get('reason'),str):
        raise ValueError('适用性回执格式无效')
    return value


def _local(record):
    assert_available('analyses',record['id'],record)
    dest=directory(record['id']);record.update(state='RUNNING',owner_pid=os.getpid());save(record)
    request=_input_request(record,dest);p.atomic_json(dest/'request.json',request)
    checked=_check_result(p.invoke(dest/'package','check',dest/'request.json',timeout=min(record['manifest']['execution']['timeout_seconds'],60)))
    assert_available('analyses',record['id'],record)
    record['check']=checked;save(record)
    if checked['status']!='APPLICABLE':record.update(state='NOT_APPLICABLE',error=checked['reason']);save(record);return
    with (dest/'stdout.log').open('w') as stdout,(dest/'stderr.log').open('w') as stderr:
        process=subprocess.Popen([sys.executable,str(p.inside(dest/'package',record['manifest']['entrypoint'])),'run','--request',str(dest/'request.json')],cwd=dest,stdout=stdout,stderr=stderr,start_new_session=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
        with _process_lock:_local_processes[record['id']]=process
        try:
            if inactive('analyses',record['id'],record):stop_local(record['id'])
            code=process.wait(timeout=record['manifest']['execution']['timeout_seconds'])
        except subprocess.TimeoutExpired:
            stop_child(process,force=True);process.wait();raise ValueError('分析超时，独立子进程已停止')
        finally:
            with _process_lock:_local_processes.pop(record['id'],None)
    record['exit_code']=code
    if code:raise ValueError('扩展退出码 '+str(code)+'：'+(dest/'stderr.log').read_text(errors='replace')[-2000:])
    _complete(record)


def _collect(record):
    dest=directory(record['id']);path=dest/'outputs/result.json'
    if not path.exists():remote.pull(record,'outputs/result.json',path)
    if path.stat().st_size>artifacts.MAX_PULL_BYTES:raise ValueError('结果 JSON 超过回传上限')
    result=json.loads(path.read_text());errors=list(Draft7Validator(RESULT_SCHEMA).iter_errors(result))
    if errors:raise ValueError('远端结果协议无效：'+errors[0].message)
    info=remote.output_info(record,[a['path'] for a in result['artifacts']]);record['remote_outputs']=info
    for name,fact in info.items():
        if fact['size_bytes']<=artifacts.MAX_PULL_BYTES:
            file=dest/'outputs'/name
            if not file.exists():remote.pull(record,'outputs/'+name,file)
            if file.stat().st_size!=fact['size_bytes']:raise ValueError('回传产物大小不一致')
    _complete(record)


def _refresh(record):
    receipt=remote.inspect(record)
    if receipt.get('job_id'):
        job=receipt['job_id']
        if not __import__('re').fullmatch(r'\d+(?:\.[A-Za-z0-9_.-]+)?',job) or record.get('job_id') and record['job_id']!=job:raise ValueError('分析 job ID 回执不一致')
        record['job_id']=job
    if receipt.get('exit') is None:
        if record['location']=='scheduler' and remote.queued(record):record['state']='SUBMITTED';record.pop('error',None)
        else:record.update(state='UNKNOWN',error='尚无程序结束回执；保留来源和 job ID，只核对，不重投')
        return save(record)
    record['exit_code']=receipt['exit']['exit_code'];record['check']=receipt.get('check')
    if record['exit_code']==3:
        record.update(state='NOT_APPLICABLE',error=(record.get('check') or {}).get('reason','远端输入不适用'));save(record)
    elif record['exit_code']==4:
        doctor=receipt.get('doctor') or {}
        record.update(state='FAILED',runtime_doctor=doctor,error='远端分析环境未满足：'+', '.join(doctor.get('missing') or [doctor.get('reason') or '请查看 doctor.json']))
        save(record)
    elif record['exit_code']:
        record.update(state='FAILED',error='远端分析非零退出：'+str(record['exit_code'])+'；查看独立目录中的 doctor.err/check.err/stderr.log');save(record)
    else:_collect(record)
    return record


def _remote(record):
    assert_available('analyses',record['id'],record)
    from . import execution_control as control
    dest=directory(record['id']);cores=None
    if record['location']=='scheduler':
        preview=remote.resource_preview(record['runtime']);record['resource_preview']=preview
        if not preview['ready']:record.update(state='WAIT_RESOURCE',error=preview['reason']);save(record);return
        cores=preview['selection']['requested_cores']
        record['requested_cores']=cores
        # Use the calculation dispatch transaction as the shared quota lock.
        with closing(ex.db()) as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:control.enforce_limits(connection,{'id':record['id'],'config':record['runtime']['config'],'profile':{'ppn':cores}})
            except ValueError as exc:
                record.update(state='WAIT_RESOURCE',error=str(exc));save(record);connection.commit();return
            record.update(state='DISPATCHING',dispatch_started_at=now());save(record);connection.commit()
    else:record.update(state='DISPATCHING',dispatch_started_at=now());save(record)
    request=_input_request(record,dest);p.atomic_json(dest/'request.json',request)
    text=remote.script(record['runtime'],record['runtime']['config'].get('remote_workspace_root','$HOME/230269265lht').rstrip('/')+'/analyses/'+record['id'],record['manifest']['entrypoint'],record['location'],cores,record['manifest']['execution']['timeout_seconds'])
    (dest/'launch.sh').write_text(text);record['script']=text
    assert_available('analyses',record['id'],record)
    record['remote_dir']=remote.stage(record,dest,request);save(record)
    # A crash from here must never resubmit, even if job.id hasn't arrived yet.
    record['submission_started']=True;save(record)
    assert_available('analyses',record['id'],record)
    if record['location']=='scheduler':
        record['job_id']=remote.submit(record);record.update(state='SUBMITTED');save(record)
    else:
        response=remote.direct(record);record['dispatch_receipt']=response;save(record);_refresh(record)


def _execute(key):
    if inactive('analyses',key):return
    with lock(key) as owned:
        if not owned:return
        record=get(key)
        if inactive('analyses',key,record) or record['state'] not in ('QUEUED','WAIT_RESOURCE'):return
        try:
            if record['location']=='local':_local(record)
            else:_remote(record)
        except Exception as exc:
            # A submission timeout is uncertainty, not a reason to resubmit.
            uncertain=record.get('submission_started') and record.get('exit_code') is None
            record.update(state='UNKNOWN' if uncertain else 'FAILED',error=str(exc)[-2500:]);save(record)


def refresh(key):
    with lock(key) as owned:
        record=get(key)
        if not owned:return record
        if record['location']=='local' or record['state'] not in ('DISPATCHING','SUBMITTED','UNKNOWN'):return record
        if not record.get('remote_dir'):
            record.update(state='UNKNOWN',error='准备期间中断，请检查独立目录；未自动重投');return save(record)
        try:return _refresh(record)
        except Exception as exc:record.update(state='FAILED' if record.get('exit_code') is not None else 'UNKNOWN',error=str(exc)[-2500:]);return save(record)


def tick():
    count=0
    for record in history():
        if record['state'] in ('QUEUED','WAIT_RESOURCE'):schedule(record['id']);count+=1
        elif record['location']!='local' and record['state'] in ('DISPATCHING','SUBMITTED','UNKNOWN'):
            refresh(record['id']);count+=1
        elif record['location']=='local' and record['state']=='RUNNING':
            if not process_alive(record.get('owner_pid')):
                record.update(state='UNKNOWN',error='本地分析进程已中断，历史输入保留');save(record)
    return {'checked':count}


def output(key,name):
    record=get(key);dest=directory(key)
    if name=='analysis.json':return dest/'analysis.json','application/json'
    if name in ('stdout.log','stderr.log','launch.sh'):
        return p.inside(dest,name),'text/plain'
    if name=='result.json':return p.inside(dest/'outputs',name),'application/json'
    item=next((a for a in record['outputs'] if a['path']==name),None)
    if not item or not item['available']:raise ValueError('产物留在远端或超过 10 MiB；请在服务器查看')
    return p.inside(dest/'outputs',name),item['media_type']


def preview(payload):
    receipt=p.installed(payload['extension_id']);manifest=receipt['manifest']
    if not receipt['enabled']:raise ValueError('扩展已停用')
    sources=describe_sources(payload['sources'],payload.get('project_id'));options=p.options_for(manifest,payload.get('options') or {})
    location=payload.get('location','local');reasons=blockers(manifest,sources,location)
    if reasons:raise ValueError('；'.join(reasons))
    if location=='local':return {'ready':True,'reason':'使用本机登记解释器，在独立目录运行'}
    runtime=remote.runtimes().get(payload.get('alias'))
    if not runtime:raise ValueError('请先保存远端已有 Python 运行时')
    if any(s.get('alias') and s['alias']!=runtime['alias'] for s in sources):raise ValueError('远端来源需与运行主机相同')
    resources=remote.resource_preview(runtime) if location=='scheduler' else {'ready':True}
    if not resources['ready']:return resources
    key=uuid.uuid4().hex;binding={'revision':receipt['revision'],'sources':sources,'options':options,'location':location,'runtime':runtime}
    p.atomic_json(ROOT/'analysis-previews'/(key+'.json'),{'created_at':now(),'binding':binding,'resources':resources})
    text=remote.script(runtime,(runtime['config'].get('remote_workspace_root') or '$HOME/230269265lht').rstrip('/')+'/analyses/<analysis_id>',manifest['entrypoint'],location,(resources.get('selection') or {}).get('requested_cores'),manifest['execution']['timeout_seconds'])
    return {'preview_id':key,'ready':True,'resources':resources,'runtime':runtime,'script':text,'reason':'将在独立 analyses 目录执行 doctor、check、run；来源文件只读'}


def stop_local(key):
    """Stop only an exact child process owned by this backend; no remote action."""
    with _process_lock:process=_local_processes.get(key)
    if process is not None and process.poll() is None:
        try:
            stop_child(process)
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            stop_child(process,force=True);process.wait()
        except ProcessLookupError:pass

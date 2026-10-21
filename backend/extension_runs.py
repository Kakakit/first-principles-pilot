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
from . import artifacts, execution as ex, extension_protocol as p, extension_remote as remote, extension_environments as environments
from .recycle import is_recycled, inactive, assert_available
from .storage import ROOT, WorkspaceStore
from .platform_runtime import exclusive_lock, process_alive, stop_child

TERMINAL={'COMPLETED','FAILED','NOT_APPLICABLE','UNKNOWN'}
ACTIVE_REMOTE={'DISPATCHING','SUBMITTED','RUNNING','UNKNOWN'}
_executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='extension')
_local_processes={};_process_lock=threading.RLock()
_threads=set();_thread_lock=threading.RLock()
RESULT_SCHEMA=json.loads((Path(__file__).resolve().parents[1]/'resources/extension-protocol/result.schema.json').read_text())


def now():return datetime.now(timezone.utc).isoformat()


def directory(key):
    if not isinstance(key,str) or not __import__('re').fullmatch(r'[a-f0-9]{32}',key):raise ValueError('analysis ID 无效')
    return ROOT/'analyses-v2'/key


def get(key):
    record=json.loads((directory(key)/'analysis.json').read_text())
    return _presentation_record(record)


def _presentation_record(record):
    from . import extension_presentations as views
    record.update(views.describe(record))
    return record


def save(record):
    if inactive('analyses',record['id'],record):return record
    record['updated_at']=now();p.atomic_json(directory(record['id'])/'analysis.json',record)
    if record['state'] not in ('DISPATCHING','RUNNING','SUBMITTED','UNKNOWN'):
        from .analysis_queue import notify
        notify()
    return record


def history(project_id=None,attempt_id=None):
    base=ROOT/'analyses-v2';rows=[]
    for path in base.glob('*/analysis.json') if base.exists() else []:
        try:
            record=json.loads(path.read_text())
            if project_id and record['project_id']!=project_id:continue
            if attempt_id and not any(s.get('attempt_id')==attempt_id for s in record['sources']):continue
            if not inactive('analyses',record['id'],record):
                rows.append(_presentation_record(record))
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
        atom_block=re.search(r'^\s*begin\s+atoms_(?:cart|frac)\s*$(.*?)^\s*end\s+atoms_(?:cart|frac)\s*$',frozen_win,re.M|re.I|re.S)
        elements=list(dict.fromkeys(match.group(1) for line in (atom_block.group(1).splitlines() if atom_block else []) for match in [re.match(r'^\s*([A-Z][a-z]?)(?:\d*)\s+[-+.0-9]',line)] if match))
        model={'bundle_id':key,'seed':record.get('seed') or bundle.get('seed') or snapshot.get('seed'),
               'parent_attempt_id':record.get('parent_attempt_id') or bundle.get('parent_attempt_id') or snapshot.get('source_attempt_id'),
               'spinors':bundle.get('spinors',snapshot.get('spinors')),
               'num_wann':num_wann,'elements':elements,
               'energy_reference':copy.deepcopy(bundle.get('energy_reference') or record.get('live_energy_reference') or {})}
        try:parent=ex.get(model['parent_attempt_id']);model['parent_name']=parent.get('job_id') or parent.get('stage_slug') or 'DFT 来源'
        except (OSError,ValueError,KeyError,TypeError):model['parent_name']='DFT 来源已记录' if model['parent_attempt_id'] else '缺少来源'
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


def _input_note(record):
    from .input_display import frozen_note
    return frozen_note(record, ROOT) or ''


def source_catalog(project_id):
    """Local metadata only: no remote reads or arbitrary directory discovery."""
    if not isinstance(project_id,str) or not project_id.strip():raise ValueError('请选择项目')
    workspace=WorkspaceStore(ROOT).read()['workspace'] or {}
    project=next((v for v in workspace.get('projects',[]) if v['id']==project_id),None)
    if project is None:raise ValueError('项目不存在')
    nodes={n['id']:n['data'] for n in project['nodes']}
    rows=[]
    # None deliberately retains older records beyond the execution page's 100-row default.
    for record in ex.history(project_id,limit=None):
        if record.get('recycled') or record.get('path_schema')!='fpilot.remote-attempt-path':continue
        from .calculation_nodes import owner, case_info
        stage,_=_stage(record);node=nodes.get(owner(record),{})
        info=case_info(record)
        input_note=_input_note(record)
        rows.append({'id':record['id'],'node_id':record.get('node_id'),'stage':stage,
                     **info, 'name':((node.get('label') or record.get('display_name') or stage) + (' · Case '+str(info['case_index']+1)+' · '+info['parameter_summary'] if info else '')),
                     'input_reason':input_note, 'input_note':input_note, 'seed':record.get('seed'),
                     'chain_run_id':record.get('chain_run_id'), 'config':{'name':record.get('config',{}).get('name')},
                     'state':record['state'],'job_id':record.get('job_id'),'created_at':record.get('created_at'),
                     'alias':record['config']['alias'],'evidence':record.get('evidence') or {}})
    return {'attempts':rows,'max_sources':p.MAX_SOURCES}


def describe_sources(raw,project_id=None):
    if not isinstance(raw,list) or not 1<=len(raw)<=p.MAX_SOURCES:raise ValueError(f'请选择 1–{p.MAX_SOURCES} 个来源')
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
                         'stage':stage,'name':record.get('job_id') or ('已完成运行' if record['state']=='COMPLETED' else '未提交运行'),'alias':record['config']['alias'],
                         'state':record['state'],'evidence':record.get('evidence') or {},'input_summary':model,
                         'remote_root':ex.attempt_path(record)['shell'],'files':[f for f in listing['files'] if f['path']==f['name']],
                         'needs_refresh':listing.get('needs_refresh',False),'host_state':listing.get('host_state','READY'),
                         'files_incomplete':listing.get('truncated',False),'refreshing':listing.get('refreshing',False),
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
    if location not in ('local','ssh'):return ['扩展只支持本地或服务器直接命令行；大型计算请使用计算节点']
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
                reasons.append(source['name']+('：文件清单待更新' if source.get('needs_refresh') or source.get('files_incomplete') else '：缺少有效 '+role['title']))
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
        runtime=None;environment=None;location=payload.get('location','local')
        if location in item['execution']['locations']:
            try:
                runtime=environments.resolve(item['id'],location,payload.get('alias',''))
                if location=='local':
                    environment=p.doctor(p.installed(item['id']),python=runtime['python'],background=True)
                    if environment and not environment['ready']:reasons.append('本机缺依赖或 Python 不可用：'+', '.join(environment.get('missing') or [environment.get('reason') or '请重新检查环境']))
            except ValueError as exc:reasons.append(str(exc))
        rows.append({**item,'blockers':reasons,'runtime':runtime,'runtime_doctor':environment,
                     'environment_checking':location=='local' and runtime is not None and environment is None})
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
    runtime=environments.resolve(manifest['id'],location,payload.get('alias',''))
    environments.expected(runtime,payload)
    if location!='local' or payload.get('preview_id'):
        approval=json.loads((ROOT/'analysis-previews'/(payload.get('preview_id','')+'.json')).read_text()) if __import__('re').fullmatch(r'[a-f0-9]{32}',payload.get('preview_id','')) else None
        binding={'revision':receipt['revision'],'sources':sources,'options':options,'location':location,'runtime':runtime}
        if not approval or approval['binding']!=binding or (datetime.now(timezone.utc)-datetime.fromisoformat(approval['created_at'])).total_seconds()>600:raise ValueError('请重新预览当前来源、选项和运行环境')
        runtime=copy.deepcopy(runtime)
        if location!='local' and any(s.get('alias') and s['alias']!=runtime['alias'] for s in sources):raise ValueError('远端来源需与运行主机相同')
    environment=p.doctor(receipt,python=runtime['python']) if location=='local' else None
    if environment is not None and not environment['ready']:raise ValueError('本地环境未满足：'+', '.join(environment.get('missing') or [environment.get('reason') or '请检查依赖']))
    key=uuid.uuid4().hex;dest=directory(key);dest.mkdir(parents=True)
    shutil.copytree(receipt['root'],dest/'package',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    record={'schema':'fpilot.analysis.v2','id':key,'project_id':payload.get('project_id') or 'geometry',
            'extension_id':manifest['id'],'manifest':manifest,'revision':receipt['revision'],'sources':sources,'options':options,
            'location':location,'runtime':runtime,'runtime_doctor':environment,'state':'QUEUED','created_at':now(),'result':None,'outputs':[],
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
    if record['state']=='COMPLETED' and record['manifest'].get('presentation'):
        from . import extension_presentations as views
        try:views.schedule(record['id'],frozen=True)
        except Exception as exc:
            record['presentation_error']=str(exc)[-1500:];save(record)


def check(payload):
    receipt=p.installed(payload['extension_id']);manifest=receipt['manifest']
    if not receipt['enabled']:raise ValueError('扩展已停用')
    options=p.options_for(manifest,payload.get('options') or {})
    sources=describe_sources(payload['sources'],payload.get('project_id'))
    reasons=blockers(manifest,sources,payload.get('location','local'))
    if reasons:return {'status':'NOT_APPLICABLE','reason':'；'.join(reasons)}
    runtime=environments.resolve(manifest['id'],payload.get('location','local'),payload.get('alias',''));environments.expected(runtime,payload)
    if payload.get('location','local')!='local':return {'status':'PENDING_REMOTE','reason':'文件清单适用；远端环境和内容检查将在独立分析目录执行'}
    environment=p.doctor(receipt,python=runtime['python'])
    if not environment['ready']:raise ValueError('本地环境未满足：'+', '.join(environment.get('missing') or [environment.get('reason') or '请检查依赖']))
    with tempfile.TemporaryDirectory(prefix='fpilot-analysis-check-') as temporary:
        dest=Path(temporary);record={'id':'check','manifest':manifest,'sources':sources,'options':options,'location':'local'}
        request=_input_request(record,dest);p.atomic_json(dest/'request.json',request)
        return _check_result(p.invoke(receipt['root'],'check',dest/'request.json',python=runtime['python'],timeout=min(manifest['execution']['timeout_seconds'],60)))


def _check_result(value):
    if value.get('schema')!='fpilot.extension-check.v2' or value.get('status') not in ('APPLICABLE','NOT_APPLICABLE') or not isinstance(value.get('reason'),str):
        raise ValueError('适用性回执格式无效')
    return value


def _local(record):
    assert_available('analyses',record['id'],record)
    dest=directory(record['id']);record.update(state='RUNNING',owner_pid=os.getpid());save(record)
    python=(record.get('runtime') or {}).get('python') or sys.executable
    environment=p.doctor(p.inspect_package(dest/'package'),python=python);record['runtime_doctor']=environment;save(record)
    if not environment['ready']:raise ValueError('本地环境未满足：'+', '.join(environment.get('missing') or [environment.get('reason') or '请检查依赖']))
    request=_input_request(record,dest);p.atomic_json(dest/'request.json',request)
    checked=_check_result(p.invoke(dest/'package','check',dest/'request.json',python=python,timeout=min(record['manifest']['execution']['timeout_seconds'],60)))
    assert_available('analyses',record['id'],record)
    record['check']=checked;save(record)
    if checked['status']!='APPLICABLE':record.update(state='NOT_APPLICABLE',error=checked['reason']);save(record);return
    from .analysis_queue import subprocess_environment
    with (dest/'stdout.log').open('w') as stdout,(dest/'stderr.log').open('w') as stderr:
        process=subprocess.Popen([python,str(p.inside(dest/'package',record['manifest']['entrypoint'])),'run','--request',str(dest/'request.json')],cwd=dest,stdout=stdout,stderr=stderr,start_new_session=True,env=subprocess_environment((record.get('runtime') or {}).get('cores')))
        with _process_lock:_local_processes[record['id']]=process
        try:
            if inactive('analyses',record['id'],record):stop_local(record['id'])
            code=process.wait(timeout=record['manifest']['execution']['timeout_seconds'])
        except subprocess.TimeoutExpired:
            stopped=_stop_local_child(process)
            if not stopped:record['stop_unconfirmed']=True
            raise ValueError('分析超时，独立子进程已停止' if stopped else '分析超时，未确认子进程退出；状态保留 UNKNOWN，请手动核查')
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
    record['last_checked_at']=now()
    record['remote_logs']=receipt.get('logs') or {}
    if receipt.get('stop_unconfirmed'):
        record.update(state='UNKNOWN',error='分析已超时，无法确认进程退出；保留原目录，不自动重新执行',
                      stop_unconfirmed=receipt['stop_unconfirmed'])
        return save(record)
    if receipt.get('doctor'):
        receipt['doctor']=p.doctor_result(receipt['doctor'],record['runtime']['python'])
        record['runtime_doctor']=receipt['doctor']
    if receipt.get('job_id'):
        job=receipt['job_id']
        if not __import__('re').fullmatch(r'\d+(?:\.[A-Za-z0-9_.-]+)?',job) or record.get('job_id') and record['job_id']!=job:raise ValueError('分析 job ID 回执不一致')
        record['job_id']=job
    if receipt.get('exit') is None:
        run=receipt.get('run_receipt') or {}
        expected=record['remote_dir'].rstrip('/')+'/outputs/result.json'
        if run.get('schema')=='fpilot.extension-run.v2' and run.get('result_file')==expected:
            record['check']=receipt.get('check')
            record['verification']={'recovered_from':'successful_cli_receipt','wrapper_exit_recorded':False,'checked_at':record['last_checked_at']}
            _collect(record)
            if record['state']!='COMPLETED':raise ValueError('成功 CLI 回执与结果状态不一致')
            record.pop('error',None);record['refresh_message']='已从成功 CLI 回执收集原分析结果；包装程序退出码未记录'
            return save(record)
        if record['location']=='ssh' and receipt.get('process_alive'):
            record.update(state='RUNNING',remote_process=receipt.get('launch'),refresh_message='原服务器分析进程仍在运行');record.pop('error',None)
            return save(record)
        if record['location']=='scheduler' and remote.queued(record):record['state']='SUBMITTED';record.pop('error',None)
        else:record.update(state='UNKNOWN',error='尚无程序结束回执；保留来源和 job ID，只核对，不重投')
        record['refresh_message']='核对完成，尚无足够结束证据；未重新执行'
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
    else:_collect(record);record.pop('error',None)
    record['refresh_message']='核对完成，已读取原任务结束回执'
    save(record)
    return record


def _remote(record):
    assert_available('analyses',record['id'],record)
    if record['location']=='scheduler':
        raise ValueError('旧扩展调度预览已失效；请选择本地或服务器直接命令行重新建立分析')
    dest=directory(record['id']);cores=None
    record.update(state='DISPATCHING',dispatch_started_at=now());save(record)
    request=_input_request(record,dest);p.atomic_json(dest/'request.json',request)
    text=remote.script(record['runtime'],record['runtime']['config'].get('remote_workspace_root','$HOME').rstrip('/')+'/analyses/'+record['id'],record['manifest']['entrypoint'],record['location'],cores,record['manifest']['execution']['timeout_seconds'])
    (dest/'launch.sh').write_text(text);record['script']=text
    shutil.copy2(Path(__file__).with_name('extension_process.py'),dest/'extension-runner.py')
    assert_available('analyses',record['id'],record)
    record['remote_dir']=remote.stage(record,dest,request);save(record)
    # A crash from here must never resubmit, even if job.id hasn't arrived yet.
    record['submission_started']=True;save(record)
    assert_available('analyses',record['id'],record)
    response=remote.direct(record);record['dispatch_receipt']=response;save(record);_refresh(record)


def _execute(key):
    from .project_sync import dispatch_guard, locally_executable, SyncStopped
    from .attempt_operations import AttemptBusy
    if inactive('analyses',key):return
    with lock(key) as owned:
        if not owned:return
        record=get(key)
        if inactive('analyses',key,record) or record['state'] not in ('QUEUED','WAIT_RESOURCE'):return
        if not locally_executable(record.get('project_id')):return
        try:
            with dispatch_guard(record):
                record=get(key)
                if record['state'] not in ('QUEUED','WAIT_RESOURCE'):return
                if record['location']=='local':_local(record)
                else:
                    from .analysis_queue import slot,Busy
                    try:
                        with slot(record['runtime']['alias'],owner=key,wait=False):_remote(record)
                    except Busy as exc:
                        record.update(state='WAIT_RESOURCE',error=str(exc));save(record)
        except (SyncStopped,AttemptBusy):
            return  # Retain the queued analysis; ownership never becomes retry authority.
        except Exception as exc:
            # A submission timeout is uncertainty, not a reason to resubmit.
            uncertain=record.get('stop_unconfirmed') or (record.get('submission_started') and record.get('exit_code') is None)
            record.update(state='UNKNOWN' if uncertain else 'FAILED',error=str(exc)[-2500:]);save(record)


def refresh(key):
    with lock(key) as owned:
        record=get(key)
        if not owned:return {**record,'refresh_busy':True,'refresh_message':'后台正在核对或收集这条分析，请稍后查看记录'}
        if record['location']=='local' or record['state'] not in ACTIVE_REMOTE:return {**record,'refresh_message':'本条分析已有终态，无需重新执行'}
        if not record.get('remote_dir'):
            record.update(state='UNKNOWN',error='准备期间中断，请检查独立目录；未自动重投');return save(record)
        try:return _refresh(record)
        except Exception as exc:record.update(state='FAILED' if record.get('exit_code') is not None else 'UNKNOWN',last_checked_at=now(),refresh_message='本次核对未完成；原任务未重新执行',error=str(exc)[-2500:]);return save(record)


def tick():
    count=0
    for record in history():
        if record['state'] in ('QUEUED','WAIT_RESOURCE'):schedule(record['id']);count+=1
        elif record['location']!='local' and record['state'] in ACTIVE_REMOTE:
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
    runtime=environments.resolve(manifest['id'],location,payload.get('alias',''));environments.expected(runtime,payload)
    if location=='local':
        key=uuid.uuid4().hex;binding={'revision':receipt['revision'],'sources':sources,'options':options,'location':location,'runtime':runtime}
        p.atomic_json(ROOT/'analysis-previews'/(key+'.json'),{'created_at':now(),'binding':binding,'resources':{'ready':True}})
        return {'preview_id':key,'ready':True,'runtime':runtime,'options':copy.deepcopy(options),'reason':'使用选定本地解释器，在独立目录运行'}
    if any(s.get('alias') and s['alias']!=runtime['alias'] for s in sources):raise ValueError('远端来源需与运行主机相同')
    resources={'ready':True}
    if not resources['ready']:return resources
    key=uuid.uuid4().hex;binding={'revision':receipt['revision'],'sources':sources,'options':options,'location':location,'runtime':runtime}
    p.atomic_json(ROOT/'analysis-previews'/(key+'.json'),{'created_at':now(),'binding':binding,'resources':resources})
    text=remote.script(runtime,(runtime['config'].get('remote_workspace_root') or '$HOME').rstrip('/')+'/analyses/analysis-preview',manifest['entrypoint'],location,(resources.get('selection') or {}).get('requested_cores'),manifest['execution']['timeout_seconds'])
    return {'preview_id':key,'ready':True,'resources':resources,'runtime':runtime,'script':text,'reason':'将在独立 analyses 目录执行 doctor、check、run；来源文件只读','options':copy.deepcopy(options)}


def stop_local(key):
    """Stop only an exact child process owned by this backend; no remote action."""
    with _process_lock:process=_local_processes.get(key)
    if process is not None and process.poll() is None:
        return _stop_local_child(process)
    return True


def _stop_local_child(process):
    try:stop_child(process,force=True)
    except (ProcessLookupError,subprocess.TimeoutExpired):pass
    try:process.wait(timeout=2)
    except subprocess.TimeoutExpired:return False
    return True

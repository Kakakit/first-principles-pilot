"""Explicit, durable recovery scopes. Only confirmed operations may dispatch."""
import copy
import difflib
import hashlib
import io
import json
import re
import uuid
import zipfile
from contextlib import ExitStack

from . import execution as ex, execution_control as control, scans, chain_runs, servers, recycle, candidate_pool, wannier_scans
from .platform_runtime import exclusive_lock

TERMINAL={'FAILED','COMPLETED','CANCELLED'}
REMAINING={'PENDING','PREPARED','FAILED','BLOCKED','RETRY_PENDING','RECOVERY_PENDING'}


def path(key):
    if not re.fullmatch(r'[a-f0-9]{32}',key):raise ValueError('恢复操作身份无效')
    return ex.ROOT/'task-recovery'/(key+'.json')


def read(key):return json.loads(path(key).read_text())


def reference(key):
    try:return read(key)
    except OSError:return {'state':'MISSING','items':[]}
def save(value):
    target=path(value['id'])
    if target.exists():
        previous=json.loads(target.read_text())
        replaced={i['id'] for i in previous.get('items',[]) if i.get('dispatch_state')=='SUPERSEDED'}
        for item in value.get('items',[]):
            if item['id'] in replaced:item.update(dispatch_state='SUPERSEDED',waiting_reason='已由新的显式恢复范围替代')
        if replaced and all(i.get('dispatch_state')!='PENDING' for i in value.get('items',[])) and value['state']=='CONFIRMED':value['state']='FINISHED'
        if previous.get('state')=='PAUSED' and value['state'] in ('CONFIRMED','FINISHED') and previous.get('paused_at','')>=value.get('resumed_at',''):
            value.update(state='PAUSED',paused_at=previous.get('paused_at'),snapshot_pause_reason=previous.get('snapshot_pause_reason'))
    control.write_json(target,value)
    return value


def container(kind,key,project_id):
    if kind not in ('attempts','scans','chains'):raise ValueError('恢复类型无效')
    value={'attempts':ex.get,'scans':scans.get,'chains':chain_runs.get}[kind](key)
    if value.get('project_id')!=project_id:raise ValueError('任务不属于当前项目')
    recycle.assert_available(kind,key,value)
    return value


def entries(kind,value):
    if kind=='attempts':return [{'id':value['id'],'attempt_id':value['id'],'bundle_id':value['bundle_id'],'state':value['state']}]
    return value['cases'] if kind=='scans' else [{**s,'id':s['node_id']} for s in value['steps']]


def describe(kind,value,item):
    parent=ex.get(item['attempt_id']) if item.get('attempt_id') else None
    auth=value.get('authorization') or {}
    resource=(auth.get('resources',{}).get(item['id']) or {}) if kind=='chains' else auth
    config=copy.deepcopy(parent['config'] if parent else auth.get('config') or servers.read_config())
    if kind=='chains' and not parent and resource:config=chain_runs._node_config(config,resource)
    choices=copy.deepcopy(resource.get('candidates') or [])
    node=parent.get('node') if parent else resource.get('node') or (choices[0]['node'] if choices else '')
    cores=parent.get('cores') if parent else resource.get('cores') or (choices[0]['cores'] if choices else None)
    if parent and cores is None:cores=parent.get('profile',{}).get('ppn',parent.get('profile',{}).get('ntasks'))
    template=(parent or {}).get('template_snapshot',{}).get('id') or resource.get('template_id') or config.get('template_id')
    window=(parent or {}).get('kind')=='wannier90' or kind=='scans' and wannier_scans.is_window(value)
    if window:
        from . import wannier_execution as w
        bundle=w.configuration(item['bundle_id']);text=bundle['win']
    else:
        _,files=ex.bundle(item['bundle_id'],allow_dependencies=True);text=((parent or {}).get('final_incar') or files['INCAR'].decode())
    state=(parent or item)['state']
    blocked=state in control.ACTIVE and state!='UNKNOWN'
    return {'id':item['id'],'label':item.get('label') or ('Case '+str(item.get('index',0)+1) if kind=='scans' else '当前任务'),
            'attempt_id':item.get('attempt_id'),'bundle_id':item['bundle_id'],'state':state,
            'config':config,'node':node,'cores':cores,'template_id':template,'choices':choices,'template_snapshot':(parent or {}).get('template_snapshot') or (parent or {}).get('script_source'),
            'window':window,'input_text':text,'parameters':item.get('parameters'),
            'blockers':['原任务仍在途，请先核查或取消'] if blocked else [],
            'warnings':['原提交结果仍不明确，新提交可能与原作业重复'] if state=='UNKNOWN' else []}


def context(project_id,kind,target_id,item_ids=None,scope='single'):
    value=container(kind,target_id,project_id);items=entries(kind,value)
    selected=set(item_ids or [])
    if scope not in ('single','remaining'):raise ValueError('恢复范围无效')
    result=[];skipped=[]
    for item in items:
        if scope=='single' and kind!='attempts' and item['id'] not in selected:continue
        if scope=='remaining' and item.get('recovery_operation_id') and reference(item['recovery_operation_id'])['state']=='CONFIRMED':
            skipped.append({'id':item['id'],'state':'RECOVERY_PENDING'});continue
        try:info=describe(kind,value,item)
        except (ValueError,OSError,KeyError) as exc:
            info={'id':item['id'],'label':item.get('label','所选任务'),'state':item['state'],'attempt_id':item.get('attempt_id'),'bundle_id':item['bundle_id'],
                  'config':(value.get('authorization') or {}).get('config',{}),'node':'','cores':None,'window':False,'input_text':'','blockers':[str(exc)],'warnings':[]}
        if scope=='remaining' and (info['state'] not in REMAINING or info['state']=='CANCELLED'):
            skipped.append({'id':info['id'],'state':info['state']});continue
        result.append(info)
    if scope=='single' and len(result)!=1:raise ValueError('请选择一个具体任务')
    operations=[json.loads(p.read_text()) for p in (ex.ROOT/'task-recovery').glob('*.json')]
    operations=[op for op in operations if op['project_id']==project_id and op['kind']==kind and op['target_id']==target_id and op['state']!='PREVIEW']
    operations.sort(key=lambda op:op['created_at'],reverse=True)
    return {'operations':operations[:5],'project_id':project_id,'kind':kind,'target_id':target_id,'scope':scope,'items':result,'skipped':skipped,
            'max_parallel':(value.get('authorization') or {}).get('max_parallel',1)}


def _edited_bundle(info,edit,mode,source_id,acknowledged):
    """Freeze a derived input, preserving the exact original geometry and lineage."""
    if (not edit or edit==info['input_text']) and mode=='keep':return info['bundle_id'],''
    if info['window']:
        from . import wannier_execution as w, inputs
        if mode not in ('keep','checkpoint'):raise ValueError('Wannier90 请选择原矩阵重算或检查点续算')
        value=w.configuration(info['bundle_id']);before=value['win']
        value['win']=edit or before
        if mode=='checkpoint':
            from . import source_files
            source=ex.get(source_id or info['attempt_id'])
            if source.get('kind')!='wannier90' or source.get('config',{}).get('alias')!=value['alias'] or source.get('parent_attempt_id')!=value['parent_attempt_id'] or source.get('seed')!=value['seed']:raise ValueError('检查点须属于同一矩阵来源、服务器和 seed')
            if source['state'] in control.ACTIVE and not (source['state']=='UNKNOWN' and acknowledged):raise ValueError('检查点来源仍在途或 UNKNOWN 未确认')
            original=w.configuration(source['bundle_id'])
            if w.relative_window_values(value['win'],value['energy_reference']['fermi_eV'])!=original['relative_windows']:raise ValueError('检查点续算需保持原能窗；改变能窗请用原矩阵重算')
            checkpoint_path=ex.attempt_path(source)['shell'];name=value['seed']+'.chk'
            metadata=source_files.snapshot(value['alias'],checkpoint_path,[name],ex.ssh,'lightweight')
            value['checkpoint']={'source_attempt_id':source['id'],'source_path':checkpoint_path,'name':name,'metadata':metadata[name]}
            value['win']=w.edit_win(value['win'],{'restart':'default'})['win']
        w._checked_win(before,value['win'])
        value['win_sha256']=w._sha(value['win'].encode())
        value['relative_windows']=w.relative_window_values(value['win'],value['energy_reference']['fermi_eV'])
        value['absolute_windows']={k:v for k,v in w.parameter_values(value['win']).items() if k in w.WINDOW_TAGS and v}
        value.update(recovery_of_bundle=info['bundle_id'],created_at=ex.now())
        raw=json.dumps(value,ensure_ascii=False,sort_keys=True).encode();key=w._sha(raw);inputs.put_file(w.ROOT/'wannier-bundles'/(key+'.json'),raw)
        return key,''.join(difflib.unified_diff(before.splitlines(True),value['win'].splitlines(True),fromfile='原 .win',tofile='本次 .win'))
    from . import inputs, incar_text, source_contracts as sources
    with zipfile.ZipFile(ex.ROOT/'inputs'/(info['bundle_id']+'.zip')) as z:files={n:z.read(n) for n in z.namelist()}
    manifest=json.loads(files['manifest.json']);before=info['input_text'];text=edit or before
    if not isinstance(text,str) or not 0<len(text.encode())<=262144:raise ValueError('INCAR 须为非空文本且不超过 256 KiB')
    tags,_,errors=incar_text.parse(text)
    if errors:raise ValueError('INCAR 无法解析：'+'；'.join(map(str,errors)))
    sources.validate_pead(text)
    files['INCAR']=text.encode()
    old=ex.get(info['attempt_id']) if info.get('attempt_id') else {}
    snapshot=old.get('chain_source_snapshot') or {}
    if snapshot.get('actual_poscar'):files['POSCAR']=snapshot['actual_poscar'].encode();files.pop('POSCAR.reference',None)
    contract=copy.deepcopy(snapshot.get('source_contract') or manifest.get('source_contract'))
    if mode!='keep':
        if mode not in ('new','structure','charge','both'):raise ValueError('续算方式无效')
        if mode=='new':
            if manifest['stage'] in sources.FIXED:raise ValueError('固定电荷阶段需要 CHGCAR 来源，请选择读取电荷')
            text=incar_text.merge(text,'ISTART=0\nICHARG=2\n')
            policy={'mode':'new','purpose':'charge','files':'structure','parent_id':None,'stage':manifest['stage'],'required_files':[],
                    'electronic_usage':'none','initialization':{'ISTART':0,'ICHARG':2},'draft':{}}
            contract={'schema':'fpilot.source-contract.v2','mode':'new','structure':'configured','roles':{},'source_plan':policy,'initialization':policy['initialization']}
        else:
            source=ex.get(source_id or info['attempt_id'])
            if source.get('project_id')!=old.get('project_id',manifest.get('project_id')) and old:raise ValueError('续算来源不属于同一项目')
            structure='CONTCAR' if manifest['stage']=='relax' else 'POSCAR'
            required=[structure]+(['CHGCAR'] if mode in ('charge','both') else [])+(['WAVECAR'] if mode=='both' else [])
            if manifest['stage'] in sources.FIXED and mode=='structure':raise ValueError('固定电荷阶段必须读取 CHGCAR')
            initial={'ISTART':sources.wave_start(manifest['stage'],text,manifest['stage']=='relax') if mode=='both' else 0,
                     'ICHARG':11 if manifest['stage'] in sources.FIXED else 1 if mode in ('charge','both') else 2}
            text=incar_text.merge(text,'\n'.join(k+'='+str(v) for k,v in initial.items())+'\n')
            policy={'mode':'manual','purpose':'continue' if structure=='CONTCAR' else 'charge','files':mode,'stage':manifest['stage'],
                    'parent_id':None,'structure_file':structure,'required_files':required,'initialization':initial,
                    'electronic_usage':'fixed' if manifest['stage'] in sources.FIXED else 'initial' if mode in ('charge','both') else 'none',
                    'acknowledge_differences':True,'draft':{'acknowledge_unverified':acknowledged}}
            report={**manifest,'files':{n:v.decode() for n,v in files.items() if n in ('INCAR','POSCAR','POSCAR.reference','KPOINTS')},'errors':[]}
            report['files']['INCAR']=text
            report.pop('poscar_scan',None)
            manifest.pop('poscar_scan',None)
            contract=sources.bind(policy,source,report,manifest.get('verification_mode','lightweight'))
            files['POSCAR']=report['files']['POSCAR'].encode();files.pop('POSCAR.reference',None)
            for name in ('geometry_sha256','source_sha256','structure_source'):
                if name in report:manifest[name]=report[name]
        manifest['source_plan']=policy;manifest['source_contract']=contract
        manifest['dependencies']=[{**d,'status':'bound'} for d in manifest.get('dependencies',[])]
        manifest['ready']=True;manifest['restart_required']=False
    elif contract and snapshot:
        manifest['source_contract']=contract;manifest['source_plan']=contract['source_plan']
    files['INCAR']=text.encode()
    manifest['incar_tags']={k:{'value':v,'source':'recovery'} for k,v in incar_text.parse(text)[0].items()}
    manifest.update(recovery_of_bundle=info['bundle_id'],reason='',errors=[])
    manifest['files']={n:{'sha256':hashlib.sha256(v).hexdigest(),'size_bytes':len(v)} for n,v in files.items() if n!='manifest.json'}
    files['manifest.json']=scans.packed(manifest).encode()
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
        for name,data in sorted(files.items()):
            entry=zipfile.ZipInfo(name,(1980,1,1,0,0,0));entry.compress_type=zipfile.ZIP_DEFLATED;z.writestr(entry,data)
    raw=stream.getvalue();key=inputs.digest(raw);inputs.put_file(ex.ROOT/'inputs'/(key+'.zip'),raw)
    return key,''.join(difflib.unified_diff(before.splitlines(True),text.splitlines(True),fromfile='原 INCAR',tofile='本次 INCAR'))


def preview(project_id,kind,target_id,item_ids=None,scope='single',config_overrides=None,node=None,cores=None,template_id=None,input_text='',parameter_patch='',restart_mode='keep',source_attempt_id=None,acknowledge_unverified=False,**unused):
    result=context(project_id,kind,target_id,item_ids,scope)
    if not result['items']:raise ValueError('没有失败或尚未提交的任务')
    if scope=='remaining' and input_text:raise ValueError('整批修改使用公共参数补丁，不可用一份原文覆盖所有 case')
    operation={**{k:v for k,v in result.items() if k!='operations'},'id':uuid.uuid4().hex,'state':'PREVIEW','created_at':ex.now(),'warnings':[]}
    probes={}
    for info in operation['items']:
        info['attempt_key']=hashlib.sha256((operation['id']+':'+info['id']).encode()).hexdigest()[:32]
        try:
            if info['blockers']:raise ValueError('；'.join(info['blockers']))
            info['config'].update(config_overrides or {})
            if template_id is None and info.get('template_id') and info.get('template_snapshot'):
                from . import job_templates
                try:
                    recycle.assert_available('job-templates',info['template_id']);job_templates.get_template(info['template_id'])
                except (ValueError,OSError):
                    saved=info['template_snapshot']
                    if not saved.get('original_text'):raise ValueError('原脚本快照不可用，请选择脚本')
                    restored=job_templates.import_template(saved['original_text'],info['config']['alias'],saved['program'],'原任务 '+str(info.get('attempt_id'))+' 的脚本快照','恢复脚本副本',scheduler=saved['scheduler'])
                    info['template_id']=restored['id']
                    info['warnings'].append('使用原任务保存的脚本新建恢复副本；原回收记录保留')
            if node is not None:info['node']=node
            if cores is not None:info['cores']=cores
            if template_id is not None:info['template_id']=template_id or None;info['config']['template_id']=template_id
            if info['template_id']:info['config']['template_id']=info['template_id']
            info['config']=servers.validate(info['config'])
            if not info['node']:raise ValueError('请选择计算节点')
            edited=input_text
            if parameter_patch:
                if info['window']:
                    from .wannier_execution import parameter_values, edit_win
                    edited=edit_win(info['input_text'],{k:v for k,v in parameter_values(parameter_patch).items() if v})['win']
                else:
                    from .incar_text import merge
                    edited=merge(info['input_text'],parameter_patch)
            info['new_bundle_id'],info['input_diff']=_edited_bundle(info,edited,restart_mode,source_attempt_id,acknowledge_unverified)
            cache=scans.digest(servers.capacity_config(info['config']))
            purpose='wannier90' if info['window'] else 'vasp'
            if cache not in probes:probes[cache]=servers.capacity_probe(info['config'],purpose=purpose)
            rendered=servers.preview_for_prepare(info['config'],probes[cache]['id'],info['node'],info['cores'],'',purpose=purpose)
            if not rendered['resource_ready'] and not candidate_pool.capacity_wait(rendered):
                raise ValueError('资源请求不可用：'+'；'.join(rendered['blockers']))
            if info['window']:
                from . import wannier_execution as w
                script,_=w._render_script(info['config'],rendered['profile'],info['template_id'])
                rendered['script']=script
            info['script']=ex.with_exit_trap(rendered['script'],info['config']['scheduler']);info['warnings']+=rendered.get('script_warnings',[])+rendered.get('blockers',[])
            info['attempt_key']=hashlib.sha256((operation['id']+':'+info['id']).encode()).hexdigest()[:32]
            info['dispatch_state']='PENDING'
        except (ValueError,OSError,KeyError) as exc:
            info.update(dispatch_state='FAILED',preview_error=True,error=control.redact(str(exc))[:1000])
    operation['requires_unknown_confirmation']=any(i['state']=='UNKNOWN' for i in operation['items'])
    return save(operation)


def _module(kind):return scans if kind=='scans' else chain_runs


def authorized(record):
    key=record.get('recovery_operation_id')
    if not key:return False
    op=reference(key)
    if op['state']=='MISSING':return False
    if op['kind']!='attempts':
        value=container(op['kind'],op['target_id'],op['project_id'])
        if value.get('dispatch_pause_at','')>op.get('resumed_at',op.get('confirmed_at','')):return False
    return op['state'] in ('CONFIRMED','FINISHED') and op['project_id']==record['project_id'] and any(i.get('attempt_key')==record['id'] and i.get('new_bundle_id')==record['bundle_id'] for i in op['items'])


def _parent_lock(op):
    return _module(op['kind'])._dispatch_lock(op['target_id']) if op['kind']!='attempts' else exclusive_lock(ex.ROOT/'task-recovery'/(op['target_id']+'.target.lock'))


def confirm(key,project_id,confirm_unknown=False,*,dispatch=True):
    with exclusive_lock(path(key).with_suffix('.lock')) as owned:
        if not owned:
            op=read(key)
            if op['project_id']==project_id and op['state']!='PREVIEW':return op
            raise ValueError('恢复操作正在提交，请读取当前状态')
        op=read(key)
        if op['project_id']!=project_id:raise ValueError('恢复操作不属于当前项目')
        if op['state']!='PREVIEW':return op
        if not any(i['dispatch_state']=='PENDING' for i in op['items']):raise ValueError('本次没有可提交项目，请修正逐项错误')
        if op['requires_unknown_confirmation'] and confirm_unknown is not True:raise ValueError('请确认 UNKNOWN 新提交可能产生重复作业')
        with _parent_lock(op) as acquired:
            if not acquired:raise ValueError('计划正在派发，请稍后读取并重试确认')
            value=container(op['kind'],op['target_id'],project_id)
            current={i['id']:i for i in entries(op['kind'],value)}
            for saved in (ex.ROOT/'task-recovery').glob('*.json'):
                other=json.loads(saved.read_text())
                if other['id']==op['id'] or other['state'] not in ('CONFIRMED','FINISHED') or (other['kind'],other['target_id'])!=(op['kind'],op['target_id']):continue
                overlap={i['id'] for i in other['items']}&{i['id'] for i in op['items']}
                if overlap and other['state']=='CONFIRMED':
                    for prior in other['items']:
                        if prior['id'] not in overlap:continue
                        try:actual=ex.get(prior['attempt_key'])
                        except ValueError:actual=None
                        if actual and actual['state'] in control.ACTIVE:raise ValueError('最近的恢复任务已在途，请读取原回执')
                        prior.update(dispatch_state='SUPERSEDED',waiting_reason='已由新的显式恢复范围替代')
                    if all(i['dispatch_state']!='PENDING' for i in other['items']):other['state']='FINISHED'
                    save(other)
                if op['kind']=='attempts' and overlap:
                    for prior in other['items']:
                        if prior.get('dispatch_state')=='SENT' and ex.get(prior['attempt_key'])['state'] in control.ACTIVE:raise ValueError('最近的重算任务仍在途，请读取其原回执')
            for info in op['items']:
                if info.get('preview_error'):continue
                item=current.get(info['id'])
                if not item or (item.get('attempt_id'),item['bundle_id'])!=(info['attempt_id'],info['bundle_id']):raise ValueError('所选任务版本已变化，请重新预览')
                state=ex.get(info['attempt_id'])['state'] if info['attempt_id'] else item['state']
                if state in control.ACTIVE and state!='UNKNOWN':raise ValueError('所选任务已在途，请重新预览')
                if op['scope']=='remaining' and state in ('COMPLETED','CANCELLED'):raise ValueError('所选任务已结束，请重新预览跳过该项')
                if item.get('recovery_operation_id'):
                    prior=reference(item['recovery_operation_id'])
                    if prior['state']=='CONFIRMED' and any(i['id']==info['id'] and i['dispatch_state']=='PENDING' for i in prior['items']):raise ValueError('所选任务已有恢复操作，请读取该操作')
            op.update(state='CONFIRMED',confirmed_at=ex.now(),unknown_confirmed=confirm_unknown)
            save(op)  # authority precedes preparation or scheduler calls
            if op['kind']!='attempts':
                field='cases' if op['kind']=='scans' else 'steps'
                selected={i['id'] for i in op['items'] if not i.get('preview_error')}
                for item in value[field]:
                    if item.get('id',item.get('node_id')) in selected:item['recovery_operation_id']=key
                if op['scope']=='remaining':value.update(state='ACTIVE',blocker=None)
                value['recovery_scope']={'operation_id':key,'item_ids':list(selected),'at':op['confirmed_at']}
                _module(op['kind']).save(value)
        if dispatch:_dispatch(op)
        return read(key)


def status(key,project_id):
    """Read current receipts without dispatching or rewriting historical authority."""
    op=read(key)
    if op['project_id']!=project_id:raise ValueError('恢复操作不属于当前项目')
    for info in op['items']:
        try:record=ex.get(info['attempt_key'])
        except (KeyError,ValueError):continue
        info.update(state=record['state'],attempt_id_new=record['id'],job_id=record.get('job_id'))
        if record.get('note'):info['receipt_note']=record['note']
        receipt=record.get('dispatch_receipt') or {}
        if record['state']=='UNKNOWN':
            info.update(dispatch_state='UNCERTAIN',receipt_note=record.get('note') or receipt.get('stderr') or '提交回执不明，请核查原作业；未自动重投')
        elif info['dispatch_state']=='UNCERTAIN' and record.get('job_id'):info['dispatch_state']='SENT'
    return op


def process(key):
    """HTTP background work and the existing worker share the same durable lock."""
    with exclusive_lock(path(key).with_suffix('.lock')) as owned:
        if not owned:return
        op=read(key)
        if op['state']=='CONFIRMED':_dispatch(op)


def progress(op,info,phase):
    info['progress']={'phase':phase,'at':ex.now()}
    info.pop('waiting_reason',None)
    save(op)


def _attach(op,info,record):
    if op['kind']=='attempts':return
    module=_module(op['kind']);value=module.get(op['target_id'])
    field='cases' if op['kind']=='scans' else 'steps'
    item=next(i for i in value[field] if i.get('id',i.get('node_id'))==info.get('attached_id',info['id']))
    if info.get('attached_id') is None and op['kind']=='scans' and coordinates_changed(item,info):
        # A derived case retains its own visible input identity, never relabel an old result.
        derived_id=scans.digest({'operation':op['id'],'case':info['id']})[:24]
        existing=next((c for c in value['cases'] if c['id']==derived_id),None)
        if existing is None:
            derived=copy.deepcopy(item);derived.update(id=derived_id,derived_from_case_id=item['id'],
                attempt_id=None,attempt_history=[],reuse=None)
            derived['index']=len(value['cases']);derived['parameters']=_parameters(derived,info)
            value['cases'].append(derived)
        else:derived=existing
        item.pop('recovery_operation_id',None);item=derived
        record['scan_case_id']=item['id'];info['attached_id']=item['id']
    history=item.setdefault('attempt_history' if op['kind']=='scans' else 'attempts',[])
    previous=item.get('attempt_id')
    if previous and previous!=record['id'] and previous not in history:history.append(previous)
    item.setdefault('original_bundle_id',item['bundle_id'])
    if op['kind']=='scans' and info['new_bundle_id']!=item['bundle_id']:
        manifest,files=(None,{}) if info['window'] else ex.bundle(info['new_bundle_id'],allow_dependencies=True)
        item.setdefault('input_versions',[]).append({'bundle_id':item['bundle_id'],'input_hashes':item.get('input_hashes',{})})
        if manifest:item['input_hashes']={n:hashlib.sha256(v).hexdigest() for n,v in files.items()}
        if info['window']:
            from .wannier_execution import configuration
            window=configuration(info['new_bundle_id']);item.update(relative_windows=window['relative_windows'],absolute_windows=window['absolute_windows'],win=window['win'])
    item.update(attempt_id=record['id'],state=record['state'],bundle_id=info['new_bundle_id'],recovery_operation_id=op['id'])
    if op['kind']=='chains':
        if record['id'] not in history:history.append(record['id'])
        if previous!=record['id']:item.pop('final_attempt_id',None)
    item.pop('dispatch_error',None);item.pop('blocker',None);item.pop('waiting_reason',None)
    if op['kind']=='scans':item['retry_count']=item.get('retry_count',0)+(previous!=record['id'])
    if op['kind']=='chains' and op['scope']=='remaining':value['state']='ACTIVE'
    module.save(value);ex.save(record);save(op)


def _parameters(case,info):
    parameters=copy.deepcopy(case.get('parameters',[]))
    if info['window']:return parameters
    from .source_contracts import tag
    manifest,files=ex.bundle(info['new_bundle_id'],allow_dependencies=True)
    for p in parameters:
        if p.get('kind')=='incar':p['value']=tag(files['INCAR'],p['key']) or p['value']
        elif p.get('kind') in ('u','j'):
            values=(tag(files['INCAR'],'LDAUU' if p['kind']=='u' else 'LDAUJ') or '').split()
            # The actual input remains authoritative even for non-scalar element arrays.
            species=manifest.get('species',[])
            if p['key'] in species and len(values)==len(species):p['value']=values[species.index(p['key'])]
            p['actual_values']=' '.join(values)
    return parameters


def coordinates_changed(case,info):
    if info['bundle_id']==info['new_bundle_id']:return False
    if info['window']:
        from .wannier_execution import configuration
        return configuration(info['bundle_id'])['relative_windows']!=configuration(info['new_bundle_id'])['relative_windows']
    return [(p['key'],p['value']) for p in case.get('parameters',[])]!=[(p['key'],p['value']) for p in _parameters(case,info)]


def _dispatch(op):
    from . import candidate_pool
    for info in op['items']:
        if info['dispatch_state']!='PENDING':continue
        try:
            with _parent_lock(op) as owned:
                if not owned:continue
                value=container(op['kind'],op['target_id'],op['project_id'])
                current_op=read(op['id'])
                if current_op['state']!='CONFIRMED':return
                if any(i['id']==info['id'] and i['dispatch_state']=='SUPERSEDED' for i in current_op['items']):
                    info['dispatch_state']='SUPERSEDED';continue
                if op['kind']!='attempts':
                    current=next((i for i in entries(op['kind'],value) if i['id']==info.get('attached_id',info['id'])),None)
                    if current and current['state']=='CANCELLED' and info['state']!='CANCELLED':
                        info.update(dispatch_state='SUPERSEDED',waiting_reason='用户已取消，未提交');save(op);continue
                    owner=(current or {}).get('recovery_operation_id')
                    if current is None or owner and owner!=op['id'] and reference(owner)['state'] in ('CONFIRMED','PAUSED'):
                        info.update(dispatch_state='SUPERSEDED',waiting_reason='任务已由其他明确操作接管');save(op);continue
                from .project_sync import locally_executable
                if not locally_executable(op['project_id']):
                    info['waiting_reason']='当前项目尚未允许本机派发';save(op);continue
                if op['kind']!='attempts' and value.get('dispatch_pause_at','')>op.get('resumed_at',op['confirmed_at']):
                    info['waiting_reason']='用户已暂停，等待明确继续本次恢复范围';save(op);continue
                if info['state']=='UNKNOWN' and op.get('unknown_confirmed') and info.get('attempt_id'):
                    release_quota(op['project_id'],info['attempt_id'])
                if op['kind']=='scans':
                    live=[ex.get(c['attempt_id']) for c in value['cases'] if c.get('attempt_id')]
                    if sum(r['state'] in control.ACTIVE and not r.get('quota_released') for r in live)>=op['max_parallel']:
                        info['waiting_reason']='达到本批在途上限，等待已有任务结束或明确解除 UNKNOWN 的额度占用';save(op);continue
                args={}
                if op['kind']=='chains':
                    step=next(s for s in value['steps'] if s['node_id']==info['id']);parent=next((s for s in value['steps'] if s['node_id']==step['parent_id']),None)
                    manifest=None if info['window'] else ex.bundle(info['new_bundle_id'],allow_dependencies=True)[0]
                    if manifest and manifest.get('source_contract',{}).get('mode')=='planned':
                        binding=value.get('reuse_bindings',{}).get(step['parent_id'])
                        source=(binding or {}).get('attempt_id') if parent is None else None
                        if parent and parent.get('attempt_id'):
                            actual=ex.get(parent['attempt_id'])
                            if actual['state']=='COMPLETED':
                                valid=parent['stage'] not in ('relax','scf') or chain_runs.convergence(actual,parent['stage'])['status']=='CONVERGED'
                                if valid:source=actual['id']
                                else:raise ValueError('上游尚未收敛，先恢复上游或明确选择手动来源')
                            elif actual['state'] in ('FAILED','UNKNOWN','CANCELLED'):raise ValueError('上游分支尚未完成，先恢复上游')
                        if not source:continue
                        args.update(chain_source_id=source,chain_source_kind='dependency')
                try:record=ex.get(info['attempt_key'])
                except ValueError:record=None
                if record is None:
                    purpose='wannier90' if info['window'] else 'vasp'
                    progress(op,info,'PROBING')
                    probe=servers.capacity_probe(info['config'],purpose=purpose)
                    choices=[{'node':info['node'],'cores':info['cores']}]
                    rendered=None
                    for choice in choices:
                        test=servers.preview_for_prepare(info['config'],probe['id'],choice['node'],choice['cores'],'',purpose=purpose)
                        if test['resource_ready']:rendered=test;info['node']=choice['node'];info['cores']=choice['cores'];break
                        if not candidate_pool.capacity_wait(test):raise ValueError('资源请求不可用：'+'；'.join(test['blockers']))
                    if rendered is None:info['waiting_reason']='等待所选节点空闲';save(op);continue
                    progress(op,info,'PREPARING')
                    if info['window']:
                        from . import wannier_execution as w
                        bundle=w.configuration(info['new_bundle_id'])
                        record=w.prepare(bundle['project'],bundle['node_id'],bundle['parent_attempt_id'],info['config'],probe['id'],info['node'],info['cores'],'',
                            template_id=info['template_id'],frozen_id=info['new_bundle_id'],attempt_id=info['attempt_key'],execution_scope=bundle.get('execution_scope'),
                            **({'scan_batch_id':op['target_id'],'scan_case_id':info['id'],'scan_case_table_hash':value['case_table_hash']} if op['kind']=='scans' else {}))
                    else:
                        manifest,_=ex.bundle(info['new_bundle_id'],allow_dependencies=True)
                        record=ex.prepare(info['new_bundle_id'],info['config'],probe['id'],info['node'],info['cores'],'',info['template_id'],project_id=op['project_id'],
                                          attempt_id=info['attempt_key'],execution_scope=manifest.get('execution_scope'),**args)
                    record.update(recovery_operation_id=op['id'],recovery_of=info['attempt_id'],recovery_confirmed_at=op['confirmed_at'])
                    if op['kind']=='scans':record.update(scan_batch_id=op['target_id'],scan_case_id=info['id'],scan_case_table_hash=value['case_table_hash'])
                    if op['kind']=='chains':record.update(chain_run_id=op['target_id'],chain_step_id=info['id'],chain_plan_hash=value['plan_hash'])
                    ex.save(record)
                    if record.get('script') and record['script']!=info['script']:raise ValueError('本次脚本或资源与确认预览不同，请重新预览')
                # Reattach even after a crash between preparing and updating the container.
                _attach(op,info,record)
                if record['state']=='PREPARED':
                    progress(op,info,'SUBMITTING')
                    record=ex.submit(record['id'])
                if record['state']=='PREPARED':info['waiting_reason']=record.get('note') or '等待资源或额度';save(op);continue
                outcome='UNCERTAIN' if record['state']=='UNKNOWN' else 'FAILED' if record['state']=='FAILED' else 'SENT'
                info.update(dispatch_state=outcome,state=record['state'],attempt_id_new=record['id'],job_id=record.get('job_id'))
                if record.get('note'):info['receipt_note']=record['note']
                receipt=record.get('dispatch_receipt') or {}
                if outcome in ('UNCERTAIN','FAILED'):
                    info['error']='\n'.join(v for v in (record.get('note'),receipt.get('stderr')) if v) or '未取得有效提交回执，请核查此项'
                info.pop('waiting_reason',None);_attach(op,info,record)
        except Exception as exc:
            message=control.redact(str(exc))[:1000]
            try:actual=ex.get(info['attempt_key'])
            except (KeyError,ValueError):actual=None
            if actual and actual['state'] in ('UNKNOWN','DISPATCHING'):
                info.update(dispatch_state='UNCERTAIN',state=actual['state'],attempt_id_new=actual['id'],job_id=actual.get('job_id'),error=message)
                _attach(op,info,actual)
                save(op);continue
            if any(v in message for v in ('在途任务数已达上限','核数预算不足','实时资源已变化','正在派发','恢复授权已暂停')):
                info['waiting_reason']=message
            else:
                info.update(dispatch_state='FAILED',error=message)
        save(op)
    if all(i['dispatch_state']!='PENDING' for i in op['items']):
        op.update(state='FINISHED',finished_at=ex.now());save(op)


def holds_item(operation_id,item_id):
    if not operation_id:return False
    op=reference(operation_id)
    if op['state']=='MISSING':return True
    item=next((i for i in op['items'] if item_id in (i['id'],i.get('attached_id'))),None)
    return bool(item and item['dispatch_state'] in ('PENDING','FAILED'))


def tick():
    for p in (ex.ROOT/'task-recovery').glob('*.json'):
        op=json.loads(p.read_text())
        if op['state']!='CONFIRMED':continue
        process(op['id'])


def release_quota(project_id,key):
    from .attempt_operations import locked
    with locked({key}):
        record=container('attempts',key,project_id)
        if record['state']!='UNKNOWN':raise ValueError('只有 UNKNOWN 可单独解除本机额度占用')
        if record.get('quota_released'):return record
        record['quota_released']={'at':ex.now(),'reason':'用户手动解除；原作业仍继续跟踪'}
        record['updated_at']=ex.now()
        return ex.save(record)


def prepare_legacy_retry(key,probe_id,node,cores=None,reason=''):
    """Keep old clients' prepare-then-submit response, with the relaxed policy."""
    parent=ex.get(key)
    if parent.get('path_schema')!='fpilot.remote-attempt-path':raise ValueError('attempt 目录结构不受支持')
    if parent['state'] in control.ACTIVE:raise ValueError('在途或 UNKNOWN 请先核查；UNKNOWN 可通过新恢复入口显式确认')
    kwargs={}
    source=parent.get('dependency_snapshot') or {}
    if source.get('upstream_attempt_id'):kwargs.update(dependency_attempt_id=source['upstream_attempt_id'],dependency_reason='沿用原来源')
    return ex.prepare(parent['bundle_id'],dict(parent['config']),probe_id,node,cores,reason,
        template_id=parent.get('template_snapshot',{}).get('id'),retry_of=key,
        execution_scope=parent.get('execution_scope'),**kwargs)


def resume(key,project_id,*,dispatch=True):
    with exclusive_lock(path(key).with_suffix('.lock')) as owned:
        if not owned:raise ValueError('恢复操作正在派发，请稍后读取')
        op=read(key)
        if op['project_id']!=project_id or op['state'] not in ('CONFIRMED','PAUSED'):raise ValueError('请选择仍在等待的恢复操作')
        op.update(state='CONFIRMED',resumed_at=ex.now());save(op)
        if dispatch:_dispatch(op)
        return read(key)

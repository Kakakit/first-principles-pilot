"""Human-gated, one-coordinate Wannier window rounds on the shared scan worker."""
import copy
import difflib
import importlib.util
import json
import math
import re
import shlex
import uuid
from pathlib import Path

from . import scans, execution as ex, wannier_execution as w90, recycle, servers, branch_bindings
from .storage import ROOT, validate_workspace

SCHEMA = 'fpilot.wannier90-window-scan.v1'
MODE = 'wannier90-window'
TOOL = Path(__file__).resolve().parents[1]/'resources/sets/wannier_windows.py'
_spec = importlib.util.spec_from_file_location('fpilot_window_tool', TOOL)
helper = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(helper)
ORDER = [('outer','dis_win_max'),('outer','dis_win_min'),('frozen','dis_froz_max'),('frozen','dis_froz_min'),('recheck','dis_win_max'),('recheck','dis_win_min')]


def is_window(batch):
    return batch.get('schema') in (SCHEMA,'fpilot.wannier90-window-scan.v2')


def _remote(alias, action, request):
    from .extension_remote import runtimes
    from .pyw90_remote import PYTHON
    python = runtimes().get(alias,{}).get('python') or PYTHON.get(alias)
    if not python:
        raise ValueError('该服务器缺少已有 Python 路径；请在设置 → 结果与扩展中登记远端运行环境')
    command = shlex.quote(python)+' -c '+shlex.quote(TOOL.read_text())+' '+shlex.quote(action)+' '+shlex.quote(scans.packed(request))
    result = ex.ssh(alias, command, timeout=180, stdout_limit=8*1024*1024)
    if result['exit_code'] or result.get('stdout_truncated'):
        raise ValueError('远端窗口检查/摘要失败：'+(result.get('stdout') or result.get('stderr',''))[-500:])
    try: value = json.loads(result['stdout'])
    except ValueError: raise ValueError('窗口检查回执不完整')
    if value.get('error'): raise ValueError(value['error'])
    return value


def _source(bundle, scope):
    recycle.assert_available('wannier-inputs',bundle['id'])
    recycle.assert_available('attempts',bundle['parent_attempt_id'])
    w90._parent(bundle['project'],bundle['node_id'],bundle['parent_attempt_id'],bundle['alias'])
    from .source_files import snapshot
    current = snapshot(bundle['alias'],bundle['source_path'],bundle['source_files'],ex.ssh,mode=bundle.get('verification_mode','full'))
    if current != bundle['source_files']:
        raise ValueError('冻结矩阵来源已变化，请重新审查，未更换来源')
    from .interactive_plots import energy_reference
    reference = energy_reference(bundle['project_id'],bundle['parent_attempt_id'])
    if reference['outcar_sha256'] != bundle['energy_reference']['outcar_sha256'] or not math.isclose(reference['fermi_eV'],bundle['energy_reference']['fermi_eV'],abs_tol=1e-9):
        raise ValueError('冻结费米参考已变化，请重新审查')
    return current


def _selected(batch, case_id):
    recycle.assert_available('scans',batch['id'],batch)
    if batch['state'] != 'COMPLETE' and not (batch.get('schema')=='fpilot.wannier90-window-scan.v2' and batch['state'] in ('PAUSED','BLOCKED')): raise ValueError('本轮尚未结束；请等待后再创建下一轮')
    case = next((c for c in batch['cases'] if c['id']==case_id),None)
    if not case or not case.get('attempt_id'): raise ValueError('请选择本轮已完成的候选')
    record = ex.get(case['attempt_id'])
    recycle.assert_available('attempts',record['id'],record)
    if record['state'] != 'COMPLETED' or not (record.get('evidence') or {}).get('wannier_all_done'):
        raise ValueError('候选缺少程序完成证据，不能作为下一轮基线')
    return case, record


def preview(project, scan_node_id, base_node_id, frozen_id, search, source_draft=None):
    if source_draft is not None:
        if frozen_id:raise ValueError("草稿与旧冻结来源不能同时指定")
        from .wannier_scan_drafts import preview as draft_preview
        return draft_preview(project,scan_node_id,base_node_id,source_draft,search)
    validate_workspace({'activeId':project['id'],'projects':[project]})
    recycle.assert_available('projects',project['id'])
    nodes={n['id']:n for n in project['nodes']}
    if (nodes.get(scan_node_id,{}).get('data',{}).get('kind')!='scan' or
        nodes.get(base_node_id,{}).get('data',{}).get('kind')!='wannier90' or
        [e['source'] for e in project['edges'] if e['target']==scan_node_id] != [base_node_id]):
        raise ValueError('能窗迭代须直接连接一个 Wannier90 基础节点')
    bundle={'id':frozen_id,**w90.configuration(frozen_id)}
    if bundle.get('execution_scope') or (bundle['project_id'],bundle['node_id']) != (project['id'],base_node_id):
        raise ValueError('请选择当前基础节点的独立冻结 Wannier 输入')
    base = helper.validate_base(bundle['win'])
    search=copy.deepcopy(search or {})
    phase, boundary=search.get('phase','outer'),search.get('boundary','dis_win_max')
    if (phase,boundary) not in ORDER: raise ValueError('迭代阶段或边界无效')
    target=search.get('target')
    if not isinstance(target,list) or len(target)!=2: raise ValueError('请填写固定目标能量范围')
    target=[helper.number(x) for x in target]
    if target[0]>=target[1]: raise ValueError('目标能量范围须具有正宽度')
    lower, upper, step=(helper.number(search.get(k)) for k in ('lower','upper','step'))
    if lower>=upper or step<=0: raise ValueError('允许范围须递增，初始步长须为正数')
    initial={k:base['windows'][k]-bundle['energy_reference']['fermi_eV'] for k in w90.WINDOW_TAGS}
    previous=None; campaign=None
    if search.get('previous_batch_id'):
        previous=scans.get(search['previous_batch_id'])
        if not is_window(previous) or (previous['project_id'],previous['scan_node_id'],previous['source_frozen_id']) != (project['id'],scan_node_id,frozen_id):
            raise ValueError('上一轮来源或方案不一致')
        selected,_=_selected(previous,search.get('selected_case_id'))
        old=ORDER.index((previous['search']['phase'],previous['search']['boundary']))
        new=ORDER.index((phase,boundary))
        if new not in (old,old+1) or (new==old and phase=='recheck'):
            raise ValueError('按外窗上下沿、冻窗上下沿、外窗局部复查顺序推进')
        if target!=previous['search']['target']: raise ValueError('同一方案的比较目标范围不能变化')
        initial=selected['relative_windows'];campaign=previous['campaign_id']
        if search.get('refine'):
            if new!=old: raise ValueError('细化须保持当前边界')
            step=previous['search']['step']/2
        if step<1e-8: raise ValueError('细化步长过小，已停止')
    elif (phase,boundary)!=ORDER[0]:
        raise ValueError('首轮从固定小冻窗的外窗上沿开始')
    branch_bindings.check(project,base_node_id,bundle['alias'],scope=scan_node_id)
    identity=_source(bundle,scan_node_id)
    search.update(phase=phase,boundary=boundary,target=target,lower=lower,upper=upper,step=step)
    history=[b for b in scans.history(project['id']) if is_window(b) and b.get('campaign_id')==campaign] if campaign else []
    known={scans.packed(c['relative_windows']):(b,c) for b in reversed(history) for c in b['cases']}
    candidates=[];filtered=[]
    center=initial[boundary]
    for offset in (0,-2,-1,1,2):
        windows={**initial,boundary:round(center+offset*step,10)}
        if not lower<=windows[boundary]<=upper:
            filtered.append({'value':windows[boundary],'reason':'超出允许范围'});continue
        if not windows['dis_win_min']<=windows['dis_froz_min']<windows['dis_froz_max']<=windows['dis_win_max']:
            filtered.append({'value':windows[boundary],'reason':'冻窗未包含于外窗或宽度无效'});continue
        if not windows['dis_win_min']<=target[0]<target[1]<=windows['dis_win_max']:
            filtered.append({'value':windows[boundary],'reason':'外窗未覆盖固定目标范围'});continue
        prior=known.get(scans.packed(windows))
        reuse=None
        if prior:
            old_batch,old_case=prior
            try:
                recycle.assert_available('scans',old_batch['id'],old_batch)
                record=ex.get(old_case['attempt_id']) if old_case.get('attempt_id') else None
                if record:recycle.assert_available('attempts',record['id'],record)
                if record and record['state']=='COMPLETED' and record.get('evidence',{}).get('wannier_all_done'):
                    reuse={'attempt_id':record['id'],'batch_id':old_batch['id'],'case_id':old_case['id']}
                else:
                    filtered.append({'value':windows[boundary],'reason':'此组合已测试或待运行；请处理原 case'});continue
            except (ValueError,FileNotFoundError):
                filtered.append({'value':windows[boundary],'reason':'原结果已回收或清理；不会替换为最新结果'});continue
        absolute={k:v+bundle['energy_reference']['fermi_eV'] for k,v in windows.items()}
        win=w90.edit_win(bundle['win'],{k:str(v) for k,v in windows.items()},bundle['energy_reference']['fermi_eV'])['win']
        candidates.append({'index':len(candidates),'relative_windows':windows,'absolute_windows':absolute,'win':win,
                           'parameters':[{'kind':'window','key':boundary,'value':str(windows[boundary])}],
                           'input_hashes':{bundle['seed']+'.win':w90._sha(win.encode())},
                           'input_diff':{'.win':'\n'.join(difflib.unified_diff(bundle['win'].splitlines(),win.splitlines(),lineterm=''))},
                           'reuse':reuse})
    if candidates:
        request={**{k:base[k] for k in ('num_wann','num_bands','num_kpts')},
                 **{ext:bundle['source_path'].rstrip('/')+'/'+bundle['seed']+'.'+ext for ext in ('eig','amn','mmn')},
                 'candidates':[c['absolute_windows'] for c in candidates]}
        cache=ROOT/'window-checks'/(scans.digest({'source':identity,'parent':bundle['parent_attempt_id'],'path':bundle['source_path'],'alias':bundle['alias'],'request':request})+'.json')
        if cache.is_file():result=json.loads(cache.read_text())
        else:
            result=_remote(bundle['alias'],'check',request)
            if _source(bundle,scan_node_id) != identity:
                raise ValueError('窗口检查期间来源发生变化，请重新审查')
            result.update(project_id=project['id'],source_attempt_id=bundle['parent_attempt_id'],source_frozen_id=frozen_id)
            cache.parent.mkdir(parents=True,exist_ok=True);cache.write_text(scans.packed(result))
        if len(result.get('rows',[]))!=len(candidates):raise ValueError('候选检查回执不完整')
        valid=[]
        for case,row in zip(candidates,result['rows']):
            if row['errors']:
                filtered.append({'value':case['relative_windows'][boundary],'reason':'；'.join(row['errors'])})
            else:valid.append({**case,'index':len(valid),'counts':row,'errors':[],'warnings':row['warnings']})
        candidates=valid
    if candidates and all(c['reuse'] for c in candidates):
        message='没有合法的新组合；可细化范围或选择已有结果推进下一边界'
    else:message='没有合法的新候选；请调整允许范围或步长' if not candidates else ''
    definition={'project':project,'scan_node_id':scan_node_id,'base_node_id':base_node_id,'frozen_id':frozen_id,'search':search,'initial':initial}
    return {'mode':MODE,'project_id':project['id'],'scan_node_id':scan_node_id,'base_node_id':base_node_id,'stage':'wannier90',
            'source_frozen_id':frozen_id,'source':{k:bundle[k] for k in ('alias','seed','parent_attempt_id','energy_reference','spinors')},
            'case_count':len(candidates),'new_case_count':sum(not c['reuse'] for c in candidates),'cases':candidates,'filtered':filtered,
            'ready':bool(candidates),'message':message,'definition_hash':scans.digest(definition),'search':search,
            'campaign_id':campaign,'round':previous['round']+1 if previous else 1,'project_snapshot':project}


def freeze(project, scan_node_id, base_node_id, frozen_id, search, reason, expected_definition_hash=None, source_draft=None):
    if not isinstance(reason,str) or not reason.strip(): raise ValueError('请填写本轮测试目的')
    report=preview(project,scan_node_id,base_node_id,frozen_id,search,source_draft)
    if expected_definition_hash!=report['definition_hash']:raise ValueError('来源或候选定义已变化，请重新预览')
    if not report['ready']:raise ValueError(report['message'])
    original=report.pop('_draft_bundle',None)
    if original is not None:
        from .inputs import put_file
        raw=scans.packed(original).encode();frozen_id=w90._sha(raw)
        put_file(ROOT/'wannier-bundles'/(frozen_id+'.json'),raw)
        report['source_frozen_id']=frozen_id
        original={'id':frozen_id,**original}
    else:original={'id':frozen_id,**w90.configuration(frozen_id)}
    batch_id=uuid.uuid4().hex;cases=[]
    snapshot=copy.deepcopy(original['project'])
    scan=next(n for n in project['nodes'] if n['id']==scan_node_id)
    snapshot['nodes']=[n for n in snapshot['nodes'] if n['id']!=scan_node_id]+[copy.deepcopy(scan)]
    snapshot['edges']=[e for e in snapshot['edges'] if e['target']!=scan_node_id]+[{'id':'window-source-'+scan_node_id,'source':base_node_id,'target':scan_node_id}]
    for c in report['cases']:
        case_id=scans.digest({'batch':batch_id,'windows':c['relative_windows']})[:24]
        frozen={**original,'project':snapshot,'win':c['win'],'win_sha256':w90._sha(c['win'].encode()),'relative_windows':w90.relative_window_values(c['win'],original['energy_reference']['fermi_eV']),
                'absolute_windows':{tag:value for tag,value in w90.parameter_values(c['win']).items() if tag in w90.WINDOW_TAGS and value},'execution_scope':scan_node_id,'scan_batch_id':batch_id,'scan_case_id':case_id,
                'window_source_frozen_id':frozen_id,'input_reason':reason.strip(),'created_at':ex.now()}
        frozen.pop('id',None)
        raw=json.dumps(frozen,ensure_ascii=False,sort_keys=True).encode();key=w90._sha(raw)
        from .inputs import put_file
        put_file(ROOT/'wannier-bundles'/(key+'.json'),raw)
        reuse=c['reuse']
        cases.append({**c,'id':case_id,'bundle_id':key,'state':'COMPLETED' if reuse else 'PENDING',
                      'attempt_id':reuse['attempt_id'] if reuse else None,'attempt_history':[],'retry_count':0})
    batch={k:v for k,v in report.items() if k not in ('cases','project_snapshot','ready','message')}
    batch.update(schema=report.get('schema',SCHEMA),id=batch_id,campaign_id=report['campaign_id'] or batch_id,cases=cases,state='FROZEN' if report['new_case_count'] else 'COMPLETE',created_at=ex.now(),reason=reason.strip())
    batch['case_table_hash']=scans.digest([{k:c[k] for k in ('id','bundle_id','relative_windows','reuse')} for c in cases])
    return scans.save(batch)


def render(config, probe_id, node, cores, reason, template_id=None):
    value=servers.preview(config,probe_id,node,cores,reason,purpose='wannier90')
    script,source=w90._render_script(config,value['profile'],template_id)
    return {**value,'script':ex.with_exit_trap(script),'script_source':source}


def template(config):
    if config.get('template_id'):return config['template_id']
    from .workbench_settings import read
    return read()['defaults'].get(config['alias']+':wannier90')


def resources(batch, config, candidates):
    if config['alias']!=batch['source']['alias']:raise ValueError('窗口任务须使用矩阵来源服务器')
    probe=servers.probe(config,purpose='wannier90')
    from .candidate_pool import validate
    return {'previews':{c['node']:render(config,probe['id'],c['node'],c['cores'],c.get('reason',''),template(config)) for c in validate(candidates)}}


def authorize(batch, project_id, case_table_hash, config, candidates, reviewed, max_parallel):
    from . import candidate_pool
    if batch['project_id']!=project_id or batch['state']!='FROZEN' or batch['case_table_hash']!=case_table_hash:raise ValueError('冻结 case 表已变化')
    recycle.assert_available('scans',batch['id'],batch)
    checked=candidate_pool.approve(candidate_pool.validate(candidates),resources(batch,config,candidates)['previews'],reviewed)
    if type(max_parallel) is not int or not 1<=max_parallel<=len(checked):raise ValueError('同时运行上限须在候选数量内')
    source={'id':batch['source_frozen_id'],**w90.configuration(batch['source_frozen_id'])}
    _source(source,batch['scan_node_id'])
    branch_bindings.check(source['project'],batch['base_node_id'],config['alias'],scope=batch['scan_node_id'])
    from .workbench_settings import snapshot
    batch.update(state='ACTIVE',authorization={'config':config,'candidates':checked,'max_parallel':max_parallel,'reason':'',
                    'template_id':template(config),'settings_snapshot':snapshot(config['alias'],'wannier90',config,template(config)),'at':ex.now(),'case_table_hash':case_table_hash})
    return scans.save(batch)


def prepare_case(batch, case, auth, choice, probe, attempt_id):
    bundle=w90.configuration(case['bundle_id'])
    return w90.prepare(bundle['project'],batch['base_node_id'],bundle['parent_attempt_id'],auth['config'],probe['id'],choice['node'],choice['cores'],choice['reason'],
                       template_id=auth['template_id'],frozen_id=case['bundle_id'],attempt_id=attempt_id,execution_scope=batch['scan_node_id'],
                       scan_batch_id=batch['id'],scan_case_id=case['id'],scan_case_table_hash=batch['case_table_hash'])


def results(batch, project_id):
    if batch['project_id']!=project_id:raise ValueError('批次不属于项目')
    recycle.assert_available('scans',batch['id'],batch)
    rows=[]
    for case in batch['cases']:
        row={'case_id':case['id'],'state':case['state'],'metrics':None,'error':None}
        if case.get('attempt_id'):
            try:
                rec=ex.get(case['attempt_id']);recycle.assert_available('attempts',rec['id'],rec)
                row['state']=rec['state']
                if rec['state']=='COMPLETED':
                    bundle=w90._bundle(rec)
                    key=scans.digest({'attempt':rec['id'],'bundle':rec['bundle_id'],'updated':rec.get('updated_at'),'evidence':rec.get('evidence')})
                    cache=ROOT/'window-summaries'/rec['id']/(key+'.json')
                    path=ex.attempt_path(rec)['shell'].rstrip('/')+'/'+bundle['seed']+'.wout'
                    metrics=_remote(rec['config']['alias'],'summary',{'wout':path,'win':bundle['win'],'expected_win_sha256':bundle['win_sha256']})
                    cache.parent.mkdir(parents=True,exist_ok=True);cache.write_text(scans.packed(metrics))
                    row['metrics']=metrics
            except (ValueError,KeyError,OSError) as exc:row['error']=str(exc)
        rows.append(row)
    return {'rows':rows,'comparison':batch.get('comparison')}


def adopt(batch, project_id, case_id):
    if batch['project_id']!=project_id:raise ValueError('批次不属于项目')
    case,record=_selected(batch,case_id)
    bundle=w90.configuration(case['bundle_id'])
    recycle.assert_available('wannier-inputs',case['bundle_id'])
    _source({'id':case['bundle_id'],**bundle},batch['scan_node_id'])
    return {'node_id':batch['base_node_id'],'source_frozen_id':batch['source_frozen_id'],'parent_id':bundle['parent_attempt_id'],
            'alias':bundle['alias'],'seed':bundle['seed'],'win':bundle['win'],'relative_windows':case['relative_windows'],'parameters':w90.parameter_values(bundle['win']),
            'energy_reference':bundle['energy_reference'],'band_path':bundle.get('band_path'),'attempt_id':record['id'],
            'input_reason':'采用能窗迭代第 '+str(batch['round'])+' 轮结果（需重新审查冻结）'}


def compare(batch, project_id, case_ids, band_attempt_id, dft_band_offset):
    from . import wannier_compare
    from .builtin_analysis.comparison_metrics import measure_curves
    if not is_window(batch) or batch['project_id']!=project_id:raise ValueError('批次不属于当前项目')
    recycle.assert_available('scans',batch['id'],batch)
    if type(dft_band_offset) is not int or dft_band_offset<0:raise ValueError('请指定本轮共同的 DFT 起始能带（从 1 计数）')
    if not 1<=len(case_ids)<=64 or len(set(case_ids))!=len(case_ids):raise ValueError('请选择 1–64 个不同 case')
    if not batch['search'].get('target'):raise ValueError('请在本批配置中填写可选的比较能量范围后再创建测试；已有结果可直接叠图')
    contract={'project_id':project_id,'band_attempt_id':band_attempt_id,'dft_band_offset':dft_band_offset,'target':batch['search']['target']}
    contract_file=ROOT/'window-comparisons'/(batch['campaign_id']+'.json')
    if contract_file.exists() and json.loads(contract_file.read_text()) != contract:
        raise ValueError('同一能窗方案已绑定 DFT 参考、目标范围与共同能带对应关系，不能更换')
    rows=[]
    for case_id in case_ids:
        case=next((c for c in batch['cases'] if c['id']==case_id),None)
        if not case or not case.get('attempt_id'):raise ValueError('请选择本批已完成的 case')
        rec=ex.get(case['attempt_id']);recycle.assert_available('attempts',rec['id'],rec)
        if rec['state']!='COMPLETED' or not rec.get('evidence',{}).get('wannier_all_done'):raise ValueError('case 缺少程序完成证据')
        # run() validates exact SCF lineage, source host/spin, full path and points.
        comparison=wannier_compare.run(project_id,rec['id'],band_attempt_id,*batch['search']['target'],diagnostic=False,dft_band_start=dft_band_offset+1)
        directory=wannier_compare.ROOT_COMPARE/comparison['id']
        request={'curves_csv':str(directory/'curves.csv'),'target_relative_eV':batch['search']['target'],'dft_band_offset':dft_band_offset}
        from .inputs import put_file
        request_file=directory/'window-metric-request.json';put_file(request_file,scans.packed(request).encode())
        metric=measure_curves(request)
        if metric.get('status')!='MEASURED':raise ValueError('误差分析回执不完整')
        rows.append({'case_id':case_id,'attempt_id':rec['id'],'comparison_id':comparison['id'],'metrics':metric})
    if len({r['metrics']['reference_values'] for r in rows})!=1:raise ValueError('共同参考取样数不一致，不排序')
    value={'rows':rows,**contract,'scientific_validation':'NOT_PERFORMED','created_at':ex.now()}
    contract_file.parent.mkdir(parents=True,exist_ok=True)
    if not contract_file.exists():
        from .inputs import put_file
        put_file(contract_file,scans.packed(contract).encode())
    batch['comparison']=value
    scans.save(batch)
    return value

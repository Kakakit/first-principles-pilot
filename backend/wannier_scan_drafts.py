"""One-parameter window tests from an audited current draft, without a prior freeze."""
import copy
import difflib
from . import wannier_execution as w90, scans, recycle, branch_bindings
from .storage import validate_workspace

SCHEMA = 'fpilot.wannier90-window-scan.v2'


def values(search):
    if 'values' in search:
        raw=search['values']
        if not isinstance(raw,list) or not 1<=len(raw)<=64:raise ValueError('请提供 1–64 个窗口值')
        result=[]
        for value in raw:
            number=w90.win_contract._format_scalar(value)
            try:n=float(number)
            except ValueError:raise ValueError('窗口值须为有限数值') from None
            if not __import__('math').isfinite(n):raise ValueError('窗口值须为有限数值')
            if n not in result:result.append(n)
        return result
    start,end,step=(scans.decimal(str(search.get(k,''))) for k in ('start','end','step'))
    if step==0 or (end-start)*step<0:raise ValueError('窗口步长方向错误')
    result=[];current=start
    while (current<=end if step>0 else current>=end) and len(result)<=64:
        result.append(float(current));current+=step
    if not result or len(result)>64:raise ValueError('每批最多 64 个窗口值')
    return result


def preview(project,scan_node_id,base_node_id,draft,search):
    from . import wannier_scans as windows
    validate_workspace({'activeId':project['id'],'projects':[project]})
    recycle.assert_available('projects',project['id'])
    nodes={n['id']:n for n in project['nodes']}
    if nodes.get(scan_node_id,{}).get('data',{}).get('kind')!='scan' or nodes.get(base_node_id,{}).get('data',{}).get('kind')!='wannier90' or [e['source'] for e in project['edges'] if e['target']==scan_node_id]!=[base_node_id]:raise ValueError('参数迭代须连接一个 Wannier90 节点')
    if not isinstance(draft,dict) or draft.get('pending'):raise ValueError('请先应用或解析基础节点的当前参数草稿')
    fields={k:draft.get(k) for k in ('parent_id','alias','win','seed','energy_reference','band_path')}
    bundle=w90.configuration_draft(project,base_node_id,input_reason=draft.get('input_reason') or '窗口测试草稿基线',**fields)
    base=windows.helper.validate_base(bundle['win'],allow_defaults=True)
    search=copy.deepcopy(search or {})
    boundary=search.get('boundary')
    if boundary not in w90.WINDOW_TAGS:raise ValueError('每批只选择一个窗口边界')
    if boundary=='dis_froz_min' and 'dis_froz_max' not in base['windows']:raise ValueError('先设置冻结窗上界，才能测试冻结窗下界')
    axis=values(search);fermi=bundle['energy_reference']['fermi_eV']
    branch_bindings.check(project,base_node_id,bundle['alias'],scope=scan_node_id)
    cases=[]
    for value in axis:
        win=w90.edit_win(bundle['win'],{boundary:str(value)},fermi)['win']
        absolute={k:float(v) for k,v in w90.parameter_values(win).items() if k in w90.WINDOW_TAGS and v}
        cases.append({'index':len(cases),'value':value,'win':win,'relative_windows':{k:v-fermi for k,v in absolute.items()},'absolute_windows':absolute,
                      'parameters':[{'kind':'window','key':boundary,'value':str(value)}],'input_hashes':{bundle['seed']+'.win':w90._sha(win.encode())},
                      'input_diff':{'.win':'\n'.join(difflib.unified_diff(bundle['win'].splitlines(),win.splitlines(),lineterm=''))},'reuse':None})
    request={**{k:base[k] for k in ('num_wann','num_bands','num_kpts')},**{ext:bundle['source_path'].rstrip('/')+'/'+bundle['seed']+'.'+ext for ext in ('eig','amn','mmn')},'allow_defaults':True,'candidates':[c['absolute_windows'] for c in cases]}
    result=windows._remote(bundle['alias'],'check',request)
    from .source_files import snapshot
    if snapshot(bundle['alias'],bundle['source_path'],bundle['source_files'],w90.ex.ssh,mode=bundle.get('verification_mode','full'))!=bundle['source_files']:raise ValueError('检查期间来源已变化，请重新预览')
    # Recheck exact source and Fermi identity after the read-only matrix pass.
    original_id=scans.digest({k:v for k,v in bundle.items() if k not in ('created_at','project')})
    checked=w90.configuration_draft(project,base_node_id,input_reason=bundle['input_reason'],**fields)
    if original_id!=scans.digest({k:v for k,v in checked.items() if k not in ('created_at','project')}):raise ValueError('检查期间来源已变化，请重新预览')
    if len(result.get('rows',[]))!=len(cases):raise ValueError('窗口态数回执不完整')
    for case,row in zip(cases,result['rows']):
        effective=row['absolute_windows']
        case.update(counts=row,errors=row['errors'],warnings=row['warnings'],absolute_windows=effective,relative_windows={k:v-fermi if v is not None else None for k,v in effective.items()})
    # A plotting range is optional and does not constrain scientific eligibility.
    target=search.get('target')
    if target is not None:
        import math
        if not isinstance(target,list) or len(target)!=2 or not all(type(v) in (int,float) and math.isfinite(v) for v in target) or target[0]>=target[1]:raise ValueError('比较能量范围须为有限递增区间')
    search={'boundary':boundary,'values':axis,'target':target}
    definition={'source':original_id,'project':project,'scan_node_id':scan_node_id,'base_node_id':base_node_id,'search':search}
    return {'mode':windows.MODE,'schema':SCHEMA,'project_id':project['id'],'scan_node_id':scan_node_id,'base_node_id':base_node_id,'stage':'wannier90',
            'source_frozen_id':None,'source':{k:bundle[k] for k in ('alias','seed','parent_attempt_id','energy_reference','spinors')},
            'case_count':len(cases),'new_case_count':len(cases),'cases':cases,'filtered':[],
            'ready':all(not c['errors'] for c in cases),'message':'请调整或移除有错误的候选' if any(c['errors'] for c in cases) else '',
            'definition_hash':scans.digest(definition),'search':search,'campaign_id':None,'round':1,'_draft_bundle':bundle}

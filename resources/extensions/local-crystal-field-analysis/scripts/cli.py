#!/usr/bin/env python3
"""Workstation/SSH adapter, source IDs are paired explicitly; no scheduler calls."""
import csv,hashlib,json,re
from pathlib import Path
from protocol_cli import main,dump,artifact

def selected(request,role,source,required=True):
    matches=[i for i in request['inputs'].get(role,[]) if i['source_id']==source['id']]
    if len(matches)!=1:
        if not matches and not required:return None
        raise ValueError('同来源需选择一个 '+role)
    item=matches[0];path=Path(item['path'])
    if not path.is_file() or path.is_symlink() or path.stat().st_size!=item['size_bytes']:raise ValueError('冻结输入文件无效或大小变化：'+item['name'])
    return path

def collect(request,parse=True):
    from onsite_core import analyze_files
    from pdos_core import analyze_pdos
    from occupation_core import analyze_occupation
    from polyhedron_types import parse_types
    parse_types(request['options'])
    outputs=[]
    stages={s.get('stage') for s in request['sources']}
    if stages<={'dos','scf'}:mode='vasp'
    elif stages=={'wannier90'}:mode='hamiltonian'
    else:raise ValueError('一次分析请选择同一种模式的 DOS/SCF 或 Wannier90 来源')
    output=Path(request['output_dir']).resolve()
    for items in request['inputs'].values():
        for item in items:
            parent=Path(item['path']).resolve().parent
            if output==parent or parent in output.parents:raise ValueError('输出目录不得覆盖或位于原输入目录内')
    for source in request['sources']:
        if source.get('state')!='COMPLETED':raise ValueError('请选择已完成的来源')
        if mode=='vasp':
            dos=selected(request,'doscar',source,False);structure=selected(request,'structure',source);outcar=selected(request,'outcar',source)
            choice=request['options'].get('vasp_quantity','auto')
            if choice not in ('auto','occupation','pdos'):raise ValueError('VASP分析内容选项无效')
            has_matrix=False
            if choice=='auto' and (source.get('stage')=='scf' or dos is None):
                with outcar.open(errors='replace') as stream:has_matrix=any('onsite density matrix' in line.lower() for line in stream)
            chosen='occupation' if choice=='occupation' or choice=='auto' and has_matrix else 'pdos'
            if chosen=='pdos' and dos is None:raise ValueError('当前来源缺少DOSCAR或最终占据矩阵，请选择对应来源/分析内容；不自动重跑')
            if parse:
                data=analyze_occupation(structure,outcar,request['options']) if chosen=='occupation' else analyze_pdos(dos,structure,outcar)
                if chosen=='pdos' and request['options'].get('polyhedron_types'):
                    from polyhedron_types import parse_types
                    specs=parse_types(request['options']);symbols={a['element'] for a in data['atoms']}
                    if any(c not in symbols or v['ligand'] not in symbols for c,v in specs.items()):raise ValueError('所选多面体的中心或配体不在当前结构中')
                    data['atoms']=[a for a in data['atoms'] if a['element'] in specs]
                    if not data['atoms']:raise ValueError('所选多面体中心在PDOS中无对应原子')
                    data['warnings'].append('普通PDOS不包含完整轨道交叉项，不能凭多面体名称把五条谱旋转为局域坐标。')
                data['source']=source;outputs.append(data)
            else:
                if chosen=='occupation':analyze_occupation(structure,outcar,request['options'])
                outputs.append({'mode':chosen})
            continue
        hr=selected(request,'hamiltonian',source);win=selected(request,'wannier-input',source);wout=selected(request,'wannier-output',source);centres=selected(request,'centres',source,False)
        names={role:next((i['name'] for i in request['inputs'].get(role,[]) if i['source_id']==source['id']),None) for role in ['hamiltonian','wannier-input','wannier-output','centres']}
        seed=names['hamiltonian'][:-7] if names['hamiltonian'].endswith('_hr.dat') else ''
        if not seed or names['wannier-input']!=seed+'.win' or names['wannier-output']!=seed+'.wout' or centres and names['centres']!=seed+'_centres.xyz':raise ValueError('hr/win/wout/centres 前缀必须属于同一模型')
        frozen=source.get('input_summary') or {}
        if frozen.get('seed') and frozen['seed']!=seed:raise ValueError('文件通道与冻结 Wannier seed 不符')
        if source.get('state')!='COMPLETED' or source.get('stage')!='wannier90':raise ValueError('请选择已完成的 Wannier90 来源')
        if parse:
            data=analyze_files(hr,win,wout,centres,request['options']);data.update(source=source,seed=seed);outputs.append(data)
    if mode=='vasp':
        modes={m['mode'] for m in outputs}
        if len(modes)!=1:raise ValueError('所选VASP来源进入不同分析内容，请分别分析，或明确选择统一内容')
        mode=next(iter(modes))
    if not 1<=len(request['sources'])<=64:raise ValueError('需要 1–64 个来源')
    return {'schema':'fpilot.occupation-bundle.v1' if mode=='occupation' else 'fpilot.pdos-bundle.v1' if mode=='pdos' else 'fpilot.onsite-bundle.v2','mode':mode,'models':outputs,'comparison':'分来源展示；PDOS 重心与 Wannier 局域能级含义不同，不做自动跨方法相减或自旋平均'}

def check(request):
    data=collect(request,parse=False);return f"{len(request['sources'])} 个 {data['mode']} 来源的冻结文件可用；运行时解析数值，物理有效性需另行审核"

def analyze(request):
    data=collect(request);out=Path(request['output_dir'])
    if data['mode']=='occupation':
        dump(out/'occupation.json',data)
        rows=[{'source':m['source'].get('name','来源'),'site':a['label'],'polyhedron':a['polyhedron'],'spin':sp['label'],'n_d':sp['global']['trace']} for m in data['models'] for a in m['atoms'] for sp in a['spins']]
        columns=[{'key':k,'label':label,'unit':u} for k,label,u in [('source','来源',''),('site','原子',''),('polyhedron','多面体',''),('spin','自旋',''),('n_d','d壳占据','电子')]]
        with (out/'occupation.csv').open('w',newline='',encoding='utf-8-sig') as f:
            writer=csv.DictWriter(f,fieldnames=[c['key'] for c in columns]);writer.writeheader();writer.writerows(rows)
        return {'summary':f"已读取 {len(rows)//3} 个位点的最终VASP占据矩阵；占据不是晶场能量",'scientific_validation':'INSUFFICIENT','warnings':list(dict.fromkeys(w for m in data['models'] for w in m['warnings'])),'tables':[{'title':'最终 d 壳占据','columns':columns,'rows':rows}],'artifacts':[artifact(out/'occupation.json','完整占据矩阵、局域配位架与来源证据'),artifact(out/'occupation.csv','逐位点占据汇总','text/csv')]}
    if data['mode']=='pdos':
        dump(out/'pdos.json',data)
        return {'summary':f"已解析 {len(data['models'])} 个逐原子 PDOS 来源；可直接浏览并调整重心积分区间",'scientific_validation':'INSUFFICIENT','warnings':list(dict.fromkeys(w for m in data['models'] for w in m['warnings'])),'artifacts':[artifact(out/'pdos.json','冻结逐原子 PDOS、全范围重心及来源证据')]}
    dump(out/'onsite.json',data)
    rows=[]
    for model in data['models']:
        for r in model['reports']:
            candidate=r['coordination_candidate'];part=candidate['metrics'].get('cubic_partition') if candidate else None
            delta=part['eg_minus_t2g_ev']*(-1 if candidate['coordination']==4 else 1) if part else None
            rows.append({'model':model['source'].get('name',model['seed']),'site':r['label'],'wf':','.join(map(str,r['wf_indices'])),'span':r['raw']['span_ev'],'centroid':r['raw']['centroid_ev'],'delta_candidate':delta,'basis':r['basis_status']})
    columns=[{'key':k,'label':label,'unit':u} for k,label,u in [('model','模型',''),('site','原子/子壳',''),('wf','WF 编号（1 起）',''),('centroid','投影子壳重心','eV'),('span','投影子壳本征值范围','eV'),('delta_candidate','条件性的局域 d 分组差','eV'),('basis','轨道身份','')]]
    with (out/'onsite.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=[c['key'] for c in columns]);writer.writeheader();writer.writerows(rows)
    return {'summary':f"已分析 {len(data['models'])} 个模型、{len(rows)} 个 s/p/d 子壳；物理晶场解释尚未验证",'scientific_validation':'INSUFFICIENT','warnings':list(dict.fromkeys(w for m in data['models'] for w in m['warnings'])),'tables':[{'title':'原始 onsite 子壳与条件分组候选','columns':columns,'rows':rows}], 'artifacts':[artifact(out/'onsite.json','完整复矩阵、WF 映射、原文与过程'),artifact(out/'onsite.csv','子壳汇总 CSV','text/csv')]}
if __name__=='__main__':raise SystemExit(main(check,analyze))

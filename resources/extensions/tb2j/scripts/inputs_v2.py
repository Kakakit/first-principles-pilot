"""TB2J input adapter. Reads explicit files; never reads workbench state."""
import json
import math
import re
import shutil
from pathlib import Path
import numpy as np
from ase import Atoms
from ase.io import write
from ase.units import Bohr


def finite(value):
    if isinstance(value,bool):raise ValueError('数值不能为 boolean')
    result=float(value)
    if not math.isfinite(result):raise ValueError('数值必须有限')
    return result


def truth(value):
    if isinstance(value,bool):return value
    text=str(value).strip().lower()
    if text in ('true','.true.','t','1'):return True
    if text in ('false','.false.','f','0','none'):return False
    raise ValueError('spinors 值无效')


def win_model(path):
    text='\n'.join(re.split(r'[!#]',line,1)[0].strip() for line in path.read_text().splitlines())
    tags=dict((k.lower(),v.strip()) for k,v in re.findall(r'^\s*(\w+)\s*[:=]\s*(.*?)\s*$',text,re.M))
    n=int(tags.get('num_wann','0'));spinors=truth(tags.get('spinors','false'))
    if n<1 or spinors and n%2:raise ValueError('num_wann 必须为正整数，spinor 基底数须为偶数')
    def block(name):
        match=re.search(r'^begin\s+'+name+r'\s*$(.*?)^end\s+'+name+r'\s*$',text,re.M|re.S|re.I)
        if not match:raise ValueError('.win 缺少 '+name)
        return [line.split() for line in match.group(1).splitlines() if line.strip()]
    cell=block('unit_cell_cart');factor=1
    if len(cell[0])==1:
        unit=cell.pop(0)[0].lower()
        if unit not in ('ang','angstrom','bohr'):raise ValueError('晶胞单位不受支持')
        factor=Bohr if unit=='bohr' else 1
    lattice=np.array([[finite(v) for v in row] for row in cell])*factor
    if lattice.shape!=(3,3) or abs(np.linalg.det(lattice))<1e-10:raise ValueError('晶胞无效')
    fractional=bool(re.search(r'^begin\s+atoms_frac\s*$',text,re.M|re.I));rows=block('atoms_frac' if fractional else 'atoms_cart');factor=1
    if not fractional and len(rows[0])==1:
        unit=rows.pop(0)[0].lower()
        if unit not in ('ang','angstrom','bohr'):raise ValueError('原子坐标单位不受支持')
        factor=Bohr if unit=='bohr' else 1
    symbols=[re.sub(r'\d+$','',r[0]) for r in rows]
    positions=np.array([[finite(v) for v in r[1:]] for r in rows])*factor
    if not symbols or positions.shape!=(len(symbols),3):raise ValueError('原子坐标无效')
    atoms=Atoms(symbols,cell=lattice,pbc=True,**({'scaled_positions':positions} if fractional else {'positions':positions}))
    return {'num_wann':n,'spinors':spinors,'atoms':atoms,'tags':tags}


def item(request,role,source,required=True):
    matches=[i for i in request['inputs'].get(role,[]) if i['source_id']==source['id']]
    if len(matches)>1:raise ValueError('同来源输入角色重复：'+role)
    if not matches:
        if required:raise ValueError('缺少 '+role+'：'+source['id'])
        return None
    result=matches[0];path=Path(result['path'])
    if not path.is_file() or path.is_symlink() or path.stat().st_size!=result['size_bytes'] or path.stat().st_size==0:raise ValueError('输入文件无效或大小已变化：'+result['name'])
    return result


def centres_check(path,model):
    lines=path.read_text().splitlines();count=int(lines[0]);rows=[line.split() for line in lines[2:] if line.strip()]
    if count!=len(rows):raise ValueError('centres.xyz 行数不符')
    xs=[];symbols=[];positions=[]
    for row in rows:
        if len(row)!=4:raise ValueError('centres.xyz 坐标格式无效')
        pos=[finite(v) for v in row[1:]]
        if row[0]=='X':xs.append(pos)
        else:symbols.append(row[0]);positions.append(pos)
    if len(xs)!=model['num_wann']:raise ValueError('centres.xyz 中心数与 num_wann 不符')
    if symbols:
        atoms=model['atoms']
        if symbols!=atoms.get_chemical_symbols():raise ValueError('centres.xyz 原子顺序与 .win 不符')
        delta=np.array(positions)-atoms.positions;scaled=delta@np.linalg.inv(atoms.cell.array);scaled-=np.rint(scaled)
        if not np.allclose(scaled@atoms.cell.array,0,atol=1e-5):raise ValueError('centres.xyz 原子位置与 .win 不符')


def inspect(request):
    options=request['options'];sources=request['sources'];models=[]
    if not 1<=len(sources)<=2 or len({s['id'] for s in sources})!=len(sources):raise ValueError('需要一个 SOC 或两个不同的共线来源')
    for source in sources:
        if source.get('stage')!='wannier90' or source.get('state')!='COMPLETED' or source.get('evidence',{}).get('wannier_all_done') is not True:raise ValueError('Wannier 来源缺少完成证据')
        metadata=source.get('input_summary') or {};seed=metadata.get('seed')
        if seed not in ('wannier90','wannier90.1','wannier90.2'):raise ValueError('来源缺少冻结 Wannier 通道')
        if not metadata.get('parent_attempt_id'):raise ValueError('来源缺少冻结 DFT 父 attempt')
        win=item(request,'wannier-input',source);wout=item(request,'wannier-output',source);ham=item(request,'hamiltonian',source);centres=item(request,'centres',source,False)
        if win['name'] not in (seed+'.win','wannier90.win') or wout['name']!=seed+'.wout' or ham['name'] not in (seed+'_hr.dat',seed+'_tb.dat'):raise ValueError('文件前缀与冻结 Wannier 通道不符')
        model=win_model(Path(win['path']));model.update(source=source,seed=seed,win=win,wout=wout,ham=ham,centres=centres)
        if metadata.get('spinors') is not None and truth(metadata['spinors'])!=model['spinors']:raise ValueError('冻结自旋模式与 .win 不符')
        if metadata.get('num_wann') is not None and int(metadata['num_wann'])!=model['num_wann']:raise ValueError('冻结 num_wann 与 .win 不符')
        text=Path(wout['path']).read_text(errors='replace')
        if not re.search(r'All done',text,re.I) or not re.search(r'Final State',text,re.I):raise ValueError('.wout 缺少 All done / Final State 结束证据')
        match=re.search(r'Number of Wannier Functions\s*[:=]?\s*(\d+)',text,re.I)
        if match and int(match.group(1))!=model['num_wann']:raise ValueError('.wout num_wann 与 .win 不符')
        with Path(ham['path']).open() as stream:
            next(stream)
            if ham['name'].endswith('_tb.dat'):
                cell=np.array([[finite(v) for v in next(stream).split()] for _ in range(3)])
                if cell.shape!=(3,3) or not np.allclose(cell,model['atoms'].cell.array,atol=1e-5):raise ValueError('_tb.dat 晶胞与 .win 不符')
            count=int(next(stream));nr=int(next(stream))
            if count!=model['num_wann'] or nr<1:raise ValueError('Hamiltonian num_wann / R 数无效')
        if ham['name'].endswith('_hr.dat') and centres is None:raise ValueError('hr 需要同前缀 centres.xyz；旧 attempt 缺产物，请选择已有 tb.dat 或另行准备新 attempt（write_xyz=true）')
        if centres:
            if centres['name']!=seed+'_centres.xyz':raise ValueError('centres 文件前缀不符')
            centres_check(Path(centres['path']),model)
        models.append(model)
    mode='spinor' if len(models)==1 else 'collinear'
    if options.get('mode','auto') not in ('auto',mode):raise ValueError('模式与来源数量不符')
    if any(m['spinors']!=(mode=='spinor') for m in models):raise ValueError('来源自旋模式与分析模式不符')
    if mode=='spinor':
        if options.get('groupby') not in ('spin','orbital') or options.get('basis_confirmed') is not True:raise ValueError('SOC 必须选择 groupby 并确认 spinor 基底顺序')
    else:
        if {m['seed'] for m in models}!={'wannier90.1','wannier90.2'}:raise ValueError('共线需 .1 上自旋与 .2 下自旋两个通道')
        if len({s.get('alias') for s in sources})!=1 or not sources[0].get('alias') or len({s['input_summary']['parent_attempt_id'] for s in sources})!=1:raise ValueError('共线来源必须同服务器、同 DFT 父 attempt')
        a,b=models
        if a['num_wann']!=b['num_wann'] or a['atoms'].get_chemical_symbols()!=b['atoms'].get_chemical_symbols() or not np.allclose(a['atoms'].cell.array,b['atoms'].cell.array,atol=1e-6) or not np.allclose(a['atoms'].positions,b['atoms'].positions,atol=1e-6):raise ValueError('共线两通道晶胞、原子顺序或 num_wann 不兼容')
        models.sort(key=lambda m:m['seed'])
    atoms=models[0]['atoms'];elements=options.get('magnetic_elements') or [];indices=options.get('magnetic_atoms') or []
    if bool(elements)==bool(indices):raise ValueError('磁性元素与原子序号必须二选一填写')
    if elements and (len(set(elements))!=len(elements) or any(s not in atoms.get_chemical_symbols() for s in elements)):raise ValueError('磁性元素不存在或重复')
    if indices and (len(set(indices))!=len(indices) or any(type(i) is not int or not 1<=i<=len(atoms) for i in indices)):raise ValueError('磁性原子序号必须为有效 1-based 序号')
    mesh=options['kmesh']
    if len(mesh)!=3 or any(type(i) is not int or i<1 for i in mesh):raise ValueError('k 网格须为三个正整数')
    if finite(options['emin_ev'])>=finite(options['emax_ev']):raise ValueError('积分下界须小于上界（相对 E_F，eV）')
    for key in ('nz','nproc'):
        if type(options[key]) is not int or options[key]<1:raise ValueError(key+' 须为正整数')
    if options.get('rcut_angstrom') is not None and finite(options['rcut_angstrom'])<=0:raise ValueError('截断距离须大于 0 Å')
    refs=[s['input_summary'].get('energy_reference') or {} for s in sources]
    if options.get('energy_source','frozen')=='frozen':
        if any('fermi_eV' not in r or r.get('attempt_id')!=s['input_summary']['parent_attempt_id'] or r.get('host',s.get('alias'))!=s.get('alias') for r,s in zip(refs,sources)):raise ValueError('缺少与该 DFT 父来源匹配的冻结费米能；请选择 manual，填写数值与来源说明')
        values=[finite(r['fermi_eV']) for r in refs]
        if max(values)-min(values)>1e-8:raise ValueError('两通道冻结费米能参考不一致')
        hashes=[r.get('outcar_sha256') for r in refs]
        if mode=='collinear' and all(hashes) and len(set(hashes))!=1:raise ValueError('两通道冻结 OUTCAR 能量参考不一致')
        energy=values[0];energy_ref={'mode':'frozen','references':refs}
    elif options.get('energy_source')=='manual':
        if not isinstance(options.get('energy_note'),str) or not options['energy_note'].strip():raise ValueError('手动费米能需填写来源说明')
        energy=finite(options['efermi_ev']);energy_ref={'mode':'manual','fermi_eV':energy,'note':options['energy_note'].strip()}
    else:raise ValueError('费米能来源无效')
    mapping=options.get('orbital_mapping') or [];seen=set()
    if mapping and not options.get('orb_decomposition'):raise ValueError('轨道映射需要开启轨道分解')
    for row in mapping:
        pair=(row['atom_index'],row['orbital_index'])
        if any(type(x) is not int or x<1 for x in pair) or pair[0]>len(atoms) or pair[1]>models[0]['num_wann'] or pair in seen or not row['label'].strip():raise ValueError('轨道映射含重复、越界或空标签')
        seen.add(pair)
    from wf_report import read_report,bind_report
    report=read_report(options.get('wf_identity_report'))
    identities=[]
    if report:
        if mode!='collinear':raise ValueError('最终WF身份衔接首版只支持无SOC共线')
        for model in models:
            if not model['ham']['name'].endswith('_hr.dat'):raise ValueError('WF身份报告首版绑定hr输入，请选择同一模型的hr与centres')
            bound=bind_report(report,model['ham']['path'],model['win']['path'],model['seed'])
            if bound['channel']!=model['seed']:raise ValueError('身份报告自旋通道与来源不符')
            identities.append(bound)
    return {'models':models,'mode':mode,'atoms':atoms,'efermi_ev':energy,'energy_reference':energy_ref,'mapping':mapping,'wf_identity_models':identities,'wf_spin_alignment':report.get('spin_alignment') if report else None}


def normalize(checked,output):
    model_dir=output/'model';model_dir.mkdir() # Fail instead of replacing an earlier run.
    write(str(model_dir/'POSCAR'),checked['atoms'],format='vasp',vasp5=True,sort=False,direct=True)
    prefixes=[]
    for i,model in enumerate(checked['models']):
        prefix='spinor' if checked['mode']=='spinor' else ('up' if i==0 else 'down');prefixes.append(prefix)
        for role,suffix in [('win','.win'),('wout','.wout'),('ham','_hr.dat' if model['ham']['name'].endswith('_hr.dat') else '_tb.dat'),('centres','_centres.xyz')]:
            if model.get(role):shutil.copyfile(model[role]['path'],model_dir/(prefix+suffix))
    return model_dir,prefixes


def argv(checked,options,model_dir,prefixes,raw):
    import sys
    args=[sys.executable,'-m','TB2J.scripts.wann2J','--path',str(model_dir),'--posfile','POSCAR','--efermi',str(checked['efermi_ev']),'--kmesh']+[str(i) for i in options['kmesh']]
    if options.get('magnetic_elements'):args+=['--elements']+options['magnetic_elements']
    else:args+=['--index_magnetic_atoms']+[str(i) for i in options['magnetic_atoms']]
    if checked['mode']=='spinor':args+=['--spinor','--groupby',options['groupby'],'--prefix_spinor',prefixes[0]]
    else:args+=['--prefix_up',prefixes[0],'--prefix_down',prefixes[1]]
    for field,flag in [('emin_ev','--emin'),('emax_ev','--emax'),('nz','--nz'),('nproc','--np'),('rcut_angstrom','--rcut'),('description','--description')]:
        if options.get(field) is not None:args+=[flag,str(options[field])]
    if options.get('orb_decomposition'):args+=['--orb_decomposition']
    return args+['--output_path',str(raw)]

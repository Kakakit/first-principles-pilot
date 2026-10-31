"""Generic s/p/d onsite diagnostics. Package-owned, no workbench imports."""
import hashlib,itertools,json,math,re
from pathlib import Path
import numpy as np
from pymatgen.core import Structure, Lattice
from polyhedron_types import parse_types,label as polyhedron_label
from calc_local_crystal_field import d_rotation,kabsch_frame,OCT_REFS,TET_REFS

ORBITALS={0:['s'],1:['pz','px','py'],2:['dz2','dxz','dyz','dx2-y2','dxy']}
BOHR=0.529177210903

def finite(token):
    value=float(str(token).replace('D','E').replace('d','e'))
    if not math.isfinite(value):raise ValueError('输入含非有限数值')
    return value

def matrix_json(h):return {'real':h.real.tolist(),'imag':h.imag.tolist(),'unit':'eV'}

def read_onsite(path):
    """Stream-validate every R block, retain only R=0; never build H(k)."""
    digest=hashlib.sha256();line=0
    with Path(path).open('rb') as stream:
        def read():
            nonlocal line
            raw=stream.readline();line+=1;digest.update(raw)
            if not raw:raise ValueError('hr.dat 截断：第 '+str(line)+' 行')
            return raw.decode('utf-8').strip()
        title=read();n=int(read());nr=int(read())
        if not 1<=n<=512 or not 1<=nr<=100000:raise ValueError('hr.dat 维度超出 1–512 WF / 1–100000 R')
        weights=[]
        while len(weights)<nr:weights.extend(int(t) for t in read().split())
        if len(weights)!=nr or min(weights)<1:raise ValueError('hr.dat 简并度块无效')
        h0=None;seen_r=set();evidence=None
        for ir in range(nr):
            seen=np.zeros((n,n),bool);r=None;begin=line+1;entries=[]
            for _ in range(n*n):
                raw=read();p=raw.split()
                if len(p)!=7:raise ValueError('hr.dat 矩阵行应有七列：'+str(line))
                rr=tuple(int(v) for v in p[:3]);m,k=[int(v)-1 for v in p[3:5]]
                if r is None:r=rr
                if rr!=r or not 0<=m<n or not 0<=k<n or seen[m,k]:raise ValueError('hr.dat R 块/索引重复或越界：'+str(line))
                seen[m,k]=True;v=complex(finite(p[5]),finite(p[6]))
                if r==(0,0,0):
                    if h0 is None:h0=np.zeros((n,n),complex)
                    h0[m,k]=v;entries.append({'line':line,'text':raw})
            if r in seen_r:raise ValueError('hr.dat R 向量重复')
            seen_r.add(r)
            if r==(0,0,0):
                if weights[ir]!=1:raise ValueError('R=0 简并度应为 1；拒绝未确认的 onsite 重标度')
                evidence={'first_line':begin,'last_line':line,'degeneracy':weights[ir],'entries':entries}
        for raw in stream:
            digest.update(raw)
            if raw.strip():raise ValueError('hr.dat 标称记录后存在额外数据')
    if h0 is None:raise ValueError('hr.dat 缺少 R=0')
    residual=float(np.max(np.abs(h0-h0.conj().T)))
    if residual>1e-5:raise ValueError('R=0 非 Hermitian，最大残差 '+str(residual)+' eV')
    return (h0+h0.conj().T)/2,{'num_wann':n,'nrpts':nr,'sha256':digest.hexdigest(),'hermiticity_residual_ev':residual,'symmetrization_max_ev':residual/2,'R0':evidence,'title':title}

def block(text,name,required=True):
    found=re.search(r'^\s*begin\s+'+name+r'\s*$(.*?)^\s*end\s+'+name+r'\s*$',text,re.M|re.S|re.I)
    if not found:
        if required:raise ValueError('.win 缺少 '+name)
        return []
    return [line.strip() for line in found.group(1).splitlines() if line.strip()]

def frame_axes(z,x):
    z=np.array(z,float);x=np.array(x,float)
    if np.linalg.norm(z)<1e-10:raise ValueError('z 轴不能为零')
    z/=np.linalg.norm(z);x-=np.dot(x,z)*z
    if np.linalg.norm(x)<1e-10:raise ValueError('x/z 轴平行')
    x/=np.linalg.norm(x);return np.column_stack([x,np.cross(z,x),z])

def angular(spec):
    selected=set()
    for part in spec.lower().replace(' ','').split(';'):
        if part in ('s','p','d'):selected.update((l,m) for l in ORBITALS if 'spd'[l]==part for m in range(len(ORBITALS[l])))
        elif re.fullmatch(r'l=[012](?:,mr=[1-5](?:,[1-5])*)?',part):
            l=int(part[2]);mrs=[int(v)-1 for v in part.split('mr=')[1].split(',')] if 'mr=' in part else list(range(len(ORBITALS[l])))
            if any(m>=len(ORBITALS[l]) for m in mrs):raise ValueError('mr 超出角动量范围')
            selected.update((l,m) for m in mrs)
        else:
            for name in part.split(','):
                match=[(l,m) for l,names in ORBITALS.items() for m,label in enumerate(names) if label==name]
                if not match:raise ValueError('仅支持明确 s/p/d；混成/f/随机投影须另行适配：'+part)
                selected.update(match)
    # W90 parameters.F90 orders angular states within ONE line by l then mr.
    return sorted(selected)

def win_model(path):
    original=Path(path).read_text();text='\n'.join(re.split('[!#]',s,1)[0].strip() for s in original.splitlines())
    tags={k.lower():v.strip() for k,v in re.findall(r'^\s*(\w+)\s*[:=]\s*(.*?)\s*$',text,re.M)}
    n=int(tags.get('num_wann','0'));spintext=tags.get('spinors','false').lower()
    if spintext not in ('true','.true.','t','false','.false.','f'):raise ValueError('spinors 标签无效')
    spinors=spintext in ('true','.true.','t')
    for key in ['select_projections','auto_projections','use_bloch_phases']:
        if key in tags and tags[key].lower() not in ('false','.false.','f'):raise ValueError('自动映射尚不支持 '+key+'；请选择完整明确的原子 s/p/d 投影模型')
    cell=block(text,'unit_cell_cart');factor=1
    if len(cell[0].split())==1:
        u=cell.pop(0).lower()
        if u not in ('ang','angstrom','bohr'):raise ValueError('晶胞单位无效')
        factor=BOHR if u=='bohr' else 1
    lattice=np.array([[finite(t) for t in row.split()] for row in cell])*factor
    if lattice.shape!=(3,3) or abs(np.linalg.det(lattice))<1e-10:raise ValueError('晶胞无效')
    frac=bool(block(text,'atoms_frac',False));atoms=block(text,'atoms_frac' if frac else 'atoms_cart');factor=1
    if not frac and len(atoms[0].split())==1:
        u=atoms.pop(0).lower()
        if u not in ('ang','angstrom','bohr'):raise ValueError('原子坐标单位无效')
        factor=BOHR if u=='bohr' else 1
    rows=[a.split() for a in atoms]
    if any(len(r)!=4 for r in rows):raise ValueError('原子坐标需四列')
    labels=[r[0] for r in rows];symbols=[re.sub(r'\d+$','',label) for label in labels]
    positions=np.array([[finite(t) for t in row[1:]] for row in rows])*factor
    structure=Structure(lattice,symbols,positions,coords_are_cartesian=not frac)
    projections=block(text,'projections');mapping=[]
    if projections and projections[0].lower() in ('ang','angstrom','bohr'):projection_unit=projections.pop(0).lower()
    else:projection_unit='ang'
    for row_index,line in enumerate(projections):
        pieces=re.split(r'[:=]',line,maxsplit=1) if ':' not in line else line.split(':',1)
        if len(pieces)!=2:raise ValueError('投影必须有明确中心与角动量')
        site,rest=pieces;parts=rest.split(':');spec=parts[0];spins=[None]
        spinmatch=re.search(r'\((u|d|u,d)\)(?:\[[^\]]*\])?',spec)
        if spinors:
            spins=spinmatch.group(1).split(',') if spinmatch else ['u','d']
            if '[' in spec:raise ValueError('自定义自旋量子化轴暂需专门映射')
        elif spinmatch:raise ValueError('非 spinor 不能带自旋投影')
        spec=re.sub(r'\([^)]*\)(?:\[[^\]]*\])?','',spec)
        params=dict(p.split('=',1) for p in parts[1:] if '=' in p)
        if set(params)-{'x','z','r','zona'}:raise ValueError('投影附加参数不受支持')
        q=frame_axes([finite(v) for v in params.get('z','0,0,1').split(',')],[finite(v) for v in params.get('x','1,0,0').split(',')])
        radial=int(params.get('r','1'))
        if radial<1:raise ValueError('径向编号必须为正')
        site=site.strip();indices=[i for i,(s,label) in enumerate(zip(symbols,labels)) if site==s or site==label]
        if site.startswith(('f=','c=')):
            coords=np.array([finite(t) for t in site[2:].split(',')]);coords=coords if site.startswith('f=') else np.linalg.solve(lattice.T,coords*(BOHR if projection_unit=='bohr' else 1))
            indices=[i for i,a in enumerate(structure) if structure.lattice.get_distance_and_image(a.frac_coords,coords)[0]<1e-5]
            if len(indices)!=1:raise ValueError('显式投影中心不能唯一对应原子')
        if not indices:raise ValueError('投影中心未匹配原子：'+site)
        for atom in indices:
            for l,m in angular(spec):
                for spin in spins:
                    mapping.append({'wf':len(mapping)+1,'atom':atom+1,'element':symbols[atom],'l':l,'mr':m+1,'orbital':ORBITALS[l][m],'spin':spin,'radial':radial,'axes':q.tolist(),'projection_row':row_index+1})
    if len(mapping)!=n:raise ValueError('初始投影展开数量与 num_wann 不符；不猜测 WF 顺序')
    return {'structure':structure,'mapping':mapping,'num_wann':n,'spinors':spinors,'text':original,'tags':tags}

def spectrum(h):
    ev,u=np.linalg.eigh(h);mean=float(np.trace(h).real/len(h))
    return {'eigenvalues_ev':ev.tolist(),'centroid_ev':mean,'relative_eigenvalues_ev':(ev-mean).tolist(),'span_ev':float(np.ptp(ev)),'rms_ev':float(np.sqrt(np.mean((ev-mean)**2))),'weights':(np.abs(u)**2).tolist()}

def rotation(l,q):
    if l==0:return np.ones((1,1))
    if l==1:
        # p basis = z,x,y; q columns are local x,y,z in global Cartesian axes.
        return q[:,[2,0,1]].T[:,[2,0,1]]
    return d_rotation(q)

def shell_report(h,l,spinors):
    result=spectrum(h);result['matrix']=matrix_json(h)
    if l==2 and not spinors and len(h)==5:
        a=[1,2,4];b=[0,3]
        result['cubic_partition']={'t2g_or_t2':spectrum(h[np.ix_(a,a)]),'eg_or_e':spectrum(h[np.ix_(b,b)]),'eg_minus_t2g_ev':float(np.trace(h[np.ix_(b,b)]).real/2-np.trace(h[np.ix_(a,a)]).real/3),'mixing_frobenius_ev':float(np.linalg.norm(h[np.ix_(a,b)])),'t2_weight':np.sum(np.array(result['weights'])[a,:],axis=0).tolist()}
    return result

def neighbors(structure,atom,ligands,cn):
    radius=2.0
    while radius<=64:
        candidates=sorted([p for p in structure.get_neighbors(structure[atom],radius) if p.specie.symbol in ligands and p.nn_distance>1e-6],key=lambda p:p.nn_distance)
        if len(candidates)>cn:break
        radius*=2
    if len(candidates)<=cn:raise ValueError('指定配体的周期邻居不足')
    distances=[float(p.nn_distance) for p in candidates[:cn+1]];bonds=[(p.coords-structure[atom].coords)/p.nn_distance for p in candidates[:cn]]
    q,rms=kabsch_frame(bonds,OCT_REFS if cn==6 else TET_REFS)
    return q,{'neighbors':[{'atom':int(p.index)+1,'image':[int(v) for v in p.image],'element':p.specie.symbol,'distance_ang':float(p.nn_distance),'vector_ang':(p.coords-structure[atom].coords).tolist()} for p in candidates[:cn]],'next_neighbor_distance_ang':distances[-1],'shell_gap_ratio':distances[-1]/distances[-2],'direction_rmsd':float(rms),'axes':q.tolist()}

def centres_evidence(path,model):
    if path is None:return None
    rows=Path(path).read_text().splitlines();n=int(rows[0]);records=[r.split() for r in rows[2:] if r.strip()]
    if n!=len(records) or any(len(r)!=4 for r in records):raise ValueError('centres.xyz 不完整')
    wf=np.array([[finite(t) for t in r[1:]] for r in records if r[0]=='X'])
    if wf.shape!=(model['num_wann'],3):raise ValueError('centres.xyz WF 数不符')
    atoms=[r for r in records if r[0]!='X'];structure=model['structure']
    if len(atoms)!=len(structure):raise ValueError('centres.xyz 原子数不符')
    for i,r in enumerate(atoms):
        if r[0]!=structure[i].specie.symbol or structure.lattice.get_distance_and_image(structure[i].frac_coords,structure.lattice.get_fractional_coords([finite(t) for t in r[1:]]))[0]>1e-4:raise ValueError('centres.xyz 原子身份/几何与 .win 不符')
    distances=[]
    for entry,point in zip(model['mapping'],wf):
        distances.append(float(structure.lattice.get_distance_and_image(structure[entry['atom']-1].frac_coords,structure.lattice.get_fractional_coords(point))[0]))
    return {'wf_atom_distances_ang':distances,'max_distance_ang':max(distances),'warning':'中心接近原子只支持空间局域性，不能证明轨道相位、方向或纯 s/p/d 身份'}

def wout_centres(text,model):
    sections=re.split(r'Final State',text,flags=re.I)
    if len(sections)<2:return None
    number=r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eEdD][-+]?\d+)?'
    pattern=r'WF centre and spread\s+(\d+)\s+\(\s*('+number+r'),\s*('+number+r'),\s*('+number+r')\s*\)\s+('+number+r')'
    matches=list(re.finditer(pattern,sections[-1]))
    if len(matches)!=model['num_wann'] or [int(m.group(1)) for m in matches]!=list(range(1,model['num_wann']+1)):return None
    structure=model['structure'];distances=[]
    for entry,m in zip(model['mapping'],matches):
        coords=[finite(m.group(i)) for i in (2,3,4)]
        distances.append(float(structure.lattice.get_distance_and_image(structure[entry['atom']-1].frac_coords,structure.lattice.get_fractional_coords(coords))[0]))
    return {'source':'wout Final State','wf_atom_distances_ang':distances,'max_distance_ang':max(distances),'spreads_ang2':[finite(m.group(5)) for m in matches],'warning':'中心接近只支持空间局域性，不证明轨道身份；wout 采用打印的 Å 和 Å²'}


def analyze_files(hr,win,wout=None,centres=None,options=None):
    options=options or {};model=win_model(win)
    from local_reference import inherit,public_options
    options,reference,reference_sites=inherit(options,model['structure'])
    h0,evidence=read_onsite(hr)
    if len(h0)!=model['num_wann']:raise ValueError('hr.dat 与 .win num_wann 不符')
    wtext=Path(wout).read_text(errors='replace') if wout else ''
    complete=bool(re.search('All done',wtext,re.I));final=bool(re.search('Final State',wtext,re.I))
    count=re.search(r'Number of Wannier Functions\s*[:=]?\s*(\d+)',wtext,re.I)
    if count and int(count.group(1))!=model['num_wann']:raise ValueError('.wout num_wann 与 .win 不符')
    if wout and (not complete or not final):raise ValueError('.wout 缺少 Final State / All done；不使用未完成局域化')
    centers=centres_evidence(centres,model) or wout_centres(wtext,model)
    specs=parse_types(options)
    targets=[s.strip() for s in options.get('elements','').split(',') if s.strip()];ligands=[s.strip() for s in options.get('ligands','').split(',') if s.strip()]
    coordination={}
    for part in options.get('coordination','').split(','):
        if not part.strip():continue
        element,cn=part.strip().split(':');cn=int(cn)
        if cn not in (4,6):raise ValueError('附加 d 立方分组仅支持 4 或 6 配位')
        coordination[element.strip()]=cn
    if specs:
        if any(s not in {a.specie.symbol for a in model['structure']} or v['ligand'] not in {a.specie.symbol for a in model['structure']} for s,v in specs.items()):raise ValueError('所选多面体的中心或配体不在当前结构中')
        if options.get('coordination') or options.get('ligands') or options.get('elements'):raise ValueError('多面体列表与旧配位/元素参数请只使用一种方式')
        targets=list(specs);coordination={c:v['coordination'] for c,v in specs.items()};ligands=list({v['ligand'] for v in specs.values()})
    if coordination and not ligands:raise ValueError('配位分析必须显式选择配体元素')
    available={a.specie.symbol for a in model['structure']}
    if coordination and not set(ligands)<=available:raise ValueError('指定配体元素不在当前结构中')
    groups={}
    for entry in model['mapping']:
        if targets and entry['element'] not in targets:continue
        if specs and entry['l']!=2:continue
        key=(entry['atom'],entry['l'],entry['radial'],tuple(np.array(entry['axes']).ravel()))
        groups.setdefault(key,[]).append(entry)
    if not groups:raise ValueError('所选元素没有 s/p/d 投影')
    reports=[];warnings=['结果为所选 Wannier 模型中的有效 onsite 参数，包含杂化、交换和 DFT+U 等贡献；不是纯静电晶场或 PDOS 重心。','初始投影顺序不证明最终 WF 的轨道身份；必须结合中心、波函数/重叠及 Wannier 带拟合审核。','独立自旋/模型的 onsite 绝对能量与矩阵不能直接相减或平均，除非能量参考和 Wannier gauge 已对齐。']
    for key,entries in groups.items():
        atom,l,radial,axes=key;entries=sorted(entries,key=lambda e:(e['mr'],{None:0,'u':0,'d':1}[e['spin']]))
        indices=[e['wf']-1 for e in entries];h=h0[np.ix_(indices,indices)];expected=[(m+1,s) for m in range(len(ORBITALS[l])) for s in (['u','d'] if model['spinors'] else [None])]
        full=[(e['mr'],e['spin']) for e in entries]==expected
        label=f"{entries[0]['element']}{atom} · {'spd'[l]}";report={'label':label,'atom':atom,'element':entries[0]['element'],'l':l,'radial':radial,'wf_indices':[i+1 for i in indices],'orbital_labels':[e['orbital']+('('+e['spin']+')' if e['spin'] else '') for e in entries],'complete_shell':full,'raw':shell_report(h,l,model['spinors']),'global_axes_candidate':None,'coordination_candidate':None,'transforms':{},'basis_status':'USER_CONFIRMED_ASSUMPTION' if options.get('basis_confirmed') else 'INITIAL_PROJECTION_CANDIDATE'}
        if full:
            rd=rotation(l,np.array(entries[0]['axes']))
            if model['spinors']:rd=np.kron(rd,np.eye(2))
            report['transforms']['projection_to_global']={'spatial_axes':entries[0]['axes'],'orbital_rotation':rd.tolist(),'formula':'H_global_candidate = R_projection.T @ H_raw @ R_projection'}
            global_h=rd.T@h@rd
            report['global_axes_candidate']=shell_report(global_h,l,model['spinors'])
            if l==2 and report['element'] in coordination:
                local_ligands=[specs[report['element']]['ligand']] if specs else ligands
                if atom in reference_sites:
                    import copy
                    geometry=copy.deepcopy(reference_sites[atom]['geometry']);q=np.asarray(geometry['axes'])
                    report['reference_alignment']={'status':'REFERENCE_FRAME_MATCHED_WF_UNVERIFIED','source':reference['source'],'meaning':'继承几何轴；最终WF轨道身份仍需独立核验，不套用自然占据本征矢'}
                    report['occupation_reference']=reference_sites[atom].get('occupation_reference',[])
                else:q,geometry=neighbors(model['structure'],atom-1,local_ligands,coordination[report['element']])
                r=rotation(l,q)
                if specs:
                    if max(v['distance_ang'] for v in geometry['neighbors'])>float(options.get('search_radius_angstrom',3)):raise ValueError(report['label']+' 在搜索距离内配体不足，请检查多面体类型或调整最大键长')
                    report['polyhedron']=polyhedron_label(report['element'],specs[report['element']])
                    geometry['shell_gap_warning']=geometry['next_neighbor_distance_ang']-max(v['distance_ang'] for v in geometry['neighbors'])<0.15
                    if geometry['shell_gap_warning']:warnings.append(report['label']+' 配位壳层间隔小于0.15 Å，请确认配位选择')
                if model['spinors']:r=np.kron(r,np.eye(2))
                report['transforms']['global_to_coordination']={'orbital_rotation':r.tolist(),'formula':'H_local_candidate = R_local @ H_global_candidate @ R_local.T'}
                rotated=r@global_h@r.T
                report['coordination_candidate']={'geometry':geometry,'coordination':coordination[report['element']],'metrics':shell_report(rotated,l,model['spinors'])}
        if report.get('coordination_candidate'):
            report['reference_alignment']=report.get('reference_alignment',{'status':'GEOMETRY_FRAME_ONLY','meaning':'未导入VASP参考；配位架独立拟合，轨道名称为初始投影候选'})
        reports.append(report)
    if reference:
        warnings.append('已继承VASP冻结配位架；结构匹配不证明最终WF身份、同一电子态或能带拟合质量。')
    if centers and centers['max_distance_ang']>float(options.get('centre_tolerance_ang',0.5)):warnings.append('有 WF 中心偏离所分配原子，轨道映射需重新审核')
    if re.search(r'Disentanglement convergence criteria not satisfied',wtext,re.I):warnings.append('解缠结明确未收敛；停止标志单独保留，请结合能带拟合与局域轨道特征判断，不单凭此标志排除结果')
    if model['spinors']:warnings.append('SOC/spinor 保留复数 2(2l+1) 块；不把其劈裂冒充无 SOC 的 eg/t2g 晶场或自动去除 SOC')
    site_blocks=[]
    for atom in sorted({e['atom'] for e in model['mapping'] if not targets or e['element'] in targets}):
        entries=[e for e in model['mapping'] if e['atom']==atom];indices=[e['wf']-1 for e in entries]
        site_blocks.append({'atom':atom,'element':entries[0]['element'],'wf_indices':[i+1 for i in indices],'labels':[e['orbital']+('('+e['spin']+')' if e['spin'] else '') for e in entries],'metrics':shell_report(h0[np.ix_(indices,indices)],-1,model['spinors'])})
    return {'local_reference':reference,'schema':'fpilot.onsite-analysis.v2','unit':'eV','mapping':model['mapping'],'mapping_evidence':'Wannier90 .win 初始投影展开：行序→同元素原子序→l→mr→自旋 u/d；不是最终 WF 身份验证','spinors':model['spinors'],'lattice_ang':model['structure'].lattice.matrix.tolist(),'atoms':[{'element':a.specie.symbol,'cart_ang':a.coords.tolist()} for a in model['structure']],'source_files':{'hr':str(hr),'win':str(win),'wout':str(wout) if wout else None,'centres':str(centres) if centres else None},'hr_evidence':evidence,'centres':centers,'localization':{'program_finished':complete,'final_state_recorded':final,'disentanglement_converged':False if re.search('Disentanglement convergence criteria not satisfied',wtext,re.I) else True if re.search('Disentanglement convergence criteria satisfied',wtext,re.I) else None,'spread_convergence':'NOT_ESTABLISHED_BY_ALL_DONE','physical_validation':'NOT_PERFORMED'},'reports':reports,'site_blocks':site_blocks,'warnings':warnings,'options':public_options(options)}

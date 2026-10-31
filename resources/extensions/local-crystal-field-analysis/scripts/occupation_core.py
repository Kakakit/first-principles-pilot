"""Final-step, collinear VASP d occupations; no Hamiltonian or remote operations."""
import re
from pathlib import Path
import numpy as np
from pymatgen.core import Structure
from pdos_core import outcar_metadata
from onsite_core import neighbors,rotation,ORBITALS
from polyhedron_types import parse_types,label as polyhedron_label

VASP_D=['dxy','dyz','dz2','dxz','dx2-y2']
TO_CANONICAL=[2,3,1,4,0]

def read_final_matrices(path):
    """Stream by electronic iteration; never combine atoms from different steps."""
    records={};all_atoms=set();atom=None;angular=None;iteration=None;explicit=False;count=0;line_no=0;lattice=None;counts=None;symbols=[]
    with Path(path).open(encoding='utf-8',errors='replace') as f:
        for line in f:
            line_no+=1
            if 'direct lattice vectors' in line:
                cell=[]
                for _ in range(3):
                    row=next(f,'');line_no+=1
                    try:cell.append([float(v) for v in row.split()[:3]])
                    except ValueError:raise ValueError('OUTCAR晶格数据不完整')
                if np.asarray(cell).shape!=(3,3):raise ValueError('OUTCAR晶格数据不完整')
                lattice=cell
            if 'ions per type' in line:
                counts=[int(v) for v in line.split('=')[-1].split()]
            titel=re.search(r'TITEL\s*=\s*\S+\s+([A-Z][a-z]?)',line)
            if titel:symbols.append(titel.group(1))
            step=re.search(r'\bIteration\s+(\d+)\(\s*(\d+)\)',line)
            if step:
                iteration=[int(v) for v in step.groups()];records={};explicit=True;atom=None;angular=None
            header=re.search(r'atom\s*=\s*(\d+).*?\bl\s*=\s*(\d+)',line)
            if header:atom,angular=map(int,header.groups())
            if 'onsite density matrix' not in line.lower():continue
            if atom is None:raise ValueError('占据矩阵缺少明确原子编号，不能按出现顺序猜测')
            first=line_no;spins={};active=None;raw=[]
            for body in f:
                line_no+=1;raw.append(body.rstrip())
                if 'occupancies and eigenvectors' in body:break
                match=re.search(r'spin component\s+([12])',body)
                if match:active=match.group(1);spins[active]=[];continue
                if not body.strip():continue
                if active:
                    try:row=[float(v.replace('D','E').replace('d','e')) for v in body.split()]
                    except ValueError:raise ValueError('占据矩阵含不支持的行或已截断：第'+str(line_no)+'行')
                    spins[active].append(row)
            else:raise ValueError('最后的占据矩阵已截断；不会回退到较早电子步')
            if angular!=2:continue
            if set(spins)!={'1','2'} or any(np.asarray(rows).shape!=(5,5) for rows in spins.values()):raise ValueError('需要完整的两自旋5×5 d占据矩阵')
            if not explicit and atom in records:records={}
            if atom in records:raise ValueError('同一电子步重复出现原子占据矩阵')
            for rows in spins.values():
                a=np.asarray(rows)
                if not np.all(np.isfinite(a)) or np.max(np.abs(a-a.T))>5e-4:raise ValueError('占据矩阵含非有限数值或明显不对称')
            records[atom]={'atom':atom,'line':first,'last_line':line_no,'spins':spins,'raw_text':'onsite density matrix\n'+'\n'.join(raw),'iteration':iteration}
            all_atoms.add(atom);count+=1
    if not records:raise ValueError('OUTCAR 最后电子步未保存 d 占据矩阵；需要已有 LDAUPRINT 输出，不自动重跑')
    if set(records)!=all_atoms:raise ValueError('最后电子步缺少部分原子的占据矩阵；不会混用较早电子步')
    return records,{'matrix_blocks':count,'final_iteration':iteration,'final_atoms':sorted(records),'orbital_order':VASP_D,'canonical_order':ORBITALS[2],'permutation_0based':TO_CANONICAL,'lattice_angstrom':lattice,'ions_per_type':counts,'symbols':symbols[:len(counts)] if counts else symbols}

def density_report(n):
    n=(np.asarray(n)+np.asarray(n).T.conj())/2
    vals,u=np.linalg.eigh(n);order=np.argsort(vals)[::-1];vals=vals[order];u=u[:,order]
    return {'matrix':{'real':n.real.tolist(),'imag':n.imag.tolist(),'unit':'electron'},'diagonal':np.diag(n).real.tolist(),'trace':float(np.trace(n).real),'natural_occupations':vals.tolist(),'weights':(np.abs(u)**2).tolist()}

def analyze_occupation(structure,outcar,options=None):
    options=options or {};settings=outcar_metadata(outcar)
    if settings['soc'] or settings['noncollinear']:raise ValueError('占据首版只支持无SOC共线结果；SOC请继续使用已有PDOS或Wannier spinor分析')
    if settings['spin']!=2:raise ValueError('占据首版需要 ISPIN=2 的两自旋矩阵；当前可使用已有PDOS模式')
    if not settings['program_finished'] or not settings['ediff_reached']:raise ValueError('OUTCAR 缺少正常结束或 EDIFF 收敛证据')
    if settings.get('nsw') not in (None,0):raise ValueError('请使用已完成的静态SCF/DOS占据结果，避免末步矩阵与结构不对应')
    atoms=Structure.from_file(str(structure));records,evidence=read_final_matrices(outcar)
    if evidence['lattice_angstrom'] is not None and not np.allclose(atoms.lattice.matrix,evidence['lattice_angstrom'],rtol=0,atol=1e-5):raise ValueError('结构晶格与OUTCAR不一致，请选择同一冻结来源')
    if evidence['ions_per_type'] is not None and sum(evidence['ions_per_type'])!=len(atoms):raise ValueError('结构原子数与OUTCAR不一致')
    if evidence['symbols'] and evidence['ions_per_type'] and len(evidence['symbols'])==len(evidence['ions_per_type']):
        expected=[symbol for symbol,n in zip(evidence['symbols'],evidence['ions_per_type']) for _ in range(n)]
        if expected!=[a.specie.symbol for a in atoms]:raise ValueError('结构元素顺序与OUTCAR不一致')
    return analyze_records(atoms,records,settings,evidence,options)

def analyze_records(atoms,records,settings,evidence,options=None):
    """Same numerical path for previously frozen matrix extracts; caller retains lineage."""
    options=options or {}
    if options.get('local_reference'):raise ValueError('VASP占据阶段请移除导入参考，参考应在Wannier阶段使用')
    specs=parse_types(options);targets=[v.strip() for v in options.get('elements','').split(',') if v.strip()]
    if specs and (targets or options.get('coordination') or options.get('ligands')):raise ValueError('多面体列表与旧配位/元素参数请只使用一种方式')
    if any(a>len(atoms) for a in records):raise ValueError('OUTCAR 原子编号超出对应结构')
    selected=[a for a in records if (not specs or atoms[a-1].specie.symbol in specs) and (not targets or atoms[a-1].specie.symbol in targets)]
    if not selected:raise ValueError('所选元素没有完整 d 占据矩阵；检查 LDAUL 和 LDAUPRINT 输出')
    if specs:
        for center,spec in specs.items():
            ids=[i+1 for i,a in enumerate(atoms) if a.specie.symbol==center]
            if not ids or spec['ligand'] not in {a.specie.symbol for a in atoms}:raise ValueError('所选多面体的中心或配体不在当前结构中')
            if any(i not in records for i in ids):raise ValueError(center+' 的部分原子缺少最终 d 占据矩阵')
    warnings=['占据数是所选PAW子空间的电子占据，不是eV单位的晶场能级，也不直接等于形式氧化态。','自然占据轨道可以是多个d轨道的混合；近简并态的单个本征矢不唯一。'];reports=[]
    for atom in selected:
        symbol=atoms[atom-1].specie.symbol;record=records[atom];q=None;geometry=None
        if symbol in specs:
            spec=specs[symbol];q,geometry=neighbors(atoms,atom-1,[spec['ligand']],spec['coordination'])
            if max(v['distance_ang'] for v in geometry['neighbors'])>float(options.get('search_radius_angstrom',3)):raise ValueError(symbol+str(atom)+' 在搜索距离内配体不足，请检查多面体类型或调整最大键长')
            gap=geometry['next_neighbor_distance_ang']-max(v['distance_ang'] for v in geometry['neighbors']);geometry['shell_gap_warning']=gap<0.15
            if gap<0.15:warnings.append(symbol+str(atom)+' 配位壳层间隔小于0.15 Å，请确认配位选择')
            if geometry['direction_rmsd']>.15:warnings.append(symbol+str(atom)+' 偏离理想配位方向较明显；局域轨道分组只作近似参考')
        mats={s:np.asarray(record['spins'][key])[np.ix_(TO_CANONICAL,TO_CANONICAL)] for s,key in [('up','1'),('down','2')]};mats['total']=mats['up']+mats['down'];r=rotation(2,q) if q is not None else None;spins=[]
        for spin,n in mats.items():
            global_report=density_report(n);local=density_report(r@n@r.T) if r is not None else None;group=None
            if local is not None:
                diag=np.asarray(local['diagonal']);group={'e_label':'eɡ' if specs[symbol]['coordination']==6 else 'e','t_label':'t₂g' if specs[symbol]['coordination']==6 else 't₂','e_occupation':float(diag[[0,3]].sum()),'t_occupation':float(diag[[1,2,4]].sum()),'interpretation':'所选配位架下的近似子空间分组，不证明严格点群对称性'}
            spins.append({'id':spin,'label':{'up':'↑','down':'↓','total':'↑ + ↓'}[spin],'global':global_report,'local':local,'groups':group})
        reports.append({'atom':atom,'element':symbol,'label':symbol+str(atom),'polyhedron':polyhedron_label(symbol,specs[symbol]) if symbol in specs else None,'orbital_labels':ORBITALS[2],'spins':spins,'spin_moment_d':float(np.trace(mats['up']-mats['down'])),'geometry':geometry,'orbital_rotation':r.tolist() if r is not None else None,'source_lines':[record['line'],record['last_line']],'raw_text':record['raw_text']})
    from local_reference import structure_record
    return {'structure':structure_record(atoms),'schema':'fpilot.vasp-occupation.v1','mode':'occupation','atoms':reports,'settings':settings,'evidence':evidence,'options':options,'warnings':warnings,'unit':'electron','physical_validation':'NOT_PERFORMED'}

"""Read only this run's native SpinIO output, retaining TB2J units/conventions."""
import csv
import json
from pathlib import Path
import numpy as np


def clean(value):
    if isinstance(value,dict):return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [clean(v) for v in value]
    if hasattr(value,'tolist'):return clean(value.tolist())
    if hasattr(value,'item'):return clean(value.item())
    return value


def dump(path,value):
    Path(path).write_text(json.dumps(clean(value),ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def real(value,shape=None):
    array=np.asarray(value)
    if np.iscomplexobj(array):
        if np.max(np.abs(array.imag))>1e-10:raise ValueError('TB2J 输出含不可忽略的虚部')
        array=array.real
    array=np.asarray(array,dtype=float)
    if shape is not None and array.shape!=shape or not np.isfinite(array).all():raise ValueError('未知 TB2J 输出形状或非有限值')
    return array*1000 # Native SpinIO stores eV; display and exports use meV.


def export_csv(path,rows,columns):
    with Path(path).open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
        for row in rows:writer.writerow({k:json.dumps(clean(row[k]),ensure_ascii=False,allow_nan=False) if isinstance(row.get(k),(dict,list,tuple,np.ndarray)) else row.get(k,'') for k in columns})


def parse(raw,checked,options,output):
    from TB2J.io_exchange.io_exchange import SpinIO
    pickle_path=raw/'TB2J.pickle'
    if not pickle_path.is_file() or pickle_path.is_symlink() or not (raw/'exchange.out').is_file():raise ValueError('未知或不完整 TB2J 输出：需要本次生成的 TB2J.pickle 与 exchange.out；原始计算文件保留')
    io=SpinIO.load_pickle(path=str(raw)) # Never load a source/user-supplied pickle.
    if not isinstance(io.exchange_Jdict,dict) or not io.exchange_Jdict:raise ValueError('TB2J 没有有效交换参数')
    if bool(io.colinear)!=(checked['mode']=='collinear'):raise ValueError('TB2J 输出自旋模式与输入不符')
    if io.atoms.get_chemical_symbols()!=checked['atoms'].get_chemical_symbols() or not np.allclose(io.atoms.cell.array,checked['atoms'].cell.array,atol=1e-6) or not np.allclose(io.atoms.positions,checked['atoms'].positions,atol=1e-6):raise ValueError('TB2J 输出结构与输入原子顺序不符')
    spin_atoms=io.ind_atoms;mapping={(row['atom_index'],row['orbital_index']):row for row in checked['mapping']};validated=set();rows=[];orbital=[];groups=[]
    for key in sorted(io.exchange_Jdict):
        if not isinstance(key,tuple) or len(key)!=3 or len(key[0])!=3:raise ValueError('未知交换键格式，预期 (R, i, j)')
        R,i,j=key
        if any(int(x)!=x for x in R) or i not in spin_atoms or j not in spin_atoms:raise ValueError('TB2J 晶格平移或自旋到原子映射无效')
        ai,aj=int(spin_atoms[i])+1,int(spin_atoms[j])+1
        if not 1<=ai<=len(io.atoms) or not 1<=aj<=len(io.atoms):raise ValueError('TB2J 原子编号越界')
        distance=io.distance_dict[key]
        length=float(distance[1])
        if not np.isfinite(length) or length<0:raise ValueError('TB2J 原子间距无效')
        if options.get('rcut_angstrom') is not None and length>=float(options['rcut_angstrom']):raise ValueError('TB2J 返回了超出请求最大距离的交换项；原产物保留，请核对实际命令与版本')
        base={'atom_i':ai,'atom_j':aj,'symbol_i':io.atoms[ai-1].symbol,'symbol_j':io.atoms[aj-1].symbol,'R':list(map(int,R)),'distance_A':length}
        row={**base,'J_meV':float(real(io.exchange_Jdict[key],()))}
        for name,attribute,shape in [('DMI_meV','dmi_ddict',(3,)),('Jani_meV','Jani_dict',(3,3))]:
            values=getattr(io,attribute,None)
            if checked['mode']=='spinor' and not isinstance(values,dict):raise ValueError('未知或不完整 SOC 输出：缺少 '+attribute+'；原始计算文件保留')
            if values is not None:
                if key not in values:raise ValueError('TB2J '+attribute+' 缺少交换键')
                row[name]=clean(real(values[key],shape))
        rows.append(row)
        if not options['orb_decomposition']:continue
        native=getattr(io,'Jiso_orb',None)
        if not isinstance(native,dict) or key not in native:raise ValueError('本次输出缺少请求的 J 轨道矩阵；原始文件保留')
        matrix=real(native[key])
        if matrix.ndim!=2 or min(matrix.shape)<1:raise ValueError('未知 J 轨道矩阵形状')
        components={}
        for name,attribute,shape in [('DMI_meV','DMI_orb',(3,)+matrix.shape),('Jani_meV','Jani_orb',(3,3)+matrix.shape)]:
            values=getattr(io,attribute,None)
            if checked['mode']=='spinor' and (not isinstance(values,dict) or key not in values):raise ValueError('SOC 输出缺少请求的 '+attribute+' 轨道矩阵；原始计算文件保留')
            if values is not None and key in values:
                component=real(values[key],shape)
                components[name]=component.transpose(1,2,0) if name=='DMI_meV' else component.transpose(2,3,0,1)
        group_sum={};mapped_sum=0;matrix_sum=float(matrix.sum())
        for a in range(matrix.shape[0]):
            for b in range(matrix.shape[1]):
                ma=mapping.get((ai,a+1));mb=mapping.get((aj,b+1))
                if ma:validated.add((ai,a+1))
                if mb:validated.add((aj,b+1))
                entry={**base,'orbital_i':a+1,'orbital_j':b+1,'label_i':ma['label'] if ma else 'orb_'+str(a+1),'label_j':mb['label'] if mb else 'orb_'+str(b+1),'group_i':ma.get('group','') if ma else '', 'group_j':mb.get('group','') if mb else '', 'J_meV':float(matrix[a,b])}
                for name,values in components.items():entry[name]=clean(values[a,b])
                orbital.append(entry)
                if ma and mb and ma.get('group') and mb.get('group'):
                    pair=(ma['group'],mb['group']);group_sum[pair]=group_sum.get(pair,0)+float(matrix[a,b]);mapped_sum+=float(matrix[a,b])
        groups.append({**base,'matrix_sum_meV':matrix_sum,'total_J_meV':row['J_meV'],'matrix_minus_J_meV':matrix_sum-row['J_meV'],'mapped_group_sum_meV':mapped_sum,'unmapped_matrix_meV':matrix_sum-mapped_sum,'mapped_minus_J_meV':mapped_sum-row['J_meV'],'groups':[{'group_i':g[0],'group_j':g[1],'J_meV':v} for g,v in sorted(group_sum.items())]})
    if set(mapping)-validated:raise ValueError('轨道映射超出本次实际原子轨道矩阵：'+str(sorted(set(mapping)-validated)))
    full={'schema':'fpilot.tb2j-data.v2','units':{'exchange':'meV','distance':'Å'},'atom_index_base':1,'orbital_index_base':1,'R_convention':'native TB2J lattice translation','mode':checked['mode'],'energy_reference':checked['energy_reference'],'orbital_mapping':checked['mapping'],'exchange':rows,'orbital':orbital,'groups':groups}
    full['wf_identity_models']=checked.get('wf_identity_models',[]);full['wf_spin_alignment']=checked.get('wf_spin_alignment')
    if full['wf_identity_models']:
        from wf_report import mapping_rows
        from orbital_labels import metadata
        bounds={}
        for row in orbital:
            for end in ['i','j']:bounds[row['atom_'+end]]=max(bounds.get(row['atom_'+end],0),row['orbital_'+end])
        basis=metadata(output,full,bounds,options)['basis_rows']
        identity_maps={m['channel']:{(r['atom_index'],r['orbital_index']):r for r in mapping_rows(m,basis)} for m in full['wf_identity_models']}
        evidence=next(iter(identity_maps.values()))
        for row in orbital:
            for end in ['i','j']:
                r=evidence.get((row['atom_'+end],row['orbital_'+end]))
                if r:
                    row['wf_'+end]=r['wannier_index'];row['basis_evidence_'+end]=r['source'];row['identity_status_'+end]=r['status'];row['identity_components_'+end]=r['components']
                    matching={ch:values.get((r['atom_index'],r['orbital_index'])) for ch,values in identity_maps.items()}
                    row['identity_components_'+end]={ch:v['components'] for ch,v in matching.items() if v}
                    labels={v['label'] for v in matching.values() if v}
                    if len(labels)>1:row['identity_status_'+end]='SPIN_DEPENDENT_CHARACTER'
                    if (row['atom_'+end],row['orbital_'+end]) not in mapping:row['label_'+end]=r['label'] if len(labels)==1 else 'WF'+str(r['wannier_index'])+' · 自旋成分不同'
    dump(output/'exchange.json',full);export_csv(output/'exchange.csv',rows,['atom_i','atom_j','symbol_i','symbol_j','R','distance_A','J_meV','DMI_meV','Jani_meV'])
    paths=[('exchange.json','完整高精度数据','application/json'),('exchange.csv','交换参数 CSV','text/csv')]
    if orbital:
        export_csv(output/'orbitals.csv',orbital,['symbol_i','atom_i','symbol_j','atom_j','R','distance_A','orbital_i','orbital_j','label_i','label_j','group_i','group_j','wf_i','wf_j','basis_evidence_i','basis_evidence_j','identity_status_i','identity_status_j','identity_components_i','identity_components_j','J_meV','DMI_meV','Jani_meV']);export_csv(output/'groups.csv',groups,['symbol_i','atom_i','symbol_j','atom_j','R','distance_A','total_J_meV','matrix_sum_meV','matrix_minus_J_meV','mapped_group_sum_meV','unmapped_matrix_meV','mapped_minus_J_meV','groups']);paths += [('orbitals.csv','原始轨道矩阵与确认标签','text/csv'),('groups.csv','用户分组与总 J 差值','text/csv')]
    def column(key,label,unit=''):return {'key':key,'label':label,'unit':unit}
    common=[column('symbol_i','元素 i'),column('atom_i','原子 i（1 起）'),column('symbol_j','元素 j'),column('atom_j','原子 j（1 起）'),column('R','晶格平移 R'),column('distance_A','距离','Å')]
    cols=common+[column('J_meV','J','meV')]
    if any('DMI_meV' in r for r in rows):cols+=[column('DMI_meV','DMI [x,y,z]','meV')]
    if any('Jani_meV' in r for r in rows):cols+=[column('Jani_meV','各向异性交换 3×3','meV')]
    tables=[{'title':'交换参数（按距离升序，最多展示 1000 对；完整数据见下载）','columns':cols,'rows':sorted(rows,key=lambda r:r['distance_A'])[:1000]}]
    if orbital:tables.append({'title':'原子内轨道矩阵序号（未映射项保留原始序号）','columns':common+[column('orbital_i','轨道 i（1 起）'),column('orbital_j','轨道 j（1 起）'),column('label_i','标签 i'),column('label_j','标签 j'),column('group_i','分组 i'),column('group_j','分组 j'),column('J_meV','轨道 J','meV')],'rows':orbital[:1000]})
    if groups:tables.append({'title':'仅用户映射的分组汇总与总 J 差值','columns':common+[column(k,label,'meV') for k,label in [('total_J_meV','总 J'),('matrix_sum_meV','完整矩阵和'),('matrix_minus_J_meV','矩阵和−J'),('mapped_group_sum_meV','已映射分组和'),('unmapped_matrix_meV','未分组矩阵元素和'),('mapped_minus_J_meV','分组和−J')]],'rows':groups[:1000]})
    curve={'title':'交换参数随距离（原始原子对；非收敛检验）','x_label':'距离','x_unit':'Å','y_label':'J','y_unit':'meV','series':[{'label':'J','points':[[r['distance_A'],r['J_meV']] for r in sorted(rows,key=lambda r:r['distance_A'])[:2000]]}]}
    warnings=['程序完成只表示 TB2J 产物可读取；积分网格、积分点数和能量范围收敛尚未验证。','DFT–Wannier 能带一致性与物理有效性需要独立审查。','沿用 TB2J 的符号、原子对计数与 R 约定；未自行倍增或重定标。']
    if checked['mode']=='spinor':warnings.append('单方向 SOC：DMI 的平行分量和含该磁化方向的各向异性分量不构成完整方向验证；需多方向计算与合并后验证。')
    if checked['mapping'] and groups and any(abs(g['unmapped_matrix_meV'])>1e-10 for g in groups):warnings.append('轨道分组为部分映射；差值已保留，未映射元素不归入 t2g/eg。')
    return {'summary':'TB2J 程序完成：'+str(len(rows))+' 个原子对；数值收敛与科学验证待完成。','warnings':warnings,'tables':tables,'curves':[curve],'artifacts':[{'path':p,'label':l,'media_type':m} for p,l,m in paths], 'validation':{'program':'COMPLETED','numerical_convergence':'NOT_EVALUATED','physical':'NOT_PERFORMED','direction_validation':'SINGLE_DIRECTION' if checked['mode']=='spinor' else 'NOT_EVALUATED'}}

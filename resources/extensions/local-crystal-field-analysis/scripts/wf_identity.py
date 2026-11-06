"""Final WF/reference overlaps; identify, never replace the Hamiltonian basis."""
import re
import numpy as np
from pathlib import Path
from wf_io import read_u,read_amn
from wf_report import digest

def uniform_kpoints(win,ks):
    from onsite_core import block,finite
    rows=block(win,'kpoints');expected=np.array([[finite(v) for v in row.split()] for row in rows])
    if expected.shape!=ks.shape or not np.allclose((expected-ks)-np.rint(expected-ks),0,atol=1e-8,rtol=0):raise ValueError('U矩阵k点顺序与冻结win不符')
    reduced=np.mod(ks,1);reduced[np.isclose(reduced,1,atol=1e-9,rtol=0)]=0;rounded=np.round(reduced,9)
    if len(np.unique(rounded,axis=0))!=len(ks):raise ValueError('k点重复，不能进行相干胞间分析')
    axes=[np.unique(rounded[:,i]) for i in range(3)]
    if np.prod([len(a) for a in axes])!=len(ks):raise ValueError('首版WF身份分析需要完整均匀k网格')
    for axis in axes:
        if not np.allclose(np.diff(np.r_[axis,axis[0]+1]),1/len(axis),atol=1e-8,rtol=0):raise ValueError('首版WF身份分析不支持非均匀或不可约k网格')
    return [len(a) for a in axes]

def analyze(amn_path,u_path,udis_path,win_path,data,channel,eig_path=None):
    from onsite_core import win_model,rotation,ORBITALS
    model=win_model(win_path)
    if model['spinors']:raise ValueError('WF身份首版只核验无SOC共线；SOC保留现有候选标签')
    with Path(amn_path).open() as f:a=read_amn(f)
    with Path(u_path).open() as f:ks,u=read_u(f)
    nk,nb,nproj=a.shape;nw=model['num_wann']
    if nproj!=nw or u.shape!=(nk,nw,nw):raise ValueError('amn/U维度与完整win投影数不一致')
    grid=uniform_kpoints(model['text'],ks)
    if not np.allclose(u.conj().transpose(0,2,1)@u,np.eye(nw),atol=1e-6,rtol=0):raise ValueError('最终U矩阵不是酉矩阵')
    if udis_path:
        with Path(udis_path).open() as f:kd,ud=read_u(f)
        if ud.shape!=(nk,nb,nw) or not np.allclose(kd,ks,atol=1e-8,rtol=0):raise ValueError('U_dis的维度、k点或顺序与U不一致')
        if not np.allclose(ud.conj().transpose(0,2,1)@ud,np.eye(nw),atol=1e-6,rtol=0):raise ValueError('U_dis不是半酉变换')
        if not eig_path:raise ValueError('解缠结U_dis需要同来源.eig恢复外窗口内的压缩能带行，不能直接与完整amn相乘')
        records=np.loadtxt(eig_path,ndmin=2)
        if records.shape!=(nk*nb,3) or not np.isfinite(records).all():raise ValueError('.eig维度或数值与amn不符')
        e=np.full((nk,nb),np.nan)
        for band,k,energy in records:
            if band!=int(band) or k!=int(k) or not 1<=band<=nb or not 1<=k<=nk or np.isfinite(e[int(k)-1,int(band)-1]):raise ValueError('.eig索引重复或越界')
            e[int(k)-1,int(band)-1]=energy
        tags=dict((k.lower(),v.strip()) for k,v in re.findall(r'^\s*(\w+)\s*[:=]\s*(.*?)\s*$',model['text'],re.M))
        if any(k in tags for k in ['dis_spheres_num','dis_spheres_first_wann','dis_spheres']):raise ValueError('首版不支持球形解缠结窗口')
        lower=float(tags.get('dis_win_min',np.min(e)-1));upper=float(tags.get('dis_win_max',np.max(e)+1))
        if not np.isfinite([lower,upper]).all() or lower>=upper:raise ValueError('解缠结外窗口无效')
        expanded=np.zeros_like(ud);counts=[]
        for k in range(nk):
            active=np.flatnonzero((e[k]>=lower)&(e[k]<=upper));n=len(active);counts.append(n)
            if n<nw or np.max(np.abs(ud[k,n:]),initial=0)>1e-7:raise ValueError('U_dis压缩行与.eig/外窗口不一致；拒绝猜测能带对应')
            expanded[k,active]=ud[k,:n]
        v=expanded@u
        reconstructed=np.mean(np.sum(np.abs(v)**2*e[:,:,None],axis=1),axis=0);hdiag=np.zeros(nw)
        for row in data['hr_evidence']['R0']['entries']:
            parts=row['text'].split()
            if parts[3]==parts[4]:hdiag[int(parts[3])-1]=float(parts[5])
        energy_residual=float(np.max(np.abs(reconstructed-hdiag)))
        if energy_residual>1e-4:raise ValueError('U/eig重建的onsite对角能量与冻结hr不一致：'+str(energy_residual)+' eV；身份报告未生成')

    elif nb==nw:v=u
    else:raise ValueError('存在解缠结维度，需要同来源_u_dis.mat；单独U不够')
    # Transform the reference bra amplitudes before taking absolute squares.
    lm=np.eye(nproj);references=[];groups={};notes=[]
    for e in model['mapping']:
        references.append({'reference':e['wf'],'atom':e['atom'],'element':e['element'],'l':e['l'],'label':e['orbital'],'frame':'初始投影轴','projection_axes':e['axes'],'radial':e['radial']})
        groups.setdefault((e['atom'],e['l'],e['radial'],tuple(np.asarray(e['axes']).ravel())),[]).append(e)
    for (atom,l,radial,axes),entries in groups.items():
        entries=sorted(entries,key=lambda e:e['mr']);idx=[e['wf']-1 for e in entries]
        if [e['mr'] for e in entries]!=list(range(1,len(ORBITALS[l])+1)):
            notes.append('原子'+str(atom)+'的部分壳不旋转，不补造轨道');continue
        report=next((r for r in data['reports'] if r['atom']==atom and r['l']==l and r['radial']==radial and r.get('coordination_candidate')),None)
        if report:
            q=report['coordination_candidate']['geometry']['axes'];d=rotation(l,np.asarray(q))@rotation(l,np.asarray(entries[0]['axes'])).T
            lm[np.ix_(idx,idx)]=d
            for i in idx:references[i]['frame']='冻结局域配位轴'
    power=np.zeros((nproj,nw));home=np.zeros((nproj,nw),complex);raw_home=np.zeros_like(home);ranks=[];norms=np.zeros(nproj)
    for k in range(nk):
        aa=a[k]@lm.T;g=aa.conj().T@aa;b=aa.conj().T@v[k]
        values,vectors=np.linalg.eigh(g);keep=values>max(float(values[-1])*1e-10,1e-14);ranks.append(int(keep.sum()));norms+=np.diag(g).real/nk
        inv=(vectors[:,keep]/np.sqrt(values[keep]))@vectors[:,keep].conj().T
        c=inv@b;power+=np.abs(c)**2/nk;home+=c/nk;raw_home+=b/nk
    home_power=np.abs(home)**2;rows=[]
    for n in range(nw):
        total=float(power[:,n].sum());home_total=float(home_power[:,n].sum());by_atom={}
        for i,ref in enumerate(references):by_atom[ref['atom']]=by_atom.get(ref['atom'],0)+float(power[i,n])
        owner=max(by_atom,key=by_atom.get);idx=[i for i,r in enumerate(references) if r['atom']==owner];owner_total=by_atom[owner];dominant=max(idx,key=lambda i:power[i,n]);top=references[dominant];share=float(power[dominant,n]/owner_total) if owner_total>1e-14 else 0
        fraction=owner_total/total if total>1e-14 else 0;local_fraction=home_total/total if total>1e-14 else 0
        status='LOW_COVERAGE' if total<.8 else 'LOW_HOME_OVERLAP' if local_fraction<.8 else 'CROSS_ATOM_MIXED' if fraction<.8 else 'DOMINANT_REFERENCE_CHARACTER' if share>=.8 else 'MIXED_REFERENCE_CHARACTER'
        label=(('局域 ' if top['frame']=='冻结局域配位轴' else '投影轴 ')+top['label']+'型 WF') if status=='DOMINANT_REFERENCE_CHARACTER' else {'LOW_COVERAGE':'参考覆盖不足','LOW_HOME_OVERLAP':'胞内参考占比较低 WF','CROSS_ATOM_MIXED':'跨原子参考分散 WF','MIXED_REFERENCE_CHARACTER':'多分量混合 WF'}[status]
        components=[{**references[i],'relative_weight':float(power[i,n]/owner_total) if owner_total>1e-14 else 0,'home_orthogonalized_weight':float(home_power[i,n]),'home_raw_overlap_abs2':float(abs(raw_home[i,n])**2)} for i in idx]
        rows.append({'wf':n+1,'atom':owner,'element':top['element'],'label':label,'status':status,'coverage':float(np.clip(total,0,1)),'home_fraction':float(np.clip(local_fraction,0,1)),'owner_fraction':float(np.clip(fraction,0,1)),'dominant_fraction':share,'components':components})
    return {'channel':channel,'num_wann':nw,'binding':{'hr_sha256':data['hr_evidence']['sha256'],'win_sha256':digest(win_path),'amn_sha256':digest(amn_path),'u_sha256':digest(u_path),'udis_sha256':digest(udis_path) if udis_path else None,'eig_sha256':digest(eig_path) if eig_path else None},'source':data.get('source',{}),'window_validation':({'status':'MATCHED_HR_DIAGONAL','layout':'Wannier90 packed outer-window rows','active_bands_min':min(counts),'active_bands_max':max(counts),'onsite_diagonal_residual_ev':energy_residual} if udis_path else {'status':'NO_DISENTANGLEMENT'}),'kmesh':grid,'reference_rank_min':min(ranks),'reference_count':nproj,'references':references,'reference_projected_norms':norms.tolist(),'orbitals':rows,'home_overlap':{'real':home.real.tolist(),'imag':home.imag.tolist()},'local_axes':[{'atom':r['atom'],'axes':r['coordination_candidate']['geometry']['axes'],'coordination':r['coordination_candidate'].get('coordination'),'polyhedron':r.get('polyhedron')} for r in data['reports'] if r.get('coordination_candidate')],'notes':notes+['B=A†V；先旋转参考复振幅，再按投影参考Gram矩阵的伪逆平方根正交化。','胞内使用复振幅的相干k平均；全胞贡献使用模平方平均，两者分别保留。胞内占比低也可能来自WF整晶胞平移，不能单凭它断言跨胞混合。','覆盖是能带窗口内投影参考张成子空间的覆盖；即使为1也不证明绝对原子d纯度。分量是同一原子参考内的相对权重，不是PAW占据。','跨胞分析属于所用均匀k网格的Born–von Karman周期，未声称无限晶体唯一轨道。','未改变hr、原WF基底或任何交换项；0.8阈值仅用于显示主成分/混合提示。'],'physical_validation':'NOT_PERFORMED'}

def compare_channels(models):
    if len(models)!=2:return {'status':'NOT_PAIRED','note':'需上下自旋两个身份报告才能比较共同基底'}
    a,b=models
    if a['channel']==b['channel'] or a['num_wann']!=b['num_wann'] or a['local_axes']!=b['local_axes'] or a['references']!=b['references']:return {'status':'INCOMPATIBLE','note':'通道、维度或局域轴不一致，不能按序做自旋差'}
    x=np.asarray(a['home_overlap']['real'])+1j*np.asarray(a['home_overlap']['imag']);y=np.asarray(b['home_overlap']['real'])+1j*np.asarray(b['home_overlap']['imag']);z=np.vdot(x,y);phase=z/abs(z) if abs(z)>1e-14 else 1
    residual=float(np.linalg.norm(y-phase*x)/max(np.linalg.norm(x),1e-14));agree=all(p['atom']==q['atom'] and p['label']==q['label'] for p,q in zip(a['orbitals'],b['orbitals']))
    strong=all(r['coverage']>=.99 and r['home_fraction']>=.99 and r['owner_fraction']>=.99 for m in models for r in m['orbitals'])
    return {'status':'REFERENCE_OVERLAPS_AGREE' if strong and agree and residual<.01 else 'BASIS_ALIGNMENT_NOT_ESTABLISHED','relative_complex_residual':residual,'labels_agree':agree,'note':'同时比较复振幅而非仅看成分概率；仅去除整个模型的共同相位，不分别消除每个轨道相位。参考一致仍不等于物理有效性验证。'}

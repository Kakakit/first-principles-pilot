"""Frozen-input EOS analysis. Streaming OUTCAR parsing; no host or SSH imports."""
import copy,csv,hashlib,json,math,re
from pathlib import Path
import numpy as np
from pymatgen.analysis.eos import EOS
from pymatgen.core import Composition,Lattice
from pymatgen.io.vasp import Poscar

EV_A3_TO_GPA=160.2176634
NUMBER=r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[EeDd][-+]?\d+)?'
SETTINGS=('ENCUT','PREC','EDIFF','ISMEAR','SIGMA','ISPIN','LSORBIT','LNONCOLLINEAR','LDAU','LDAUTYPE','LDAUU','LDAUJ','LDAUL','GGA','METAGGA','IVDW','NELECT','LASPH','NKPTS')
DEFAULTS={'energy':'energy_sigma0_ev','model':'birch_murnaghan','force_tolerance':0.02}
FIELDS={
 'volume_a3':('体积','Å³','volume'),'a_ang':('a','Å','length'),'b_ang':('b','Å','length'),'c_ang':('c','Å','length'),
 'alpha_deg':('α','°','angle'),'beta_deg':('β','°','angle'),'gamma_deg':('γ','°','angle'),'c_over_a':('c/a','1','ratio'),'b_over_a':('b/a','1','ratio'),
 'energy_sigma0_ev':('energy(sigma→0)','eV','energy'),'energy_without_entropy_ev':('energy without entropy','eV','energy'),'free_energy_ev':('free energy TOTEN','eV','energy'),'enthalpy_ev':('enthalpy is TOTEN','eV','energy'),
 'pstress_gpa':('PSTRESS（输入/修正）','GPa','pressure'),'external_pressure_gpa':('OUTCAR external pressure','GPa','pressure'),'eos_pressure_gpa':('EOS 求导压力','GPa','pressure'),
 'force_max_ev_ang':('最大原子力模长','eV/Å','force'),'force_rms_ev_ang':('RMS 原子力','eV/Å','force'),
 **{'stress_'+s+'_gpa':('VASP σ'+s+'（原符号）','GPa','pressure') for s in ('xx','yy','zz','xy','yz','zx')}}

def numbers(line):return [float(v.replace('D','E').replace('d','e')) for v in re.findall(NUMBER,line)]
def evidence(line,text,unit=None,value=None):return {'line':line,'text':text.rstrip(),'unit':unit,'value':value}
def scalar(text):
    text=text.strip().split(';')[0].strip();text=text.split('#')[0].strip()
    if re.fullmatch(NUMBER,text):return numbers(text)[0]
    return text

def parse_outcar(path):
    """A force block starts an ionic record; only energies following it belong to it.
    Lattice/stress are refreshed before POSITION; later incomplete records never
    inherit energy/force from the preceding record. Memory is O(atoms), not O(file).
    """
    context={};settings={};settings_evidence={};titles=[];counts=[];nions=None
    last_complete=None;record_count=0;active=None;force_rows=None;force_text=[];lattice_rows=None;lattice_text=[]
    electronic=False;ionic=False;timing=False;iteration=0;last_ediff=False
    with Path(path).open(errors='replace') as stream:
      for line_no,line in enumerate(stream,1):
        if 'TITEL' in line and '=' in line:
            title=line.split('=',1)[1].strip()
            if title not in titles:titles.append(title)
        if 'ions per type' in line:counts=[int(v) for v in numbers(line.split('=',1)[-1])]
        match=re.search(r'\bNIONS\s*=\s*(\d+)',line)
        if match:nions=int(match.group(1))
        for key in SETTINGS+('PSTRESS','ISIF','NSW','IBRION','EDIFFG'):
            match=re.search(r'\b'+key+r'\s*=\s*([^;\n]+)',line)
            if match:
                # Standard OUTCAR scalar lines include explanatory prose.
                token=match.group(1).strip();numeric=re.match(NUMBER,token)
                value=float(numeric.group().replace('D','E')) if numeric and key not in ('LDAUU','LDAUJ','LDAUL') else token.split('    ')[0].strip()
                if token.startswith(('T','F')):value=token.split()[0]
                settings[key]=value;settings_evidence[key]=evidence(line_no,line)
        if 'generate k-points for' in line:settings['kmesh']=line.strip()
        match=re.search(r'Iteration\s+(\d+)\s*\(',line)
        if match:
            new=int(match.group(1))
            if new!=iteration:
                last_ediff=False
                for key in ('stress','stress_evidence','pressure','pressure_evidence'):context.pop(key,None)
            iteration=new
        if 'aborting loop because EDIFF is reached' in line:
            last_ediff=True;electronic=True
        if 'reached required accuracy' in line:
            ionic=True
            if active is not None:active['ionic_accuracy']=True
        if 'General timing and accounting' in line:timing=True
        if lattice_rows is not None:
            vals=numbers(line)
            lattice_text.append(line.rstrip())
            if len(vals)>=3:lattice_rows.append(vals[:3])
            else:lattice_rows=None
            if lattice_rows is not None and len(lattice_rows)==3:
                context['lattice']=copy.deepcopy(lattice_rows);context['lattice_evidence']=evidence(lattice_start,'\n'.join(lattice_text),'Å',copy.deepcopy(lattice_rows));lattice_rows=None
            continue
        if 'direct lattice vectors' in line:
            lattice_start=line_no;lattice_rows=[];lattice_text=[line.rstrip()];continue
        match=re.search(r'volume of cell\s*:\s*('+NUMBER+')',line)
        if match:
            token=match.group(1);context['volume']=numbers(token)[0];context['volume_evidence']=evidence(line_no,line,'Å³',context['volume'])
            mantissa=re.split('[EeDd]',token)[0];decimals=len(mantissa.split('.')[1]) if '.' in mantissa else 0;exponent=int(re.split('[EeDd]',token)[1]) if re.search('[EeDd]',token) else 0
            context['volume_rounding_a3']=0.5*10**(exponent-decimals)
        if re.match(r'\s*in kB\s',line):
            vals=numbers(line.split('kB',1)[1])
            if len(vals)==6:context['stress']=vals;context['stress_evidence']=evidence(line_no,line,'kbar',vals)
        match=re.search(r'external pressure\s*=\s*('+NUMBER+')',line)
        if match:context['pressure']=numbers(match.group(1))[0];context['pressure_evidence']=evidence(line_no,line,'kbar',context['pressure'])
        if 'POSITION' in line and 'TOTAL-FORCE' in line:
            if active is not None:
                record_count+=1
                if active['energies'] and active['cell'].get('lattice') and active['cell'].get('volume') and active.get('forces') and (not nions or len(active['forces'])==nions):last_complete=active
            active={'step':iteration or record_count+1,'cell':copy.deepcopy(context),'energies':{},'energy_evidence':{},'electronic_ediff':last_ediff,'ionic_accuracy':False}
            force_rows=[];force_start=line_no;force_text=[line.rstrip()];continue
        if force_rows is not None:
            vals=numbers(line)
            force_text.append(line.rstrip())
            if len(vals)==6:force_rows.append(vals)
            elif force_rows and line.strip().startswith('---'):
                active['forces']=force_rows;active['force_evidence']=evidence(force_start,'\n'.join(force_text),'eV/Å');force_rows=None
            elif line.strip() and not line.strip().startswith('---') and len(vals)!=6:force_rows=None
            if force_rows is not None and nions and len(force_rows)==nions:
                active['forces']=force_rows;active['force_evidence']=evidence(force_start,'\n'.join(force_text),'eV/Å');force_rows=None
            continue
        if active is None or (iteration and iteration!=active['step']):continue
        patterns={'free_energy_ev':r'free\s+energy\s+TOTEN\s*=\s*('+NUMBER+')','energy_without_entropy_ev':r'energy\s+without entropy\s*=\s*('+NUMBER+')','energy_sigma0_ev':r'energy\(sigma->0\)\s*=\s*('+NUMBER+')','enthalpy_ev':r'enthalpy\s+is\s+TOTEN\s*=\s*('+NUMBER+')'}
        for key,pattern in patterns.items():
            match=re.search(pattern,line)
            if match:
                val=numbers(match.group(1))[0];active['energies'][key]=val;active['energy_evidence'][key]=evidence(line_no,line,'eV',val)
    if active is not None and active['energies'] and active['cell'].get('lattice') and active['cell'].get('volume') and active.get('forces') and (not nions or len(active['forces'])==nions):last_complete=active
    if last_complete is None:raise ValueError('OUTCAR 未找到晶格、体积、力和能量相互关联的完整离子步')
    record=last_complete;warnings=[]
    truncated=record is not active or iteration>record['step']
    if truncated:warnings.append('末尾离子步不完整，显示最后完整步；禁止用于拟合')
    record.update(settings=settings,settings_evidence=settings_evidence,titles=titles,counts=counts,nions=nions,
                  timing=timing,trailing_incomplete=truncated,warnings=warnings,ionic_accuracy=record.get('ionic_accuracy',False))
    return record


def read_incar(path):
    result={}
    for line in Path(path).read_text(errors='replace').splitlines():
        for part in re.split(';',re.split('[#!]',line)[0]):
            if '=' in part:
                key,val=part.split('=',1);result[key.strip().upper()]=scalar(val)
    return result


def extract_point(source,inputs,options):
    record=parse_outcar(inputs['outcar']['path']);settings=record['settings'];ev=record['energy_evidence'];cell=record['cell'];warnings=record['warnings'][:]
    if 'incar' in inputs:
        actual=read_incar(inputs['incar']['path'])
        for key in SETTINGS+('PSTRESS','ISIF','NSW','IBRION','EDIFFG','MAGMOM'):
            if key in actual:settings[key]=actual[key]
    lattice=Lattice(cell['lattice']);volume=float(lattice.volume)
    mismatch=not math.isclose(volume,cell['volume'],rel_tol=2e-7,abs_tol=cell.get('volume_rounding_a3',0)+1e-5)
    if mismatch:warnings.append('OUTCAR 晶格行列式与打印体积不一致，禁止拟合')
    symbols=[]
    for title in record['titles']:
        match=re.search(r'\b([A-Z][a-z]?)(?:_[a-zA-Z0-9]+)?\s+(?:\d|$)',title)
        if match:symbols.append(match.group(1))
    composition=None
    if len(symbols)==len(record['counts']):composition=Composition({s:sum(c for x,c in zip(symbols,record['counts']) if x==s) for s in symbols})
    if 'structure' in inputs:
        entry=inputs['structure'];structure=Poscar.from_file(entry['path'],check_for_potcar=False).structure
        if composition is None:composition=structure.composition
        if entry['name']=='CONTCAR':
            if not np.allclose(structure.lattice.matrix,lattice.matrix,rtol=2e-5,atol=2e-4):warnings.append('CONTCAR 与 OUTCAR 最终晶格不一致，禁止拟合');mismatch=True
        else:warnings.append('POSCAR 为初始结构；绘图晶格来自 OUTCAR 最终完整步')
    if composition is None:raise ValueError('无法确认元素组成，请提供 POSCAR 或 CONTCAR')
    count=float(composition.num_atoms);formula,factor=composition.get_reduced_composition_and_factor()
    # Composition reduces elemental cells to e.g. Ce, not molecular conventions.
    norms=np.linalg.norm(np.asarray(record['forces'])[:,3:],axis=1);values={k:None for k in FIELDS}
    values.update(volume_a3=volume,a_ang=lattice.a,b_ang=lattice.b,c_ang=lattice.c,alpha_deg=lattice.alpha,beta_deg=lattice.beta,gamma_deg=lattice.gamma,
                  c_over_a=lattice.c/lattice.a,b_over_a=lattice.b/lattice.a,force_max_ev_ang=float(norms.max()),force_rms_ev_ang=float(np.sqrt(np.mean(norms**2))))
    values.update(record['energies'])
    pstress=settings.get('PSTRESS')
    if isinstance(pstress,(int,float)):values['pstress_gpa']=pstress*0.1
    if 'pressure' in cell:values['external_pressure_gpa']=cell['pressure']*0.1
    if 'stress' in cell:
        values.update({'stress_'+s+'_gpa':v*0.1 for s,v in zip(('xx','yy','zz','xy','yz','zx'),cell['stress'])})
    evidence_map={**ev,'lattice':cell['lattice_evidence'],'volume':dict(cell['volume_evidence'],rounding_half_unit_a3=cell.get('volume_rounding_a3')),'force':record['force_evidence'],'pressure':cell.get('pressure_evidence'),'stress':cell.get('stress_evidence'),'settings':record['settings_evidence']}
    signature={k:settings.get(k,'未提供') for k in SETTINGS}
    signature.update(pseudopotentials=record['titles'],kmesh=settings.get('kmesh','未提供'),initial_magmom=settings.get('MAGMOM','未提供'))
    if 'kpoints' in inputs:signature['kpoints']='\n'.join(Path(inputs['kpoints']['path']).read_text(errors='replace').splitlines()[1:]).strip()
    group=hashlib.sha256(json.dumps([str(formula),signature],sort_keys=True).encode()).hexdigest()[:10]
    reasons=[]
    if record['trailing_incomplete']:reasons.append('末尾离子步不完整')
    if mismatch:reasons.append('输出晶格/体积核对不一致')
    if not record['timing']:reasons.append('未找到 VASP 程序结束标记')
    if not record['electronic_ediff']:reasons.append('所选离子步未记录 EDIFF 达成')
    stage=source.get('stage','scf');force_limit=float(options.get('force_tolerance',0.02))
    if stage=='relax' and not record['ionic_accuracy']:reasons.append('离子弛豫未确认（力阈值诊断不替代停止证据）')
    if not isinstance(pstress,(int,float)):reasons.append('PSTRESS 未保存，压力能量定义未确认')
    elif pstress!=0:reasons.append('非零 PSTRESS：能量是否含 PV 未确认')
    if record.get('nions') and count!=record['nions']:reasons.append('结构组成与 OUTCAR NIONS 不一致')
    if any(signature[k]=='未提供' for k in ('ENCUT','ISMEAR','SIGMA','ISPIN','NKPTS')):reasons.append('计算设置证据不完整')
    return {'id':source['id'],'label':source.get('name') or source['id'],'job_id':source.get('job_id') or source.get('name'),
            'source':source,'files':{k:dict(v,path=Path(v['path']).name) for k,v in inputs.items()},'step':record['step'],
            'composition':composition.as_dict(),'formula':str(formula),'n_atoms':count,'formula_units':float(factor),'lattice_angstrom':cell['lattice'],
            'values':values,'evidence':evidence_map,'settings':settings,'settings_signature':signature,'group':group,
            'validation':{'program_finished':record['timing'],'electronic_ediff':record['electronic_ediff'],
                          'ionic_accuracy':record['ionic_accuracy'],'force_below_tolerance':bool(norms.max()<=force_limit),'force_tolerance_ev_ang':force_limit,'stage':stage},
            'fit_blockers':reasons,'warnings':warnings}


def pressure(volume,params,model):
    e0,b0,b1,v0=params;volume=np.asarray(volume);eta=(v0/volume)**(1/3)
    if model=='birch_murnaghan':return 1.5*b0*(eta**7-eta**5)*(1+0.75*(b1-4)*(eta**2-1))*EV_A3_TO_GPA
    if model=='vinet':
        x=(volume/v0)**(1/3);return 3*b0*(1-x)/x**2*np.exp(1.5*(b1-1)*(1-x))*EV_A3_TO_GPA
    if model=='murnaghan':return b0/b1*((v0/volume)**b1-1)*EV_A3_TO_GPA
    raise ValueError('未知 EOS 模型')


def fit_groups(points,selection):
    energy=selection.get('energy',DEFAULTS['energy']);model=selection.get('model',DEFAULTS['model'])
    if energy not in ('energy_sigma0_ev','energy_without_entropy_ev','free_energy_ev'):raise ValueError('请选择可用于 E–V 的能量字段')
    if model not in ('birch_murnaghan','vinet','murnaghan'):raise ValueError('未知 EOS 模型')
    overrides=selection.get('groups') or {};excluded=set(selection.get('excluded') or []);grouped={}
    for p in points:grouped.setdefault(str(overrides.get(p['id']) or p['group'])[:80],[]).append(p)
    results=[]
    for name,items in grouped.items():
        dropped=[];eligible=[]
        for p in items:
            reasons=p['fit_blockers'][:]
            if p['id'] in excluded:reasons.append('用户排除')
            if p['values'][energy] is None:reasons.append('所选能量字段未提供')
            if reasons:dropped.append({'id':p['id'],'label':p['label'],'reasons':reasons})
            else:eligible.append(p)
        fit={'group':name,'model':model,'energy':energy,'status':'UNAVAILABLE','excluded':dropped,'point_ids':[p['id'] for p in eligible],'warnings':[]}
        signatures={json.dumps(p['settings_signature'],sort_keys=True) for p in eligible};compositions={json.dumps(p['composition'],sort_keys=True) for p in eligible}
        if len(signatures)>1 or len(compositions)>1:fit['reason']='组内组成或计算设置不一致，请拆组';results.append(fit);continue
        # Fit per atom: cell replication never changes the material bulk modulus.
        v=np.array([p['values']['volume_a3']/p['n_atoms'] for p in eligible]);e=np.array([p['values'][energy]/p['n_atoms'] for p in eligible])
        if len(v)<6 or len(np.unique(np.round(v,8)))<6:fit['reason']='至少需要 6 个不同体积的可用点';results.append(fit);continue
        order=np.argsort(v);v=v[order];e=e[order];index=int(np.argmin(e))
        if index in (0,len(e)-1):fit['reason']='最低能量点两侧都需要体积点';results.append(fit);continue
        try:
            eos=EOS(model).fit(v,e);params=np.asarray(eos.eos_params,dtype=float);e0,b0,b1,v0=params
            if not np.all(np.isfinite(params)) or b0<=0 or not v[0]<v0<v[-1]:raise ValueError('非物理体模量或平衡体积超出拟合范围')
            grid=np.linspace(v[0],v[-1],160);energies=eos.func(grid);pressures=pressure(grid,params,model);residual=e-eos.func(v)
            if not np.all(np.isfinite(pressures)):raise ValueError('求导压力不有限')
            fit.update(status='FITTED',params={'e0_ev_atom':float(e0),'v0_a3_atom':float(v0),'b0_gpa':float(b0*EV_A3_TO_GPA),'b1':float(b1)},
                       eos_params=params.tolist(),rmse_ev_atom=float(np.sqrt(np.mean(residual**2))),max_residual_ev_atom=float(np.max(np.abs(residual))),
                       residuals=[{'id':eligible[i]['id'],'volume_a3_atom':float(vv),'residual_ev_atom':float(rr)} for i,vv,rr in zip(order,v,residual)],
                       curve=[{'volume_a3_atom':float(vv),'energy_ev_atom':float(ee),'pressure_gpa':float(pp)} for vv,ee,pp in zip(grid,energies,pressures)])
            if len(v)<9:fit['warnings'].append('点数较少，建议检查 9–15 个体积点及模型敏感性')
        except Exception as exc:fit['reason']='拟合未通过：'+str(exc)
        results.append(fit)
    return results


def collect(request):
    options={**DEFAULTS,**request.get('options',{})};by_source={s['id']:{} for s in request['sources']}
    for role,items in request['inputs'].items():
        for item in items:
            if item['source_id'] in by_source:by_source[item['source_id']][role]=item
    points=[]
    for source in request['sources']:
        try:points.append(extract_point(source,by_source[source['id']],options))
        except (ValueError,OSError,KeyError) as exc:raise ValueError((source.get('name') or source['id'])+'：'+str(exc)) from exc
    return {'schema':'fpilot.pressure-eos.v1','version':'1.0.0','points':points,'options':options,
            'fields':{k:{'label':v[0],'unit':v[1],'kind':v[2]} for k,v in FIELDS.items()},
            'conversion':{'ev_a3_to_gpa':EV_A3_TO_GPA,'kbar_to_gpa':0.1},'scientific_validation':'NOT_PERFORMED'}


def write_csv(path,data):
    with Path(path).open('w',newline='',encoding='utf-8-sig') as out:
        w=csv.writer(out);w.writerow(['source_id','name','job_id','step','group','n_atoms','formula_units']+[k+' ['+v[1]+']' for k,v in FIELDS.items()]+['fit_blockers'])
        for p in data['points']:w.writerow([p[k] for k in ('id','label','job_id','step','group','n_atoms','formula_units')]+[p['values'][k] for k in FIELDS]+['; '.join(p['fit_blockers'])])

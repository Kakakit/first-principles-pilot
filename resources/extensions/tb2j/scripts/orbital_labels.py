"""Initial-projector labels only; never infer final Wannier orbital purity."""
import math
import re
from pathlib import Path

ORDERS={'s':['s'],'p':['pz','px','py'],'d':['dz2','dxz','dyz','dx2-y2','dxy']}


def block(text,name):
    match=re.search(r'^\s*begin\s+'+name+r'\s*\n(.*?)^\s*end\s+'+name+r'\s*$',text,re.M|re.S|re.I)
    return match.group(1).strip() if match else ''


def clean(text):
    return '\n'.join(re.split(r'[!#]',line,1)[0].strip() for line in text.splitlines())


def angular(spec):
    output=[]
    for part in spec.split(';'):
        part=part.strip().lower()
        if part in ORDERS:output+=ORDERS[part];continue
        if part in [v for values in ORDERS.values() for v in values]:output.append(part);continue
        match=re.fullmatch(r'l\s*=\s*([012])(?:\s*,\s*mr\s*=\s*([1-5,\s]+))?',part)
        if not match:raise ValueError('投影含未支持的随机、杂化或角动量定义')
        order=ORDERS['spd'[int(match[1])]]
        indices=[int(v.strip())-1 for v in match[2].split(',')] if match[2] else list(range(len(order)))
        if any(i<0 or i>=len(order) for i in indices):raise ValueError('投影 mr 序号越界')
        output += [order[i] for i in indices]
    # Wannier90 internally orders multiple shells on one line by increasing l.
    return sorted(output,key=lambda v:next(i for i,values in enumerate(ORDERS.values()) if v in values))


def projectors(text,mode,groupby):
    text=clean(text);atoms=block(text,'atoms_cart') or block(text,'atoms_frac')
    symbols=[m[1] for line in atoms.splitlines() for m in [re.match(r'^([A-Z][a-z]?)(?:\d*)\s+[-+.0-9]',line)] if m]
    if not symbols:raise ValueError('冻结 .win 没有可识别的原子顺序')
    if re.search(r'^\s*(?:auto_projections|use_bloch_phases)\s*[:=]\s*(?:true|\.true\.|t)\b',text,re.M|re.I) or re.search(r'^\s*select_projections\s*[:=]',text,re.M|re.I):raise ValueError('自动/筛选投影缺少确定的序号对应')
    projection=block(text,'projections')
    if not projection:raise ValueError('冻结 .win 缺少 projections')
    counts={};sites={}
    for i,s in enumerate(symbols,1):counts[s]=counts.get(s,0)+1;sites[s+str(counts[s])]=i
    spatial=[]
    for row,line in enumerate(projection.splitlines(),1):
        if line.lower() in ('ang','angstrom','bohr'):continue
        pieces=line.split(':')
        if len(pieces)<2:raise ValueError('投影不是明确的原子 s/p/d 定义')
        site,spec=pieces[0].strip(),pieces[1].strip()
        if '(' in spec or '[' in spec:raise ValueError('自定义自旋投影需显式基底证据，保留原始序号')
        indices=[i for i,s in enumerate(symbols,1) if site==s] or ([sites[site]] if site in sites else [])
        if not indices:raise ValueError('投影中心不能唯一对应冻结原子')
        for atom in indices:
            for label in angular(spec):spatial.append({'atom':atom,'label':label,'projection_row':row,'axes':':'.join(pieces[2:]) or '默认全局 x/y/z'})
    entries=spatial
    if mode=='spinor':
        if groupby not in ('spin','orbital'):raise ValueError('SOC 未记录基底顺序，保留原始序号')
        entries=[{**p,'spin':spin} for spin in ('up','down') for p in spatial] if groupby=='spin' else [{**p,'spin':spin} for p in spatial for spin in ('up','down')]
    count=re.search(r'^\s*num_wann\s*[:=]\s*(\d+)',text,re.M|re.I)
    if not count or len(entries)!=int(count[1]):raise ValueError('投影展开数量与 num_wann 不一致')
    return symbols,sites,projection,entries


def centre_membership(win,rows,atom):
    centres=win.with_name(win.stem+'_centres.xyz')
    if not centres.is_file():return
    text=clean(win.read_text());cell=block(text,'unit_cell_cart').splitlines();fractional=bool(block(text,'atoms_frac'));sites=(block(text,'atoms_frac') or block(text,'atoms_cart')).splitlines()
    def unit(lines):
        factor=1
        if lines and len(lines[0].split())==1:
            value=lines.pop(0).lower()
            if value not in ('bohr','ang','angstrom'):raise ValueError('中心核对缺少明确坐标单位')
            factor=.529177210903 if value=='bohr' else 1
        return factor
    cf=unit(cell);sf=unit(sites);lattice=[[float(x)*cf for x in line.split()] for line in cell]
    if len(lattice)!=3 or any(len(v)!=3 for v in lattice):raise ValueError('中心核对缺少冻结晶胞')
    positions=[[float(x)*sf for x in line.split()[1:]] for line in sites]
    if any(len(v)!=3 for v in positions):raise ValueError('中心核对缺少冻结原子坐标')
    if fractional:positions=[[sum(v[k]*lattice[k][j] for k in range(3)) for j in range(3)] for v in positions]
    def dot(a,b):return sum(x*y for x,y in zip(a,b))
    def cross(a,b):return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
    reciprocal=[cross(lattice[1],lattice[2]),cross(lattice[2],lattice[0]),cross(lattice[0],lattice[1])];det=dot(lattice[0],reciprocal[0])
    if abs(det)<1e-12:raise ValueError('冻结晶胞退化')
    def distance(a,b):
        delta=[x-y for x,y in zip(a,b)];f=[dot(delta,v)/det for v in reciprocal];near=[round(v) for v in f]
        # Nearby images suffice only as a corroboration: ambiguity disables labels.
        return min(math.sqrt(sum((delta[j]-sum((near[k]+shift[k])*lattice[k][j] for k in range(3)))**2 for j in range(3))) for shift in __import__('itertools').product((-1,0,1),repeat=3))
    lines=centres.read_text().splitlines();n=int(lines[0]);nw=int(re.search(r'^num_wann\s*[:=]\s*(\d+)',text,re.M|re.I)[1])
    if n!=nw+len(positions) or len(lines)<n+2:raise ValueError('中心文件数量与冻结模型不一致')
    for _,wf,_ in rows:
        values=lines[wf+1].split()
        if len(values)!=4 or any(not math.isfinite(float(v)) for v in values[1:]):raise ValueError('中心文件坐标无效')
        centre=[float(v) for v in values[1:]];distances=[distance(centre,pos) for pos in positions];ordered=sorted(distances)
        if distances[atom-1]>ordered[0]+1e-5 or len(ordered)>1 and ordered[1]-ordered[0]<1e-5:raise ValueError('WF 中心不能唯一支持 assigned_basis 的原子归属')


def metadata(source,full,bounds,options):
    atoms={}
    for row in full['exchange']:
        for end in ('i','j'):atoms[row['atom_'+end]]=row['symbol_'+end]
    wins=sorted((source/'model').glob('*.win'));basis_path=source/'TB2J_results/assigned_basis.txt'
    basis=basis_path.read_text() if basis_path.is_file() else ''
    result={'atoms':[{'index':i,'symbol':s,'orbitals':bounds.get(i,0)} for i,s in sorted(atoms.items())],'basis_text':basis,'basis_rows':[],'projections':'','candidate_mapping':[],'automatic_mapping':[],'label_notes':[]}
    try:result['basis_rows']=assigned_indices(source,full,bounds)
    except ValueError as exc:result['label_notes'].append('原始编号关联不可用：'+str(exc))
    try:
        if not wins:raise ValueError('本机未保存冻结 .win')
        parsed=[projectors(p.read_text(),full['mode'],options.get('groupby')) for p in wins]
        symbols,sites,projection,entries=parsed[0];result['projections']=projection
        if any((p[0],p[3])!=(symbols,entries) for p in parsed[1:]):raise ValueError('上下自旋的初始投影或原子顺序不一致')
        if any(i>len(symbols) or symbols[i-1]!=s for i,s in atoms.items()):raise ValueError('冻结模型与交换结果原子顺序不一致')
        if not basis:raise ValueError('本机未保存 assigned_basis.txt')
        assigned={};seen=set()
        for line in basis.splitlines():
            m=re.fullmatch(r'\s*([A-Z][a-z]?\d+)\|([^\s]+)\s+(\d+)\s*',line)
            if not m or m[1] not in sites:raise ValueError('基底行不能对应原子与 Wannier 序号')
            atom=sites[m[1]];wf=int(m[3]);local=re.fullmatch(r'orb_(\d+)',m[2]);local=int(local[1]) if local else len(assigned.get(atom,[]))+1
            if wf<1 or wf>len(entries) or wf in seen:raise ValueError('基底 Wannier 序号重复或越界')
            seen.add(wf);assigned.setdefault(atom,[]).append((local,wf,m[2]))
        for atom,size in bounds.items():
            rows=sorted(assigned.get(atom,[]));labels=[]
            if [r[0] for r in rows]!=list(range(1,len(rows)+1)):raise ValueError('原子内基底序号缺失或重复')
            if len(rows)==size:
                labels=[entries[wf-1] for _,wf,_ in rows]
            elif full['mode']=='spinor' and len(rows)==2*size:
                raw=[entries[wf-1] for _,wf,_ in rows]
                pairs=list(zip(raw[:size],raw[size:])) if options.get('groupby')=='spin' else list(zip(raw[::2],raw[1::2]))
                if any(a['label']!=b['label'] or a['axes']!=b['axes'] for a,b in pairs):raise ValueError('SOC 自旋配对的投影名称或坐标轴不一致')
                labels=[a for a,_ in pairs]
            else:raise ValueError('基底数与实际轨道矩阵维度不一致')
            if any(p['atom']!=atom for p in labels):raise ValueError('WF 归属与初始投影中心不一致；不能自动套用轨道名称')
            # Centres corroborate membership; they cannot identify angular character.
            for win in wins:centre_membership(win,rows,atom)
            for oi,p in enumerate(labels,1):result['automatic_mapping'].append({'atom_index':atom,'orbital_index':oi,'label':p['label'],'group':'','source':'初始投影参考','projection_row':p['projection_row'],'axes':p['axes']})
        result['candidate_mapping']=result['automatic_mapping']
        result['label_notes'].append('名称自动对应冻结初始投影与 assigned_basis；不代表最终 Wannier 函数为纯原子轨道，不自动判定局域 t₂g/e_g。')
    except (ValueError,IndexError,KeyError,TypeError) as exc:
        result['automatic_mapping']=[];result['candidate_mapping']=[];result['label_notes'].append('自动标签不可用：'+str(exc)+'。无需手工填写也可继续查看总 J 和原始轨道矩阵。')
    return result

def assigned_indices(source,full,bounds):
    """Resolve native orb→WF independently of initial angular-projector guesses."""
    wins=sorted((source/'model').glob('*.win'));path=source/'TB2J_results/assigned_basis.txt'
    if not wins or not path.is_file():return []
    text=clean(wins[0].read_text());atoms=block(text,'atoms_cart') or block(text,'atoms_frac')
    symbols=[m[1] for line in atoms.splitlines() for m in [re.match(r'^([A-Z][a-z]?)(?:\d*)\s+[-+.0-9]',line)] if m];counts={};sites={}
    for i,s in enumerate(symbols,1):counts[s]=counts.get(s,0)+1;sites[s+str(counts[s])]=i
    count=re.search(r'^\s*num_wann\s*[:=]\s*(\d+)',text,re.M|re.I)
    if not count:return []
    n=int(count[1]);rows=[];seen=set();local=set()
    for line in path.read_text().splitlines():
        m=re.fullmatch(r'\s*([A-Z][a-z]?\d+)\|orb_(\d+)\s+(\d+)\s*',line)
        if not m or m[1] not in sites:raise ValueError('assigned_basis不能对应冻结原子与WF')
        atom=sites[m[1]];oi=int(m[2]);wf=int(m[3])
        if wf in seen or not 1<=wf<=n or oi<1 or (atom,oi) in local:raise ValueError('assigned_basis编号重复或越界')
        seen.add(wf);local.add((atom,oi));rows.append({'atom_index':atom,'orbital_index':oi,'wannier_index':wf})
    for row in full['exchange']:
        for end in ['i','j']:
            ai=row['atom_'+end]
            if not 1<=ai<=len(symbols) or symbols[ai-1]!=row['symbol_'+end]:raise ValueError('assigned_basis结构与交换原子不符')
    for atom,size in bounds.items():
        indices=sorted(r['orbital_index'] for r in rows if r['atom_index']==atom)
        if indices!=list(range(1,size+1)) and not (full['mode']=='spinor' and indices==list(range(1,2*size+1))):raise ValueError('assigned_basis与轨道矩阵维度不符')
    return [r for r in rows if r['atom_index'] in bounds]

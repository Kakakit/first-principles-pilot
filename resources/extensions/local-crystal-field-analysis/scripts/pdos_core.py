"""VASP atom-resolved DOS evidence and piecewise-linear spectral moments."""
import hashlib
import math
import re
from pathlib import Path
import numpy as np
from pymatgen.core import Structure

LABELS=['s','py','pz','px','dxy','dyz','dz2','dxz','dx2-y2']
SHELLS={0:['s'],1:['py','pz','px'],2:['dxy','dyz','dz2','dxz','dx2-y2']}

def values(line):
    row=[float(v.replace('D','E').replace('d','e')) for v in line.split()]
    if not row or not all(math.isfinite(v) for v in row):raise ValueError('DOSCAR 存在空行或非有限数值')
    return row

def spectral_moment(energy,density,lower,upper):
    """Exact zeroth/first moment of the linearly interpolated samples."""
    x=np.asarray(energy,dtype=float);y=np.asarray(density,dtype=float)
    if x.shape!=y.shape or len(x)<2 or not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)) or np.any(np.diff(x)<=0):raise ValueError('能量网格必须有限且严格递增')
    if not math.isfinite(lower) or not math.isfinite(upper) or not x[0]<=lower<upper<=x[-1]:raise ValueError('积分区间须在冻结能量范围内且下界小于上界')
    xx=np.r_[lower,x[(x>lower)&(x<upper)],upper];yy=np.interp(xx,x,y)
    dx=np.diff(xx);a=xx[:-1];b=xx[1:];u=yy[:-1];v=yy[1:]
    weight=float(np.sum(dx*(u+v)/2));first=float(np.sum(dx*((2*a+b)*u+(a+2*b)*v)/6))
    return {'weight':weight,'first_moment_ev':first,'centroid_ev':first/weight if weight>0 else None,'negative_values':bool(np.any(yy<0))}

def outcar_metadata(path):
    result={'spin':1,'noncollinear':False,'soc':False,'nsw':None,'program_finished':False,'ediff_reached':False};digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for raw in stream:
            digest.update(raw);line=raw.decode(errors='replace')
            for key,tag in [('spin','ISPIN'),('nsw','NSW')]:
                m=re.search(r'\b'+tag+r'\s*=\s*(\d+)',line)
                if m:result[key]=int(m.group(1))
            for key,tag in [('soc','LSORBIT'),('noncollinear','LNONCOLLINEAR')]:
                m=re.search(r'\b'+tag+r'\s*=\s*\.?([TF])',line,re.I)
                if m:result[key]=m.group(1).upper()=='T'
            result['program_finished']|='General timing and accounting informations' in line
            result['ediff_reached']|='aborting loop because EDIFF is reached' in line
    result['noncollinear']|=result['soc'];result['sha256']=digest.hexdigest();return result

def analyze_pdos(doscar,structure,outcar):
    atoms=Structure.from_file(str(structure));meta=outcar_metadata(outcar);warnings=[]
    if meta['spin'] not in (1,2):raise ValueError('OUTCAR ISPIN 无效')
    if not meta['program_finished']:raise ValueError('OUTCAR 缺少 VASP 程序结束标志')
    if meta['nsw'] and meta['nsw']>1:warnings.append('NSW > 1：DOSCAR 可能为离子步平均，不代表单一最终结构')
    if not meta['ediff_reached']:warnings.append('OUTCAR 未提供明确 EDIFF 达标证据；仅供轨道分布参考')
    digest=hashlib.sha256();line_number=0
    with Path(doscar).open('rb') as stream:
        def line():
            nonlocal line_number
            raw=stream.readline();line_number+=1;digest.update(raw)
            if not raw:raise ValueError('DOSCAR 截断：第 '+str(line_number)+' 行')
            return raw.decode()
        first=line().split()
        if len(first)<3 or int(first[1])!=len(atoms):raise ValueError('DOSCAR 原子数与结构不一致')
        if int(first[2])!=1:raise ValueError('DOSCAR 没有逐原子投影；需要输出 lm 分辨 PDOS 的 DOS 结果')
        for _ in range(4):line()
        header=values(line())
        if len(header)<4:raise ValueError('DOSCAR 总 DOS 头缺失')
        n=int(header[2]);fermi=header[3]
        if header[2]!=n or n<2 or n>1000000:raise ValueError('DOSCAR NEDOS 无效')
        total=[values(line()) for _ in range(n)];total_width=len(total[0])
        if total_width not in (3,5) or any(len(r)!=total_width for r in total):raise ValueError('DOSCAR 总 DOS 列数不一致')
        spin_count=1 if meta['noncollinear'] else (2 if meta['spin']==2 else 1)
        if total_width!=(5 if spin_count==2 else 3):raise ValueError('OUTCAR 自旋设置与 DOSCAR 总 DOS 列数不符')
        energy=np.array([r[0] for r in total]);relative=energy-fermi
        if np.any(np.diff(energy)<=0):raise ValueError('DOSCAR 能量网格不是严格递增')
        records=[];negative=False;basis=None
        for index,site in enumerate(atoms):
            start=line_number+1;h=values(line())
            if len(h)<4 or int(h[2])!=n or abs(h[3]-fermi)>1e-5:raise ValueError('DOSCAR 原子投影头与总 DOS 不一致')
            rows=[values(line()) for _ in range(n)]
            width=len(rows[0]);components=4 if meta['noncollinear'] else spin_count
            if any(len(r)!=width for r in rows) or (width-1)%components:raise ValueError('DOSCAR 原子投影列数不一致')
            count=(width-1)//components
            if count not in (3,4,9,16):raise ValueError('不支持的 DOSCAR 轨道列数：'+str(count))
            if basis is not None and basis!=count:raise ValueError('DOSCAR 不同原子的轨道列数不一致')
            basis=count;arr=np.asarray(rows,dtype=float)
            if not np.allclose(arr[:,0],energy,rtol=0,atol=1e-7):raise ValueError('DOSCAR 原子投影与总 DOS 能量网格不一致')
            labels=LABELS if count in (9,16) else ['s','p','d']
            shells=[]
            for l,names in SHELLS.items():
                if count in (3,4) and l!=0:continue
                spins=[]
                for spin in range(spin_count):
                    traces=[arr[:,1+labels.index(name)*components+spin].tolist() for name in names]
                    negative|=any(min(t)<0 for t in traces)
                    moments=[spectral_moment(relative,t,float(relative[0]),float(relative[-1])) for t in traces]
                    spins.append({'label':('电荷 DOS（SOC/非共线）' if meta['noncollinear'] else '↑' if spin_count==2 and spin==0 else '↓' if spin_count==2 else '无自旋分辨'),'density':traces,'full_range_moments':moments})
                shells.append({'l':l,'labels':names,'spins':spins})
            records.append({'atom':index+1,'element':site.specie.symbol,'shells':shells,'evidence_lines':[start,line_number]})
        for raw in stream:
            digest.update(raw)
            if raw.strip():raise ValueError('DOSCAR 标称原子块后存在额外数据')
    if basis in (3,4):warnings.append('只有 l 分辨总量：p/d 未拆分，不能输出三个 p 或五个 d 轨道能级')
    if basis==16 or basis==4:warnings.append('源文件包含 f 投影；本版仅展示 s/p/d')
    if negative:warnings.append('PDOS 存在负值，保留原值积分；积分权重非正时不生成重心')
    warnings.extend(['轨道重心是选定积分区间的谱平均位置，不是局域本征能级或光学激发能','使用 VASP 全局轨道坐标；仅凭各条 PDOS 不能任意旋转局域基底','默认完整范围是文件已保存的范围，不保证包含全部轨道谱权重'])
    return {'schema':'fpilot.pdos-levels.v1','energy_ev':relative.tolist(),'energy_reference':'E − E_F','fermi_ev':fermi,'atoms':records,'settings':meta,'warnings':warnings,'evidence':{'doscar_sha256':digest.hexdigest(),'doscar_lines':line_number,'structure_sha256':hashlib.sha256(Path(structure).read_bytes()).hexdigest(),'outcar_sha256':meta['sha256'],'orbital_columns':basis,'frame':'VASP global Cartesian axes'},'mode':'pdos'}

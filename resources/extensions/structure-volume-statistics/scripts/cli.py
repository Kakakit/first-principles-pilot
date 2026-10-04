#!/usr/bin/env python3
"""Working, standard-library-only example. Copy this package and rename its ID."""
import csv,math
from pathlib import Path
from protocol_cli import main,file,dump,artifact

def geometry(path):
    lines=path.read_text(encoding='utf-8-sig').splitlines()
    if len(lines)<8: raise ValueError('需要完整 VASP5/6 POSCAR')
    scale=float(lines[1]);cell=[[float(x) for x in line.split()[:3]] for line in lines[2:5]]
    if len(lines[1].split())!=1 or not all(len(v)==3 for v in cell): raise ValueError('模板支持单一比例因子')
    a,b,c=cell
    determinant=a[0]*(b[1]*c[2]-b[2]*c[1])-a[1]*(b[0]*c[2]-b[2]*c[0])+a[2]*(b[0]*c[1]-b[1]*c[0])
    if not math.isfinite(determinant) or abs(determinant)<1e-12 or scale==0: raise ValueError('晶胞体积无效')
    volume=abs(determinant)*scale**3 if scale>0 else -scale
    elements=lines[5].split();counts=[int(x) for x in lines[6].split()]
    if len(elements)!=len(counts) or not all(n>0 for n in counts) or sum(counts)>1500: raise ValueError('元素和计数无效')
    start=8 if not lines[7].lower().startswith('s') else 9
    if len(lines)<start+sum(counts): raise ValueError('原子坐标不完整')
    for line in lines[start:start+sum(counts)]:
        values=[float(x) for x in line.split()[:3]]
        if len(values)!=3 or not all(math.isfinite(x) for x in values): raise ValueError('坐标无效')
    return volume,elements,counts

def check(request):
    geometry(file(request,'structure'));return '完整几何可用于统计'

def analyze(request):
    volume,elements,counts=geometry(file(request,'structure'))
    out=Path(request['output_dir']);data={'volume_angstrom3':volume,'atom_count':sum(counts),'elements':dict(zip(elements,counts))}
    dump(out/'statistics.json',data)
    with (out/'elements.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.writer(stream);writer.writerow(['element','count']);writer.writerows(zip(elements,counts))
    tables=[{'title':'几何统计','columns':[{'key':'volume','label':'体积','unit':'Å³'},{'key':'atoms','label':'原子数','unit':''}], 'rows':[{'volume':volume,'atoms':sum(counts)}]}]
    if request['options'].get('include_elements',True): tables.append({'title':'元素明细','columns':[{'key':'element','label':'元素','unit':''},{'key':'count','label':'数量','unit':''}], 'rows':[{'element':e,'count':n} for e,n in zip(elements,counts)]})
    return {'summary':f'体积 {volume:.6f} Å³；{sum(counts)} 个原子','tables':tables,'artifacts':[artifact(out/'statistics.json','统计 JSON'),artifact(out/'elements.csv','元素 CSV','text/csv')],'warnings':['几何统计不代表 DFT 输入合理或数值收敛。']}

if __name__=='__main__':raise SystemExit(main(check,analyze))

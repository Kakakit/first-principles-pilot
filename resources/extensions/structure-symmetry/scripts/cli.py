#!/usr/bin/env python3
from pathlib import Path
from protocol_cli import main,file,dump,artifact

def check(request):
    from symmetry import parse_structure
    angle=request['options']['angle_tolerance_degree']
    if not 0<float(angle)<=30: raise ValueError('角度容差须在 0–30°')
    parse_structure(file(request,'structure'));return '结构可解析；比较三个几何容差'

def analyze(request):
    from symmetry import analyze as original
    data=original(file(request,'structure'),request['inputs']['structure'][0]['name'],float(request['options']['angle_tolerance_degree']))
    output=Path(request['output_dir'])/'symmetry.json';dump(output,data)
    rows=[{'symprec':r['symprec_angstrom'],'symbol':r['space_group_symbol'],'number':r['space_group_number']} for r in data['results']]
    return {'summary':'普通空间群与等价原子分析完成','tables':[{'title':'空间群','columns':[{'key':'symprec','label':'symprec','unit':'Å'},{'key':'symbol','label':'空间群','unit':''},{'key':'number','label':'编号','unit':''}],'rows':rows}], 'warnings':[data['scope']],'artifacts':[artifact(output,'完整对称性 JSON')]}

if __name__=='__main__':raise SystemExit(main(check,analyze))

#!/usr/bin/env python3
from pathlib import Path
from protocol_cli import main,file,dump,artifact

def values(request):
    from calc_lcalcpol import _single_result,compare_polarizations
    tolerance=request['options']['lattice_tolerance_angstrom']
    count=len(request['inputs']['structure'])
    if count!=len(request['inputs']['outcar']) or count not in (1,2):raise ValueError('需要一组或两组对应的结构与 OUTCAR')
    if count==2:return compare_polarizations(str(file(request,'outcar',0)),str(file(request,'structure',0)),str(file(request,'outcar',1)),str(file(request,'structure',1)),tolerance)
    return _single_result(str(file(request,'outcar')),str(file(request,'structure')),tolerance)

def check(request):
    values(request);return '完整 LCALCPOL 记录可读；端点不证明同分支路径'

def analyze(request):
    data=values(request);output=Path(request['output_dir'])/'polarization.json';dump(output,data)
    warnings=['形式极化按极化量子定义；最近整数约化不是物理最小化。','端点不证明绝缘同分支路径，不能据此声明可切换或自发极化。']
    if len(request['sources'])==2:
        vector=data['comparison']['delta_p_raw_c_m2'];candidate=data['comparison']['delta_p_branch_reduced_candidate_c_m2']
        rows=[{'axis':a,'raw':x,'candidate':y} for a,x,y in zip('xyz',vector,candidate)]
        columns=[{'key':'axis','label':'方向','unit':''},{'key':'raw','label':'端点 2 − 端点 1','unit':'C/m²'},{'key':'candidate','label':'共同量子基中的约化候选','unit':'C/m²'}]
        summary='双端点形式极化差已计算；同分支物理证据不足'
    else:
        rows=[{'axis':a,'raw':x} for a,x in zip('xyz',data['raw']['polarization_c_m2'])]
        columns=[{'key':'axis','label':'方向','unit':''},{'key':'raw','label':'形式极化','unit':'C/m²'}];summary='形式极化已读取；物理分支未建立'
    return {'summary':summary,'warnings':warnings,'scientific_validation':'INSUFFICIENT','tables':[{'title':'形式极化','columns':columns,'rows':rows}],'artifacts':[artifact(output,'完整数据、量子与假设')]}

if __name__=='__main__':raise SystemExit(main(check,analyze))

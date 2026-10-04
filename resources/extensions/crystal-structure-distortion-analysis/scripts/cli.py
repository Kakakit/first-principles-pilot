#!/usr/bin/env python3
import json,subprocess,sys
from pathlib import Path
from protocol_cli import main,file,dump,artifact

def config(request):
    option=request['options']
    if option['center_element']==option['ligand_element']:raise ValueError('中心元素与配体须不同')
    return {'centers':{option['center_element']:{'coordination':option['coordination'],'ligand':option['ligand_element']}},'search_radius_angstrom':option['search_radius_angstrom'],'shell_gap_warning_angstrom':0.15}

def original(request,check_only=False):
    out=Path(request['output_dir']);setting=out/'config.json';dump(setting,config(request))
    script=Path(__file__).resolve().parents[1]/'src/crystal_distortion.py'
    argv=[sys.executable,str(script),str(file(request,'structure')),'--config',str(setting),'--output-prefix',str(out/'distortion')]
    if check_only:argv+=['--check-applicability']
    result=subprocess.run(argv,capture_output=True,text=True,timeout=240)
    if result.returncode:raise ValueError(result.stderr[-2000:] or result.stdout[-2000:])
    return result

def check(request):
    data=json.loads(original(request,True).stdout);return f"{data['sites']} 个位点满足所选配位条件"

def analyze(request):
    original(request)
    out=Path(request['output_dir']);data=json.loads((out/'distortion.json').read_text())
    summary=data.get('summary',{})
    rows=[]
    for polyhedron,group in summary.items():
        for metric,value in group['metrics'].items():
            unit=next((unit for suffix,unit in [('angstrom3','Å³'),('angstrom','Å'),('degree2','°²'),('degree','°')] if metric.endswith(suffix)),'1')
            rows.append({'polyhedron':polyhedron,'metric':metric,'mean':value['mean'],'std':value['std_population'],'unit':unit,'sites':group['site_count']})
    columns=[{'key':'polyhedron','label':'多面体','unit':''},{'key':'metric','label':'指标','unit':''},{'key':'mean','label':'均值','unit':''},{'key':'std','label':'标准差','unit':''},{'key':'unit','label':'单位','unit':''},{'key':'sites','label':'位点数','unit':''}]
    return {'summary':'配位多面体畸变分析完成','warnings':data.get('warnings',[]),'tables':[{'title':'配位畸变指标（定义与限制见完整报告）','columns':columns,'rows':rows}], 'artifacts':[artifact(out/'distortion.json','完整 JSON'),artifact(out/'distortion.csv','逐位点 CSV','text/csv'),artifact(out/'distortion.md','定义、单位与限制','text/markdown')]}

if __name__=='__main__':raise SystemExit(main(check,analyze))

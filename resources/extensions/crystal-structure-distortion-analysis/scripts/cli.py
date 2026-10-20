#!/usr/bin/env python3
import csv,json,re,subprocess,sys
from pathlib import Path
from protocol_cli import main,file,dump,artifact
from metrics import METRICS

def config(request):
    option=request['options'];centers={};text=option.get('polyhedra','').strip()
    if text:
        for token in re.split(r'[,，;；\s]+',text):
            if not token:continue
            match=re.fullmatch(r'([A-Z][a-z]?)([A-Z][a-z]?)([46])',token)
            if not match:raise ValueError('多面体请写成 CrO6, FeO4；只支持单一配体的四/六配位')
            center,ligand,number=match.groups();spec={'ligand':ligand,'coordination':int(number)}
            if center in centers and centers[center]!=spec:raise ValueError('同一中心元素不能指定冲突配位：'+center)
            centers[center]=spec
    else:
        try:centers={option['center_element']:{'coordination':option['coordination'],'ligand':option['ligand_element']}}
        except KeyError:raise ValueError('请填写多面体类型，例如 CrO6, FeO4')
    if not centers:raise ValueError('请填写至少一种多面体')
    if any(c==v['ligand'] for c,v in centers.items()):raise ValueError('中心元素与配体须不同')
    if text and option.get('center_element'):
        old={'coordination':option.get('coordination'),'ligand':option.get('ligand_element')}
        if centers.get(option['center_element'])!=old:raise ValueError('新旧配位参数冲突，请只填写一种方式')
    return {'centers':centers,'search_radius_angstrom':option.get('search_radius_angstrom',3),'shell_gap_warning_angstrom':0.15}

def original(request,check_only=False):
    out=Path(request['output_dir']);setting=out/'config.json';dump(setting,config(request))
    script=Path(__file__).resolve().parents[1]/'src/crystal_distortion.py'
    argv=[sys.executable,str(script),str(file(request,'structure')),'--config',str(setting),'--output-prefix',str(out/'distortion')]
    if check_only:argv+=['--check-applicability']
    result=subprocess.run(argv,capture_output=True,text=True,timeout=240)
    if result.returncode:raise ValueError(result.stderr[-2000:] or result.stdout[-2000:])
    return result

def check(request):
    data=json.loads(original(request,True).stdout);return f"{data['sites']} 个中心满足所选配位条件；将计算内部角与共享配体桥角"

def detail_csv(path,columns,rows):
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream);writer.writerow([label for _,label in columns])
        for row in rows:writer.writerow([json.dumps(row.get(key),ensure_ascii=False) if isinstance(row.get(key),(list,dict)) else row.get(key,'') for key,_ in columns])

def analyze(request):
    original(request)
    out=Path(request['output_dir']);data=json.loads((out/'distortion.json').read_text())
    summary=data.get('summary',{})
    rows=[]
    for polyhedron,group in summary.items():
        for metric,value in group['metrics'].items():
            definition=METRICS[metric]
            rows.append({'polyhedron':polyhedron,'metric':definition['label'],'mean':value['mean'],'std':value['std_population'],'unit':definition['unit'],'sites':group['site_count']})
    columns=[{'key':'polyhedron','label':'多面体','unit':''},{'key':'metric','label':'指标','unit':''},{'key':'mean','label':'均值','unit':''},{'key':'std','label':'标准差','unit':''},{'key':'unit','label':'单位','unit':''},{'key':'sites','label':'位点数','unit':''}]
    bonds=[];angles=[]
    for site in data['site_results']:
        for i,(atom,image,length) in enumerate(zip(site['ligand_site_numbers'],site['ligand_images'],site['bond_lengths_angstrom'])):
            bonds.append(dict(center=site['site_number'],element=site['element'],ligand=site['ligand'],atom=atom,image=image,length=length,deviation=(length/site['mean_bond_length_angstrom']-1)*100))
        for angle in site['angles']:
            i,j=angle['ligand_local_indices'];angles.append(dict(center=site['site_number'],element=site['element'],ligand=site['ligand'],a=site['ligand_site_numbers'][i],image_a=site['ligand_images'][i],b=site['ligand_site_numbers'][j],image_b=site['ligand_images'][j],kind={'cis':'顺式','trans':'对向','tetrahedral':'四面体'}[angle['kind']],angle=angle['angle_degree'],reference=angle['reference_degree']))
    detail_csv(out/'bonds.csv',list(zip(['center','element','ligand','atom','image','length','deviation'],['中心编号','中心元素','配体元素','配体编号','配体晶格镜像','键长 / Å','相对平均键长偏差 / %'])),bonds)
    detail_csv(out/'angles.csv',list(zip(['center','element','ligand','a','image_a','b','image_b','kind','angle','reference'],['中心编号','中心元素','配体元素','配体一编号','配体一镜像','配体二编号','配体二镜像','角类型','键角 / °','参考角 / °'])),angles)
    detail_csv(out/'bridges.csv',list(zip(['center_i','element_i','center_j','element_j','center_j_image','ligand','ligand_site_number','ligand_image','length_i_angstrom','length_j_angstrom','center_distance_angstrom','angle_degree'],['起点编号','起点元素','终点编号','终点元素','终点晶格镜像','配体元素','配体编号','配体镜像','起点键长 / Å','终点键长 / Å','中心间距 / Å','桥键角 / °'])),data['bridges'])
    return {'summary':f"已分析 {len(data['site_results'])} 个多面体、{len(data['bridges'])} 条共享配体桥角；仅表示所选几何配位模型",'warnings':data.get('warnings',[]),'tables':[{'title':'畸变指标汇总（可逐位点查看）','columns':columns,'rows':rows}], 'artifacts':[artifact(out/'distortion.json','完整几何与指标 JSON'),artifact(out/'distortion.csv','原格式逐位点 CSV','text/csv'),artifact(out/'distortion.md','定义、单位与限制','text/markdown'),artifact(out/'bonds.csv','全部键长明细','text/csv'),artifact(out/'angles.csv','全部内部键角','text/csv'),artifact(out/'bridges.csv','全部桥键角','text/csv')]}

if __name__=='__main__':raise SystemExit(main(check,analyze))

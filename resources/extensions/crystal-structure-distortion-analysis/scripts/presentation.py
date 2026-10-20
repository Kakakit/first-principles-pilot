#!/usr/bin/env python3
"""Read frozen geometry only; no scientific run, SSH or workbench imports."""
import argparse,json,shutil
from pathlib import Path
from metrics import METRICS

def render(request):
    source=Path(request['source_dir']);out=Path(request['output_dir']);out.mkdir(parents=True,exist_ok=True)
    data=json.loads((source/'distortion.json').read_text())
    if data.get('analysis')!='local-polyhedral-distortion' or data.get('detail_schema')!='fpilot.polyhedra-geometry.v1':
        raise ValueError('旧结果缺少完整几何；请从同一冻结结构创建新版本地分析，不会读取最新草稿或连接服务器')
    if not data.get('site_results'):raise ValueError('没有可展示的多面体')
    root=Path(__file__).resolve().parents[1]
    # Omit private provenance paths from the interactive payload. Original JSON stays intact.
    view={k:data[k] for k in ('detail_schema','structure','site_results','bridges','summary','warnings')}
    view['metrics']=list(METRICS.values())
    view['formulas']=json.loads((root/'viewer/formulas.json').read_text())
    parts=[];buffer=[];size=2
    for site in view.pop('site_results'):
        raw=json.dumps(site,ensure_ascii=False,allow_nan=False,separators=(',',':'))
        if len(raw.encode())>5*1024**2:raise ValueError('单个位点几何超过展示上限')
        if buffer and size+len(raw.encode())>5*1024**2:
            name='sites-'+str(len(parts))+'.json';(out/name).write_text('['+','.join(buffer)+']');parts.append(name);buffer=[];size=2
        buffer.append(raw);size+=len(raw.encode())+1
    if buffer:
        name='sites-'+str(len(parts))+'.json';(out/name).write_text('['+','.join(buffer)+']');parts.append(name)
    bridges=view.pop('bridges');bridge_parts=[]
    for start in range(0,len(bridges),2000):
        name='bridges-'+str(len(bridge_parts))+'.json';raw=json.dumps(bridges[start:start+2000],ensure_ascii=False,allow_nan=False)
        if len(raw.encode())>5*1024**2:raise ValueError('桥键分片超过展示上限')
        (out/name).write_text(raw);bridge_parts.append(name)
    view.update(site_parts=parts,bridge_parts=bridge_parts,bridge_count=len(bridges))
    (out/'data.json').write_text(json.dumps(view,ensure_ascii=False,allow_nan=False))
    shutil.copyfile(root/'viewer/viewer.html',out/'index.html')
    assets=[{'path':p.name,'media_type':'text/html' if p.suffix=='.html' else 'application/json'} for p in sorted(out.iterdir()) if p.is_file()]
    if any((out/a['path']).stat().st_size>10*1024**2 for a in assets):raise ValueError('展示文件超过 10 MiB')
    return {'schema':'fpilot.extension-presentation.v1','entry_html':'index.html','data_schema':'fpilot.polyhedra-geometry.v1','assets':assets}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['render']);p.add_argument('--request',type=Path,required=True);a=p.parse_args();print(json.dumps(render(json.loads(a.request.read_text()))))

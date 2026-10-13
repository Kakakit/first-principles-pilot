#!/usr/bin/env python3
"""Rebuild display/fits exclusively from immutable pressure-eos.json and saved state."""
import argparse,hashlib,json
from pathlib import Path
from eos_core import fit_groups,pressure

def render(request):
    raw=(Path(request['source_dir'])/'pressure-eos.json').read_bytes();data=json.loads(raw);identity=hashlib.sha256(raw).hexdigest()
    state=request.get('view_state',{}).get('value',{})
    if state.get('identity')!=identity:state={}
    groups=list(dict.fromkeys(p['group'] for p in data['points']))
    for p in data['points']:
        p['group_signature']=p['group'];p['group']=p.get('formula','结构')+' · 设置组 '+str(groups.index(p['group'])+1)
    selection={**data['options'],**{k:v for k,v in state.items() if k in ('energy','model','groups','excluded')}}
    fits=fit_groups(data['points'],selection)
    for fit in fits:
        if fit['status']=='FITTED':
            for p in data['points']:
                group=(state.get('groups') or {}).get(p['id']) or p['group']
                if group==fit['group']:p['values']['eos_pressure_gpa']=float(pressure(p['values']['volume_a3']/p['n_atoms'],fit['eos_params'],fit['model']))
    data.update(identity=identity,fits=fits,fit_selection=selection)
    out=Path(request['output_dir']);out.mkdir(parents=True,exist_ok=True)
    (out/'index.html').write_text((Path(__file__).resolve().parents[1]/'viewer/viewer.html').read_text())
    (out/'data.json').write_text(json.dumps(data,ensure_ascii=False,allow_nan=False))
    return {'schema':'fpilot.extension-presentation.v1','entry_html':'index.html','data_schema':'fpilot.pressure-eos.v1',
            'assets':[{'path':'index.html','media_type':'text/html'},{'path':'data.json','media_type':'application/json'}]}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['render']);p.add_argument('--request',type=Path,required=True);a=p.parse_args();print(json.dumps(render(json.loads(a.request.read_text()))))

#!/usr/bin/env python3
"""Frozen-data viewer; never re-open original research sources."""
import argparse,hashlib,json
from pathlib import Path

def render(request):
    source=Path(request['source_dir']);path=source/'occupation.json' if (source/'occupation.json').is_file() else source/'pdos.json' if (source/'pdos.json').is_file() else source/'onsite.json'
    raw=path.read_bytes();bundle=json.loads(raw);mode=bundle.get('mode','hamiltonian')
    out=Path(request['output_dir']);out.mkdir(parents=True,exist_ok=True);viewer=Path(__file__).resolve().parents[1]/'viewer';assets=[]
    def write(name,obj):
        payload=json.dumps(obj,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
        if len(payload)>9*1024*1024:raise ValueError('展示数据分片超过 9 MiB')
        (out/name).write_bytes(payload);assets.append({'path':name,'media_type':'application/json'});return name
    models=[];page={};page_bytes=0;page_number=0
    def add_part(key,obj):
        nonlocal page,page_bytes,page_number
        size=len(json.dumps(obj,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode())
        if page and page_bytes+size>8*1024*1024:
            write(f'pdos-page-{page_number}.json',{'parts':page});page={};page_bytes=0;page_number+=1
        if size>8*1024*1024:raise ValueError('单个 PDOS 数据片过大，请减少 NEDOS')
        page[key]=obj;page_bytes+=size+len(key)+8
        return {'path':f'pdos-page-{page_number}.json','key':key}
    for mi,m in enumerate(bundle['models']):
        if mode=='pdos':
            atoms=[]
            for a in m['atoms']:
                shells=[]
                for s in a['shells']:
                    chunks=[]
                    for start in range(0,len(m['energy_ev']),4000):
                        chunks.append(add_part(f'p{mi}-a{a["atom"]}-l{s["l"]}-{start}',{'energy_ev':m['energy_ev'][start:start+4000],'density':[[t[start:start+4000] for t in spin['density']] for spin in s['spins']]}))
                    shells.append({'l':s['l'],'labels':s['labels'],'spins':[{'label':sp['label']} for sp in s['spins']],'chunks':chunks})
                atoms.append({**{k:v for k,v in a.items() if k!='shells'},'shells':shells})
            models.append({**{k:v for k,v in m.items() if k not in ('atoms','energy_ev')},'atoms':atoms,'range_ev':[m['energy_ev'][0],m['energy_ev'][-1]]})
        else:
            models.append({'source':m['source'],'seed':m.get('seed'),'file':write(f'h{mi}.json',m)})
    if page:write(f'pdos-page-{page_number}.json',{'parts':page})
    index={'schema':'fpilot.orbital-levels.v1','identity':hashlib.sha256(raw).hexdigest(),'mode':mode,'models':models,'wf_spin_alignment':bundle.get('wf_spin_alignment'),'comparison':bundle.get('comparison','分模型展示')}
    identities=[m['wf_identity'] for m in bundle['models'] if m.get('wf_identity')]
    if identities:index['wf_identity_file']=write('wf-identity.json',{'schema':'fpilot.wf-identity.v1','models':identities,'spin_alignment':bundle.get('wf_spin_alignment',{'status':'NOT_PAIRED'})})
    write('data.json',index)
    if mode=='occupation':
        html=(viewer/'occupation-shell.html').read_text().replace('/* OCCUPATION MODEL */',(viewer/'occupation-model.mjs').read_text().replace('export ','' )).replace('/* OCCUPATION MAIN */',(viewer/'occupation-main.js').read_text())
    else:html=(viewer/'shell.html').read_text().replace('/* MATH */',(viewer/'math.mjs').read_text()).replace('/* MAIN */',(viewer/'main.js').read_text())
    (out/'index.html').write_text(html);assets.insert(0,{'path':'index.html','media_type':'text/html'})
    if len(assets)>1000:raise ValueError('展示分片超过 1000，请分批选择来源')
    return {'schema':'fpilot.extension-presentation.v1','entry_html':'index.html','data_schema':index['schema'],'assets':assets}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['render']);p.add_argument('--request',type=Path,required=True);a=p.parse_args();print(json.dumps(render(json.loads(a.request.read_text()))))

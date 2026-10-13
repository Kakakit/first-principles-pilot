#!/usr/bin/env python3
"""Prepare a view from frozen output JSON only; never read live OUTCAR or run VASP."""
import argparse,hashlib,json
from pathlib import Path
import numpy as np

def render(request):
    source=Path(request['source_dir']);output=Path(request['output_dir']);output.mkdir(parents=True,exist_ok=True)
    raw=(source/'polarization.json').read_bytes();data=json.loads(raw)
    result=json.loads((source/'result.json').read_text()) if (source/'result.json').exists() else {}
    ends=[data['noSOC'],data['SOC']] if data.get('mode')=='compare' else [data]
    labels=data.get('source_labels') or [{'name':s.get('name',s.get('id','端点'))} for s in result.get('sources',[])]
    for i,end in enumerate(ends):end['label']=labels[i] if i<len(labels) else {'name':'端点 '+str(i+1)}
    basis=np.asarray(ends[0]['polarization_quantum']['vectors_c_m2'])
    raw_p=np.asarray(data['comparison']['delta_p_raw_c_m2'] if len(ends)==2 else ends[0]['raw']['polarization_c_m2'])
    coeff=np.linalg.solve(basis.T,raw_p);automatic=np.round(coeff).astype(int)
    value={'schema':'fpilot.polarization-view.v1','identity':hashlib.sha256(raw).hexdigest(),'original':data,'endpoints':ends,
           'basis':basis.tolist(),'raw_p':raw_p.tolist(),'coefficients':coeff.tolist(),'automatic':automatic.tolist(),
           'factor':ends[0].get('conversion_factor',16.021766),'volume':ends[0]['volume_angstrom3']}
    if len(ends)==2:
        value['raw_dipole_difference']={key:(np.asarray(ends[1][key])-np.asarray(ends[0][key])).tolist() for key in ['p_ion_e_ang','p_elc_e_ang','total_dipole_e_ang']}
        diagnostic=np.asarray(data['comparison']['endpoint_reduced_difference_c_m2'])
        value['diagnostic_p']=diagnostic.tolist()
        value['diagnostic_coefficients']=np.linalg.solve(basis.T,diagnostic).tolist()
    viewer=Path(__file__).resolve().parents[1]/'viewer'
    html=(viewer/'shell.html').read_text().replace('/*MODEL*/',(viewer/'model.js').read_text().replace('export ','' )).replace('/*MAIN*/',(viewer/'main.js').read_text())
    (output/'index.html').write_text(html);(output/'data.json').write_text(json.dumps(value,ensure_ascii=False,allow_nan=False))
    return {'schema':'fpilot.extension-presentation.v1','entry_html':'index.html','data_schema':value['schema'],'assets':[{'path':'index.html','media_type':'text/html'},{'path':'data.json','media_type':'application/json'}]}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['render']);p.add_argument('--request',type=Path,required=True);a=p.parse_args()
    print(json.dumps(render(json.loads(a.request.read_text()))))

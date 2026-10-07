#!/usr/bin/env python3
"""Render existing TB2J JSON into an offline view; never invoke TB2J or SSH."""
import argparse
import json
import math
import pathlib
import re
import shutil

MAX_PART=5*1024**2
PAIR_COLUMNS=['id','atom_i','atom_j','Rx','Ry','Rz','distance_A','J_meV','DMI_meV','Jani_meV']
ORBITAL_COLUMNS=['pair_id','orbital_i','orbital_j','J_meV','DMI_meV','Jani_meV']


def encoded(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)


def check_number(value):
    if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value):raise ValueError('Non-finite TB2J number')
    return value


def key(row):
    if len(row['R'])!=3 or any(type(v) is not int for v in row['R']):raise ValueError('Invalid lattice translation')
    return tuple(row['R'])+(row['atom_i'],row['atom_j'])


def orbital_metadata(source,full,bounds):
    atoms={r['atom_i']:r['symbol_i'] for r in full['exchange']}
    atoms.update({r['atom_j']:r['symbol_j'] for r in full['exchange']})
    win=next(iter(sorted((source/'model').glob('*.win'))),None)
    text=win.read_text() if win else ''
    block=re.search(r'begin\s+atoms_(?:cart|frac)\s*\n(.*?)end\s+atoms_(?:cart|frac)',text,re.S|re.I)
    sites={};counts={}
    if block:
        symbols=[m.group(1) for line in block.group(1).splitlines() for m in [re.match(r'\s*([A-Z][a-z]?)\s+[-+.0-9]',line)] if m]
        for index,symbol in enumerate(symbols,1):
            counts[symbol]=counts.get(symbol,0)+1;sites[symbol+str(counts[symbol])]=index
            if index in atoms and atoms[index]!=symbol:raise ValueError('Frozen model atom order differs from result')
    basis_path=source/'TB2J_results/assigned_basis.txt'
    basis=basis_path.read_text() if basis_path.is_file() else ''
    basis_rows=[]
    for line in basis.splitlines():
        match=re.fullmatch(r'\s*([A-Z][a-z]?\d+)\|orb_(\d+)\s+(\d+)\s*',line)
        if match and match.group(1) in sites:
            basis_rows.append({'atom_index':sites[match.group(1)],'orbital_index':int(match.group(2)),'wannier_index':int(match.group(3))})
    projection=re.search(r'begin\s+projections\s*\n(.*?)end\s+projections',text,re.S|re.I)
    projection=projection.group(1).strip() if projection else ''
    simple={};orders={'s':['s'],'p':['pz','px','py'],'d':['dz2','dxz','dyz','dx2-y2','dxy']}
    for line in projection.splitlines():
        match=re.fullmatch(r'\s*([A-Z][a-z]?)\s*:\s*([spd])\s*',line,re.I)
        if match and match.group(1) not in simple:simple[match.group(1)]=orders[match.group(2).lower()]
        elif match:simple[match.group(1)]=None
    candidates=[]
    for ai,symbol in sorted(atoms.items()):
        order=simple.get(symbol)
        matches=[r for r in basis_rows if r['atom_index']==ai]
        if order and bounds.get(ai)==len(order) and len(matches)==len(order):
            for index,label in enumerate(order,1):candidates.append({'atom_index':ai,'orbital_index':index,'label':label,'group':''})
    return {'atoms':[{'index':ai,'symbol':symbol,'orbitals':bounds.get(ai,0)} for ai,symbol in sorted(atoms.items())],
            'basis_text':basis,'basis_rows':basis_rows,'projections':projection,'candidate_mapping':candidates}


def shards(output,prefix,rows,pair=None):
    parts=[];buffer=[];size=2
    def flush():
        if not buffer:return
        name=prefix+'-'+str(len(parts))+'.json'
        raw='['+','.join(buffer)+']';(output/name).write_text(raw)
        parts.append({'path':name,'count':len(buffer),**({'pair':pair} if pair else {})})
    for row in rows:
        raw=encoded(row);length=len(raw.encode())+1
        if length>MAX_PART:raise ValueError('One TB2J row exceeds the view bound')
        if buffer and size+length>MAX_PART:flush();buffer=[];size=2
        buffer.append(raw);size+=length
    flush();return parts


def render(source,output):
    full=json.loads((source/'exchange.json').read_text())
    if full.get('schema')!='fpilot.tb2j-data.v2' or full.get('units')!={'exchange':'meV','distance':'Å'} or full.get('atom_index_base')!=1 or full.get('orbital_index_base')!=1:raise ValueError('Unknown TB2J data schema, unit, or index base')
    output.mkdir(parents=True,exist_ok=True)
    pairs=[];lookup={};symbols={};pair_counts={}
    for index,row in enumerate(full['exchange']):
        bond=key(row)
        if bond in lookup:raise ValueError('Duplicate native TB2J pair key')
        lookup[bond]=index
        for end in ('i','j'):
            ai=row['atom_'+end];symbol=row['symbol_'+end]
            if type(ai) is not int or ai<1 or not re.fullmatch(r'[A-Z][a-z]?',symbol):raise ValueError('Invalid atom index or element')
            if ai in symbols and symbols[ai]!=symbol:raise ValueError('Conflicting element for one atom')
            symbols[ai]=symbol
        distance=check_number(row['distance_A'])
        if distance<0:raise ValueError('Negative pair distance')
        pairs.append([index,row['atom_i'],row['atom_j'],*row['R'],distance,check_number(row['J_meV']),row.get('DMI_meV'),row.get('Jani_meV')])
        pair='–'.join(sorted((row['symbol_i'],row['symbol_j'])));pair_counts[pair]=pair_counts.get(pair,0)+1
    orbital={};bounds={};count=0
    for row in full.get('orbital',[]):
        bond=key(row)
        if bond not in lookup:raise ValueError('Orbital contribution has no native pair')
        for end in ('i','j'):
            ai=row['atom_'+end];oi=row['orbital_'+end]
            if type(oi) is not int or oi<1:raise ValueError('Invalid orbital index')
            bounds[ai]=max(bounds.get(ai,0),oi)
        pair='–'.join(sorted((symbols[row['atom_i']],symbols[row['atom_j']])))
        orbital.setdefault(pair,[]).append([lookup[bond],row['orbital_i'],row['orbital_j'],check_number(row['J_meV']),row.get('DMI_meV'),row.get('Jani_meV')]);count+=1
    exchange_parts=shards(output,'exchange',pairs)
    orbital_parts=[]
    for index,(pair,rows) in enumerate(sorted(orbital.items())):orbital_parts+=shards(output,'orbital-'+str(index),rows,pair)
    meta=orbital_metadata(source,full,bounds)
    result=json.loads((source/'result.json').read_text()) if (source/'result.json').is_file() else {}
    index={'schema':'fpilot.tb2j-view.v1','units':full['units'],'mode':full['mode'],
           'exchange_count':len(pairs),'orbital_count':count,'pair_columns':PAIR_COLUMNS,'orbital_columns':ORBITAL_COLUMNS,
           'exchange_parts':exchange_parts,'orbital_parts':orbital_parts,'element_pairs':pair_counts,
           'frozen_mapping':full.get('orbital_mapping') or [],'energy_reference':full.get('energy_reference'),
           'warnings':result.get('warnings',[]),'validation':result.get('validation',{}),**meta}
    (output/'index.json').write_text(encoded(index))
    html=pathlib.Path(__file__).resolve().parents[1]/'viewer/viewer.html'
    shutil.copyfile(html,output/'viewer.html')
    assets=[{'path':'viewer.html','media_type':'text/html'},{'path':'index.json','media_type':'application/json'}]
    assets += [{'path':part['path'],'media_type':'application/json'} for part in exchange_parts+orbital_parts]
    return {'schema':'fpilot.extension-presentation.v1','entry_html':'viewer.html','data_schema':index['schema'],'assets':assets}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['render']);parser.add_argument('--request',type=pathlib.Path,required=True)
    args=parser.parse_args();request=json.loads(args.request.read_text())
    if request.get('schema')!='fpilot.extension-presentation-request.v1':raise ValueError('Invalid presentation request')
    source=pathlib.Path(request['source_dir']).resolve();output=pathlib.Path(request['output_dir']).resolve()
    if source==output or source in output.parents:raise ValueError('Presentation output must be separate from original results')
    print(encoded(render(source,output)))


if __name__=='__main__':main()

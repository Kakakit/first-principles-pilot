#!/usr/bin/env python3
"""Render complete existing TB2J CSV/JSON into an offline view; never invoke TB2J or SSH."""
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


def read_data(source):
    """CSV is the complete native export, not the truncated result tables."""
    command=json.loads((source/'command.json').read_text()) if (source/'command.json').is_file() else {}
    if not (source/'exchange.csv').is_file():
        full=json.loads((source/'exchange.json').read_text())
        return full,command
    import csv
    def rows(path,orbital=False):
        with path.open(newline='',encoding='utf-8-sig') as stream:
            reader=csv.DictReader(stream);required={'atom_i','atom_j','R','distance_A','J_meV'}|({'orbital_i','orbital_j'} if orbital else {'symbol_i','symbol_j'})
            if not required<=set(reader.fieldnames or []):raise ValueError('完整 CSV 缺少所需列')
            result=[]
            for row in reader:
                item={k:row[k] for k in ('symbol_i','symbol_j') if k in row}
                for k in ('atom_i','atom_j')+(('orbital_i','orbital_j') if orbital else ()):item[k]=int(row[k])
                item.update(R=json.loads(row['R']),distance_A=float(row['distance_A']),J_meV=float(row['J_meV']))
                for k in ('DMI_meV','Jani_meV'):
                    if row.get(k):item[k]=json.loads(row[k])
                result.append(item)
            return result
    options=command.get('options') or {}
    mode=options.get('mode','auto')
    if mode=='auto':mode='spinor' if '--spinor' in command.get('argv',[]) else 'collinear'
    orbital_path=source/'orbitals.csv'
    if options.get('orb_decomposition') and not orbital_path.is_file():raise ValueError('本机缺少完整轨道 CSV；不会连接服务器或使用截断表格')
    full={'schema':'fpilot.tb2j-data.v2','units':{'exchange':'meV','distance':'Å'},'atom_index_base':1,'orbital_index_base':1,
          'mode':mode,'exchange':rows(source/'exchange.csv'),'orbital':rows(orbital_path,True) if orbital_path.is_file() else [],
          'orbital_mapping':options.get('orbital_mapping',[]),'energy_reference':command.get('energy_reference')}
    return full,command


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
    full,command=read_data(source)
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
    orbital={};bounds={};count=0;matrix_cells={}
    for row in full.get('orbital',[]):
        bond=key(row)
        if bond not in lookup:raise ValueError('Orbital contribution has no native pair')
        for end in ('i','j'):
            ai=row['atom_'+end];oi=row['orbital_'+end]
            if type(oi) is not int or oi<1:raise ValueError('Invalid orbital index')
            bounds[ai]=max(bounds.get(ai,0),oi)
        cell=(row['orbital_i'],row['orbital_j']);cells=matrix_cells.setdefault(bond,set())
        if cell in cells:raise ValueError('Duplicate orbital matrix entry')
        cells.add(cell)
        if abs(check_number(row['distance_A'])-pairs[lookup[bond]][6])>1e-7:raise ValueError('Orbital and exchange distances differ')
        pair='–'.join(sorted((symbols[row['atom_i']],symbols[row['atom_j']])))
        orbital.setdefault(pair,[]).append([lookup[bond],row['orbital_i'],row['orbital_j'],check_number(row['J_meV']),row.get('DMI_meV'),row.get('Jani_meV')]);count+=1
    if count:
        for bond in lookup:
            if len(matrix_cells.get(bond,()))!=bounds.get(bond[-2],0)*bounds.get(bond[-1],0):raise ValueError('Incomplete orbital matrix; refusing a truncated view')
    exchange_parts=shards(output,'exchange',pairs)
    orbital_parts=[]
    for index,(pair,rows) in enumerate(sorted(orbital.items())):orbital_parts+=shards(output,'orbital-'+str(index),rows,pair)
    from orbital_labels import metadata
    meta=metadata(source,full,bounds,command.get('options') or {})
    from wf_report import mapping_rows
    identities=command.get('wf_identity_models',[])
    if any(m.get('binding',{}).get('udis_sha256') and m.get('window_validation',{}).get('status')!='MATCHED_HR_DIAGONAL' for m in identities):
        identities=[];meta['label_notes'].append('旧解缠结身份报告未核对外窗口能带行；请加入同来源eig重新分析，不套用旧成分标签。')
    if identities:
        meta['wf_identity_rows']=[{**r,'channel':m['channel']} for m in identities for r in mapping_rows(m,meta['basis_rows'])]
        # Mixed or channel-dependent names retain WF identities, never a silent pure-d label.
        first=mapping_rows(identities[0],meta['basis_rows']);other=mapping_rows(identities[1],meta['basis_rows']) if len(identities)>1 else []
        for index,r in enumerate(first):
            if other and other[index]['label']!=r['label']:r['label']='WF'+str(r['wannier_index'])+' · 自旋成分不同'
            r['components_by_channel']={identities[0]['channel']:r['components']}
            if other:r['components_by_channel'][identities[1]['channel']]=other[index]['components']
        meta['automatic_mapping']=first;meta['candidate_mapping']=first
        meta['label_notes'].append('名称来自最终WF参考重叠，轨道交换仍属于原WF基底；不按成分概率转换J。')
    contexts={}
    for model in identities:
        for site in model.get('local_axes',[]):
            if site.get('coordination') in (4,6):contexts.setdefault(site['atom'],[]).append(site)
    meta['site_context']=[values[0] for values in contexts.values() if len(values)==len(identities) and len({v['coordination'] for v in values})==1]
    if not command.get('model_bindings') and (source/'model').is_dir():
        from wf_report import digest
        bindings=[]
        for prefix,channel in [('up','wannier90.1'),('down','wannier90.2')]:
            hr=source/'model'/(prefix+'_hr.dat');win=source/'model'/(prefix+'.win')
            if hr.is_file() and win.is_file():bindings.append({'prefix':prefix,'channel':channel,'hr_sha256':digest(hr),'win_sha256':digest(win)})
        command['model_bindings']=bindings
    result=json.loads((source/'result.json').read_text()) if (source/'result.json').is_file() else {}
    index={'schema':'fpilot.tb2j-view.v1','units':full['units'],'mode':full['mode'],
           'default_max_distance_A':(command.get('options') or {}).get('rcut_angstrom') or 9,'calculation_max_distance_A':(command.get('options') or {}).get('rcut_angstrom'),
           'exchange_count':len(pairs),'orbital_count':count,'pair_columns':PAIR_COLUMNS,'orbital_columns':ORBITAL_COLUMNS,
           'exchange_parts':exchange_parts,'orbital_parts':orbital_parts,'element_pairs':pair_counts,
           'frozen_mapping':full.get('orbital_mapping') or [],'energy_reference':full.get('energy_reference'),
           'model_bindings':command.get('model_bindings',[]),'wf_spin_alignment':command.get('wf_spin_alignment'),'warnings':result.get('warnings',[]),'validation':result.get('validation',{}),**meta}
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

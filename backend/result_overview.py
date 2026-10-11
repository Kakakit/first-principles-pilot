"""Local result summaries. Opening this view never contacts a cluster."""
import hashlib
import json
import re
import zipfile
from . import execution as ex
from .extension_protocol import atomic_json


def signature(record):
    fields=('id','project_id','bundle_id','state','evidence','final_incar','kind','config','job_id',
            'remote_path_absolute','remote_relative_path','parent_attempt_id','chain_source_snapshot','restart_snapshot','dependency_snapshot','source_snapshot')
    return hashlib.sha256(json.dumps({k:record.get(k) for k in fields},sort_keys=True).encode()).hexdigest()


def cache(record, row):
    atomic_json(ex.ROOT/'result-summaries'/(record['id']+'.json'),
                {'signature':signature(record),'checked_at':ex.now(),'row':row})


def frozen_input(record):
    key=record.get('bundle_id');result={'id':key,'parameters':{},'files':{},'stage':None,'warning':None}
    if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):
        result['warning']='未记录有效冻结输入';return result
    try:
        if record.get('kind')=='wannier90':
            path=ex.ROOT/'wannier-bundles'/(key+'.json')
            if path.stat().st_size>1024*1024:raise ValueError('Wannier 输入过大')
            raw=json.loads(path.read_text())
            tags={}
            for line in raw.get('win','').splitlines():
                line=re.split(r'[!#]',line,1)[0]
                if '=' in line:
                    tag,value=line.split('=',1);tags[tag.strip().upper()]=' '.join(value.split())
            channel='通道 1' if str(raw.get('seed','')).endswith('.1') else '通道 2' if str(raw.get('seed','')).endswith('.2') else '单通道'
            result.update(display_name=' · '.join(filter(None,[raw.get('input_reason') or 'Wannier90 冻结输入',channel,raw.get('created_at','')[:19].replace('T',' ')])),stage='wannier90',seed=raw.get('seed'),parent_attempt_id=raw.get('parent_attempt_id'),parameters=tags,parameter_origin='冻结 .win',files={str(raw.get('seed') or 'wannier90')+'.win':{'sha256':raw.get('win_sha256')}})
            return result
        with zipfile.ZipFile(ex.ROOT/'inputs'/(key+'.zip')) as archive:
            if archive.getinfo('manifest.json').file_size>1024*1024:raise ValueError('输入清单过大')
            manifest=json.loads(archive.read('manifest.json'))
            result.update(display_name=manifest.get('input_reason') or record.get('input_reason') or manifest.get('reason') or '已保存的计算输入',stage=manifest.get('stage'),files=manifest.get('files') or {},species=manifest.get('species') or [],geometry_sha256=manifest.get('geometry_sha256'))
            text=record.get('final_incar')
            if not text:
                if archive.getinfo('INCAR').file_size>256*1024:raise ValueError('INCAR 过大')
                text=archive.read('INCAR').decode()
            tags={}
            for line in text.splitlines():
                line=re.split(r'[!#]',line,1)[0]
                for part in line.split(';'):
                    if '=' in part:
                        tag,value=part.split('=',1);tags[tag.strip().upper()]=' '.join(value.split())
            result['parameters']=tags;result['parameter_origin']='实际准备 INCAR' if record.get('final_incar') else '冻结 INCAR'
            if archive.getinfo('KPOINTS').file_size<=256*1024:result['kpoints']=archive.read('KPOINTS').decode()[:1000]
    except (OSError,ValueError,KeyError,TypeError,AttributeError,zipfile.BadZipFile,UnicodeError) as exc:result['warning']='冻结输入暂不可读：'+str(exc)[:200]
    return result


def sources(record):
    rows=[]
    for field in ('chain_source_snapshot','restart_snapshot','dependency_snapshot','source_snapshot'):
        snapshot=record.get(field)
        if not isinstance(snapshot,dict):continue
        key=next((snapshot[k] for k in ('source_attempt_id','upstream_attempt_id','parent_attempt_id','attempt_id') if snapshot.get(k)),None)
        files=snapshot.get('files') or {}
        rows.append({'type':field,'attempt_id':key,'files':list(files) if isinstance(files,(dict,list)) else [],'reason':snapshot.get('reason'),'path':snapshot.get('source_path_absolute') or snapshot.get('source_path')})
    if record.get('parent_attempt_id') and not any(r['attempt_id']==record['parent_attempt_id'] for r in rows):rows.append({'type':'wannier_parent','attempt_id':record['parent_attempt_id'],'files':[]})
    return rows


def summary(project_id,key):
    record=ex.get(key)
    if record.get('project_id')!=project_id:raise ValueError('结果不属于当前项目')
    cached=None
    try:
        value=json.loads((ex.ROOT/'result-summaries'/(key+'.json')).read_text())
        if value['signature']==signature(record):cached=value
    except (OSError,ValueError,KeyError):pass
    from .analysis_datasets import context
    projects,attempts=context();project=projects.get(project_id) or {}
    source_rows=sources(record)
    for source in source_rows:
        item=attempts.get(source.get('attempt_id')) or {}
        node=next((n for n in project.get('nodes',[]) if n['id']==item.get('node_id')),{})
        source['display_name']=' · '.join(filter(None,[node.get('data',{}).get('label') or item.get('kind') or item.get('stage_slug') or '历史计算来源',item.get('job_id') or item.get('created_at','')[:19].replace('T',' ')]))
    return {'attempt_id':key,'state':record['state'],'evidence':record.get('evidence') or {},'input':frozen_input(record),
            'sources':source_rows,'metrics':cached['row'].get('metrics') if cached else None,
            'metrics_error':cached['row'].get('error') if cached else None,'metrics_checked_at':cached['checked_at'] if cached else None,
            'remote_checked':False,'cache_status':'available' if cached else 'not-read'}

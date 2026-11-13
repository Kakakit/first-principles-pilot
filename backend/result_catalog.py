"""One result directory, adapting existing records without migrating research data."""
import json
from urllib.parse import quote, urlencode
from . import analysis_datasets as datasets, extension_runs, recycle, execution as ex
from .history_index import paths


def listing(project_id=None):
    projects,attempts=datasets.context();rows=[]
    for record in attempts.values():
        owner=record.get('project_id');key=record['id']
        if project_id is not None and owner!=project_id or recycle.inactive('attempts',key,record):continue
        project=projects.get(owner) or {}
        from .calculation_nodes import owner as node_owner,case_info
        node_id=node_owner(record);node=next((n for n in project.get('nodes',[]) if n['id']==node_id),{})
        info=case_info(record)
        label=node.get('data',{}).get('label') or record.get('stage_slug') or record.get('kind') or '计算结果'
        if info:label+=' · Case '+str(info['case_index']+1)
        rows.append({'key':'attempt:'+key,'kind':'attempt','source_id':key,'project_id':owner,
            'project_name':project.get('name') or '未关联项目','title':label,'type':'计算结果','created_at':record.get('created_at',''),
            'state':record.get('state'),'recycle_kind':'attempts','href':'#/projects/'+quote(owner or '',safe='')+'/results?'+urlencode({'node':node_id or '', 'attempt':key,'view':'summary'}),
            'producer_kind':'attempt','computation_state':record.get('state'),'collection_state':None})
    for item in datasets.available(project_id):
        rows.append({**item,'key':item['source_kind']+':'+item['source_id'],'kind':item['source_kind'],
            'title':item.get('display_title') or item['title'],'type':item['analysis_name'],'focus':'history:'+item['source_kind']+':'+item['source_id'],
            'recycle_kind':datasets.SOURCES[item['source_kind']][2],'producer_kind':'analysis' if item['source_kind']=='v2' else None})
    for item in datasets.list_records(project_id):
        rows.append({**item,'key':'snapshot:'+item['id'],'kind':'snapshot','source_id':item['id'],
            'type':'独立副本','focus':item['id'],'state':'COMPLETED','recycle_kind':'analysis-datasets'})
    # Failed/in-flight analyses remain locatable even before their report is collected.
    for record in extension_runs.history(project_id):
        if record['state']=='COMPLETED':continue
        item=datasets.summary('v2',record,projects,attempts)
        rows.append({**item,'key':'v2:'+record['id'],'kind':'v2','type':item['analysis_name'],
            'recycle_kind':'analyses','producer_kind':'analysis','analysis_id':record['id'],
            'computation_state':record.get('computation_state'),'collection_state':record.get('collection_state'),
            'error':record.get('collection_error') or record.get('error')})
    for path in paths(datasets.ROOT,'plot-datasets','record.json',project_id=project_id):
        try:
            record=json.loads(path.read_text());key=record['id'];owner=record.get('project_id')
            if recycle.inactive('saved-plots',key,record):continue
            rows.append({'key':'saved-plot:'+key,'kind':'saved-plot','source_id':key,'project_id':owner,
                'project_name':(projects.get(owner) or {}).get('name') or '未关联项目','title':record.get('title') or '交互绘图',
                'type':'保存图表','created_at':record.get('created_at',''),'state':'COMPLETED','recycle_kind':'saved-plots',
                'files':[{'name':name,'href':'/api/interactive-plots/saved/'+key+'/files/'+name+'?'+urlencode({'project_id':owner})}
                         for name in ('figure.png','figure.svg','selected-curves.csv')]})
        except (ValueError,OSError,KeyError):continue
    return sorted(rows,key=lambda r:r.get('created_at',''),reverse=True)

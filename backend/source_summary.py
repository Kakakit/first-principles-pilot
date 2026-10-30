"""Local draft projections using the submission policy, with no file observations."""
from . import source_contracts
from .calculation_nodes import kind, VASP, standalone


def project(project):
    if not isinstance(project,dict) or not isinstance(project.get('nodes'),list) or len(project['nodes'])>500 or not isinstance(project.get('edges'),list) or len(project['edges'])>2000:
        raise ValueError('流程大小或格式无效')
    result={}
    for node in project['nodes']:
        if not isinstance(node,dict) or not isinstance(node.get('id'),str) or not isinstance(node.get('data'),dict):raise ValueError('节点格式无效')
        key=node['id']
        try:
            if kind(node,project) not in VASP:continue
            target=key;effective=project
            if node['data']['kind']=='scan' and not standalone(node):
                from .scans import source_project
                target=next((e['source'] for e in project['edges'] if e['target']==key),key)
                effective=source_project(project,key,target)
            plan=source_contracts.plan(effective,target)
            result[key]={'files':plan['required_files'],'structure_file':plan['structure_file'],
                         'has_parent':bool(source_contracts.draft(effective,target)[1]),'parent_id':plan['parent_id'],'mode':plan['mode'],'electronic_usage':plan['electronic_usage']}
        except (ValueError,KeyError,TypeError) as exc:
            result[key]={'files':[],'error':str(exc)}
    return {'nodes':result,'remote_read':False}


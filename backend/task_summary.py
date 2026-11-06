"""Small local-only list projections; frozen inputs and receipts stay in details."""
from . import execution, scans, chain_runs
from .input_display import frozen_note

ATTEMPT_FIELDS=('id','node_id','owner_node_id','execution_scope','state','kind','created_at','updated_at',
    'job_id','tracking_error','tracking_checked_at','state_checked_at','path_schema','stage_slug','remote_path_absolute','remote_path','bundle_id','evidence',
    'scan_batch_id','scan_case_id','case_index','batch_reason','parent_attempt_id','parameter_summary','project_id',
    'plot_source_attempt_id','band_attempt_id','node','cores','seed','chain_run_id','command_run_id','direct_runtime')


def attempt(row):
    return {**{k:row[k] for k in ATTEMPT_FIELDS if k in row},
            'input_note':frozen_note(row) if not row.get('scan_batch_id') else None,
            'config':{k:row.get('config',{}).get(k) for k in ('alias','scheduler','name') + (
                ('command_mode',) if 'command_mode' in row.get('config',{}) else ())}}


def attempts(project_id):
    return [attempt(r) for r in execution.history(project_id,limit=None)]


def page(project_id,limit=50,cursor=None,node_id=None):
    rows=execution.history(project_id,limit=None)
    if node_id:rows=[r for r in rows if node_id in (r.get('node_id'),r.get('owner_node_id'),r.get('execution_scope'))]
    result=paginate(rows,limit,cursor)
    result['items']=[attempt(row) for row in result['items']]
    return result


def paginate(rows,limit=50,cursor=None):
    start=0
    if cursor:
        positions=[i for i,r in enumerate(rows) if r['id']==cursor]
        if not positions:raise ValueError('历史游标已失效，请刷新列表')
        start=positions[0]+1
    limit=max(1,min(limit,200));items=rows[start:start+limit]
    return {'items':items,'next_cursor':items[-1]['id'] if items and start+limit<len(rows) else None,'total':len(rows)}


def scan(row):
    return {**{k:row[k] for k in ('id','scan_node_id','stage','state','schema','promotion','waiting_reason','last_worker_error') if k in row},
            'cases':[{k:c[k] for k in ('id','index','state','attempt_id','parameters') if k in c} for c in row['cases']]}


def project(project_id):
    return {'attempts':attempts(project_id),'scans':{'batches':[scan(r) for r in scans.history(project_id)]},
            'chains':{'runs':[{k:r[k] for k in ('id','state') if k in r} | {
                'steps':[{k:s[k] for k in ('node_id','state') if k in s} for s in r['steps']]}
                for r in chain_runs.history(project_id)]}}


def recovery(row):
    fields=('id','state','scope','max_parallel','skipped','requires_unknown_confirmation')
    item_fields=('id','state','error','waiting_reason','dispatch_state','job_id','attempt_id_new',
                 'receipt_note','receipt_state','progress')
    return {**{k:row[k] for k in fields if k in row},
            'items':[{k:item[k] for k in item_fields if k in item} for item in row['items']]}

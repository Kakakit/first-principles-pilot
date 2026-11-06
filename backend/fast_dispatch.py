"""Live guards executed inside the one non-replayed upload/submission request."""
import inspect
import json
import shlex
from contextlib import closing
from pathlib import Path
from . import servers,remote_paths,remote_transport,source_files,execution as ex


def enabled(record):return record.get('config',{}).get('scheduler')!='custom' and record.get('dispatch_protocol')=='fpilot.batch-dispatch.v1'


def stage_receipt_prefix():
    return ("set -e\nfpilot_submitting=0\nfpilot_created=0\n"
            + "trap 'rc=$?; if test \"$rc\" -ne 0 && test \"$fpilot_submitting\" = 0; then printf \"FPILOT_NOT_DISPATCHED=%s\\n\" \"$fpilot_created\"; fi' EXIT\n")


def prefix(record,purpose='vasp'):
    config=record['config']
    # Reuse the resource parser and selector, rather than weakening node rules.
    parser_source=Path(servers.parser.__file__).read_text().split('if __name__ ==',1)[0]
    selector_source=Path(servers.selector.__file__).read_text().split('if __name__ ==',1)[0]
    code='import sys,json\np={};s={}\nexec('+repr(parser_source)+',p)\nexec('+repr(selector_source)+',s)\n'
    contract={k:record[k] for k in ('config','node','cores','reason','profile')}
    code+='contract='+repr(contract)+'\ntry:\n    exec('+repr(CAPACITY)+')\nexcept ValueError as exc:\n    print("FPILOT_RESOURCE_BLOCKED=1",flush=True)\n    print(str(exc),file=sys.stderr)\n    sys.exit(1)\n'
    queue_command='qstat -Qf' if servers.normalize_scheduler(config['scheduler'])=='pbs' else "sinfo -h -o 'FPILOT_PARTITION=%P|%a|%l'"
    body=servers.availability_command(config)+"printf 'FPILOT_QUEUE_BEGIN\\n'\n"+queue_command
    command=(stage_receipt_prefix()
        +'capacity=$(\n'+body+'\n)\n'
        +shlex.quote(remote_transport._interpreter(config['alias']))+' -c '+shlex.quote(code)+' "$capacity"\n')
    if config.get('resource_policy')=='scheduler_queue':
        command=stage_receipt_prefix()
    if purpose=='vasp' and config.get('runtime_source')!='script':
        for name in ('mpi_executable','vasp_executable','environment_script'):
            value=config.get(name)
            if value:command+='test '+('-r ' if name=='environment_script' else '-x ')+shlex.quote(value)+'\n'
    reference=record.get('live_energy_reference')
    if reference:
        command += '\n'.join(source_files.guard(remote_paths.shell_path(reference['remote_path']),reference['source_receipt']))+'\n'
    if purpose=='wannier90':
        bundle=json.loads((ex.ROOT/'wannier-bundles'/(record['bundle_id']+'.json')).read_text())
        path=bundle.get('band_path')
        if path:
            for name,field in (('KPOINTS','kpoints_sha256'),('POSCAR','poscar_sha256')):
                file=remote_paths.shell_path(path['source_path'])+'/'+name
                command += 'test "$(sha256sum '+file+' | awk '+shlex.quote('{print $1}')+')" = '+shlex.quote(path[field])+'\n'
    return command


CAPACITY=r'''
text=sys.argv[1]
capacity,marker,queues=text.partition('FPILOT_QUEUE_BEGIN\n')
if not marker:raise ValueError('live queue report missing')
observed=p['parse_preflight'](capacity)
gpu=contract['profile'].get('resource_request') if contract['profile'].get('resource_mode')=='gpu' else None
try:
    selection=(s['gpu_resources'](observed,contract['config']['alias'],contract['node'],gpu) if gpu else
        s['select_resources'](observed,contract['config']['alias'],contract['node'],
        user_override=contract['cores'],user_override_evidence=contract['reason'] or '用户选择请求核数'))
except s['ResourceSelectionError'] as exc:
    facts,_=s['_node'](s['_host'](observed,contract['config']['alias']),contract['node'])
    waiting=(facts.get('STATUS') in ('BUSY','FULLY_IDLE','PARTIAL','SUSPECT_LOAD') and
             str(exc).startswith(s['CAPACITY_BLOCKERS']))
    print('FPILOT_CAPACITY_WAIT=1' if waiting else 'FPILOT_RESOURCE_BLOCKED=1',flush=True)
    print(str(exc),file=sys.stderr)
    sys.exit(75)
expected=contract['profile'].get('ppn',contract['profile'].get('ntasks'))
if selection['requested_cores']!=expected:raise ValueError('live capacity changed; prepare again')
if gpu and (selection['requested_gpus']!=contract['profile']['gpus'] or selection['gpu_resource_registered']!=contract['profile']['gpu_resource_registered']):raise ValueError('GPU allocation changed; prepare again')
config=contract['config']
if config['scheduler'] in ('pbs','PBS','PBS/Torque'):
    selected=None
    for line in queues.splitlines():
        if line.startswith('Queue:'):
            name=line.split(':',1)[1].strip()
            if name==config['queue']:selected={}
            else:
                if selected is not None:break
        elif selected is not None and '=' in line:
            key,value=line.strip().split('=',1);selected[key.strip()]=value.strip()
    if selected is None or selected.get('enabled')!='True' or selected.get('started')!='True':raise ValueError('queue unavailable')
    def seconds(value):
        a,b,c=map(int,value.split(':'));return a*3600+b*60+c
    if selected.get('resources_max.walltime') and seconds(config['walltime'])>seconds(selected['resources_max.walltime']):raise ValueError('walltime exceeds queue limit')
else:
    rows=[line.split('=',1)[1].split('|') for line in queues.splitlines() if line.startswith('FPILOT_PARTITION=')]
    row=next((row for row in rows if len(row)==3 and row[0].rstrip('*')==config['queue'] and row[1]=='up'),None)
    if row is None:raise ValueError('partition unavailable')
    facts=next(n for h in observed['hosts'] for n in h.get('nodes',[]) if n.get('NODE')==contract['node'])
    if facts.get('PARTITIONS') and config['queue'] not in facts['PARTITIONS'].split(','):raise ValueError('node not in selected partition')
    def duration(value):
        days,clock=(value.split('-',1) if '-' in value else ('0',value))
        parts=list(map(int,clock.split(':')))
        if len(parts)==2:parts.insert(0,0)
        return int(days)*86400+parts[0]*3600+parts[1]*60+parts[2]
    if row[2].lower() not in ('infinite','unlimited','n/a') and duration(config['walltime'])>duration(row[2]):raise ValueError('walltime exceeds partition limit')
print('FPILOT_LIVE_CAPACITY='+json.dumps(selection,separators=(',',':')))
'''


def source_guards(path,files):
    return '\n'.join(line for name,info in files.items() for line in source_files.guard(remote_paths.shell_path(path)+'/'+shlex.quote(name),info))+'\n'


def clear_previous_dispatch(record):
    # These facts belong to one send, never to a later retry of its preview.
    for field in ('not_dispatched','note','waiting_kind','dispatch_receipt'):
        record.pop(field,None)


def tracking_note(record):
    if record['state']=='UNKNOWN':
        import re
        record.pop('not_dispatched',None)
        record.pop('waiting_kind',None)
        missing='未找到原 attempt 目录；' if 'FPILOT_ATTEMPT_MISSING=1' in (record.get('status_receipt') or {}).get('stdout','') else ''
        record['note']='提交回执待核查（UNKNOWN）；'+missing+'只核对原作业，确认前不会自动重投'
        receipt=record.get('dispatch_receipt') or {}
        phases=re.findall(r'^FPILOT_SUBMIT_PHASE=([A-Z]+)$',receipt.get('stdout',''),re.M)
        labels={'WORKSPACE':'远端工作区目录创建或解析未返回','PROJECT':'远端项目目录核对未返回',
                'DIRECTORY':'独立 attempt 目录创建未返回','INPUTS':'输入传输或校验未返回','SCHEDULER':'未取得调度器确认回执'}
        if phases and receipt.get('exit_code') is None:
            record['note']+='；'+labels.get(phases[-1],phases[-1])
    else:
        record.pop('note',None)


def known_not_submitted(record,receipt):
    import re
    from .execution_modes import is_direct
    match=re.search(r'^FPILOT_NOT_DISPATCHED=([01])$',receipt.get('stdout',''),re.M)
    not_sent=receipt.get('transport_state')=='NOT_SENT' and receipt.get('exit_code') is None
    if not not_sent and (not match or receipt.get('exit_code') in (None,0)):return False
    project_conflict = bool(re.search(r'^FPILOT_PROJECT_CONFLICT=1$',receipt.get('stdout',''),re.M))
    resource_blocked='FPILOT_RESOURCE_BLOCKED=1' in receipt.get('stdout','').splitlines()
    retryable=not_sent or (match[1]=='0' and not project_conflict and not resource_blocked)
    action = '没有启动进程' if is_direct(record.get('config', {})) else '没有调用调度器'
    record.update(state='PREPARED' if retryable else 'FAILED',job_id=None,
                  not_dispatched=True,dispatch_receipt=receipt,updated_at=ex.now(),
                  note='派发前检查未通过，'+action+'；'+receipt.get('stderr','')[-400:])
    if not_sent:
        note = ('本次启动请求未发送，准备记录可在 SSH 恢复后重新确认' if is_direct(record.get('config', {}))
                else '本次派发请求未发送，等待 SSH 连接恢复后复核资源；没有调用调度器')
        record.update(waiting_kind='CONNECTION',note=note)
    elif 'FPILOT_CAPACITY_WAIT=1' in receipt.get('stdout','').splitlines():
        record.update(waiting_kind='CAPACITY',note='节点仍被占用或负载未恢复，等待资源释放后复核；没有调用调度器')
    with closing(ex.db()) as c:
        c.execute("DELETE FROM branch_hosts WHERE state='RESERVED' AND attempt_id=?",(record['id'],))
        c.commit()
    ex.save(record)
    return True

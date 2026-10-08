"""Live guards executed inside the one non-replayed upload/submission request."""
import inspect
import json
import shlex
from contextlib import closing
from pathlib import Path
from . import servers,remote_paths,remote_transport,source_files,execution as ex


def enabled(record):return record.get('dispatch_protocol')=='fpilot.batch-dispatch.v1'


def prefix(record,purpose='vasp'):
    config=record['config']
    # Reuse the resource parser and selector, rather than weakening node rules.
    parser_source=Path(servers.parser.__file__).read_text().split('if __name__ ==',1)[0]
    selector_source=Path(servers.selector.__file__).read_text().split('if __name__ ==',1)[0]
    code='import sys,json\np={};s={}\nexec('+repr(parser_source)+',p)\nexec('+repr(selector_source)+',s)\n'
    contract={k:record[k] for k in ('config','node','cores','reason','profile')}
    code+='contract='+repr(contract)+'\n'+CAPACITY
    queue_command='qstat -Qf' if servers.normalize_scheduler(config['scheduler'])=='pbs' else "sinfo -h -o 'FPILOT_PARTITION=%P|%a|%l'"
    body=servers.availability_command(config)+"printf 'FPILOT_QUEUE_BEGIN\\n'\n"+queue_command
    command=("set -e\nfpilot_submitting=0\nfpilot_created=0\n"
        +"trap 'rc=$?; if test \"$rc\" -ne 0 && test \"$fpilot_submitting\" = 0; then printf \"FPILOT_NOT_DISPATCHED=%s\\n\" \"$fpilot_created\"; fi' EXIT\n"
        +'capacity=$(\n'+body+'\n)\n'
        +shlex.quote(remote_transport._interpreter(config['alias']))+' -c '+shlex.quote(code)+' "$capacity"\n')
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
selection=(s['gpu_resources'](observed,contract['config']['alias'],contract['node'],gpu) if gpu else
    s['select_resources'](observed,contract['config']['alias'],contract['node'],
    user_override=contract['cores'],user_override_evidence=contract['reason']))
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


def known_not_submitted(record,receipt):
    import re
    match=re.search(r'^FPILOT_NOT_DISPATCHED=([01])$',receipt.get('stdout',''),re.M)
    if not match or receipt.get('exit_code') in (None,0):return False
    project_conflict = bool(re.search(r'^FPILOT_PROJECT_CONFLICT=1$',receipt.get('stdout',''),re.M))
    record.update(state='PREPARED' if match[1]=='0' and not project_conflict else 'FAILED',job_id=None,
                  not_dispatched=True,dispatch_receipt=receipt,updated_at=ex.now(),
                  note='派发前检查未通过，没有调用调度器；'+receipt.get('stderr','')[-400:])
    with closing(ex.db()) as c:
        c.execute("DELETE FROM branch_hosts WHERE state='RESERVED' AND attempt_id=?",(record['id'],))
        c.commit()
    ex.save(record)
    return True

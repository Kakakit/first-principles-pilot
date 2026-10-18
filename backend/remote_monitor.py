"""One host round trip for scheduler states, without reading calculation logs."""
import re
import shlex
from . import execution as ex


def batch(records):
    eligible=[r for r in records if r.get('job_id') and r['state'] in
              ('SUBMITTED','QUEUED','RUNNING','HELD','EXITING','UNKNOWN')
              and r.get('config',{}).get('scheduler') in ('pbs','slurm')]
    if not eligible:return {}
    commands=[]
    for r in eligible:
        commands.append('printf "FPILOT_MONITOR_ID=%s\\n" '+shlex.quote(r['id']))
        if r['config']['scheduler']=='pbs':
            # Missing/terminal jobs use the existing full audit below the batch.
            commands.append("qstat -f {} 2>/dev/null | sed -n '/job_state[[:space:]]*=/p'".format(shlex.quote(r['job_id'])))
        else:
            job=shlex.quote(r['job_id'])
            commands.append("squeue -h -j {} -o 'FPILOT_SLURM_LIVE=%T' || true".format(job))
    receipt=ex.ssh(eligible[0]['config']['alias'],'\n'.join(commands),timeout=30,mutating=False)
    if receipt.get('exit_code') is None:return {r['id']:receipt for r in eligible}
    parts=re.split(r'^FPILOT_MONITOR_ID=([^\n]+)\n',receipt['stdout'],flags=re.M)
    return {parts[i]:{**receipt,'stdout':parts[i+1]} for i in range(1,len(parts)-1,2)}


def active_state(record,receipt):
    text=receipt.get('stdout','')
    if record['config']['scheduler']=='pbs':
        match=re.search(r'job_state\s*=\s*([A-Z])',text)
        return {'Q':'QUEUED','R':'RUNNING','H':'HELD','E':'EXITING'}.get(match.group(1)) if match else None
    match=re.search(r'^FPILOT_SLURM_LIVE=([A-Z_+]+)',text,re.M)
    return {'PENDING':'QUEUED','CONFIGURING':'QUEUED','RUNNING':'RUNNING',
            'COMPLETING':'EXITING','SUSPENDED':'HELD'}.get(match.group(1)) if match else None

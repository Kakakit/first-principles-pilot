"""One host round trip for scheduler states, without reading calculation logs."""
import re
import shlex
from collections import defaultdict
from . import execution as ex, batch_scheduler


def batch(records):
    eligible=[r for r in records if r.get('job_id') and r['state'] in
              ('SUBMITTED','QUEUED','RUNNING','HELD','EXITING','UNKNOWN')
              and ex.scheduler_name(r) in ('pbs','slurm')]
    if not eligible:return {}
    groups=defaultdict(list);result={}
    for record in eligible:
        scheduler=ex.scheduler_name(record)
        cluster=batch_scheduler.cluster_for(record) if scheduler=='slurm' else None
        groups[(record['config']['alias'],scheduler,cluster)].append(record)
    for (alias,scheduler,cluster),rows in groups.items():
        ids=list(dict.fromkeys(str(row['job_id']) for row in rows))
        command=('qstat -f '+ ' '.join(map(shlex.quote,ids)) if scheduler=='pbs' else
                 "squeue"+(" -M "+shlex.quote(cluster) if cluster else "")+" -h --array --jobs="+shlex.quote(','.join(ids))+" -o '%i|%T'")
        receipt=ex.ssh(alias,command,timeout=30,mutating=False)
        if receipt.get('exit_code') in (None,255):
            result.update({row['id']:receipt for row in rows});continue
        states={}
        if scheduler=='pbs':
            for block in re.split(r'^Job Id:\s*',receipt.get('stdout',''),flags=re.M)[1:]:
                job=block.splitlines()[0].strip()
                state=re.search(r'^\s*job_state\s*=\s*([A-Z])',block,re.M)
                if state:states[job]='job_state = '+state.group(1)+'\n'
        else:
            for job,state in re.findall(r'^\s*([^|\s]+)\|([A-Z_+]+)\s*$',receipt.get('stdout',''),re.M):
                states[job]='FPILOT_SLURM_LIVE='+state+'\n'
        # Missing jobs continue through the existing receipt/terminal audit, never inferred complete.
        result.update({row['id']:{**receipt,'stdout':states.get(str(row['job_id']),'')} for row in rows})
    return result


def active_state(record,receipt):
    state,_,_=batch_scheduler.observation(ex.scheduler_name(record),receipt.get('stdout',''))
    return state if state in ('QUEUED','RUNNING','EXITING','HELD') else None

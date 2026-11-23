"""Batch wrappers for a single foreground program with user-supplied text inputs."""
import re
import shlex

from . import batch_scheduler, execution as ex


def resources(draft):
    value = draft.get('batch') or {}
    if not isinstance(value, dict):
        raise ValueError('调度资源格式无效')
    cores, duration = value.get('cores', 1), value.get('walltime', '00:10:00')
    if type(cores) is not int or not 1 <= cores <= 4096:
        raise ValueError('单节点核数需要在 1 到 4096 之间')
    if not isinstance(duration,str) or not re.fullmatch(r'\d{2,3}:[0-5]\d:[0-5]\d', duration) or not any(c in '123456789' for c in duration):
        raise ValueError('请输入有效时限，例如 00:10:00')
    profile = dict(ppn=cores, walltime=duration)
    for field in ('queue','node'):
        token = value.get(field,'')
        if not isinstance(token,str) or token and not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}',token):
            raise ValueError('队列或节点名格式无效')
        profile[field] = token
    return profile


def wrapper(scheduler, profile):
    cores, node, queue = profile['ppn'], profile['node'], profile['queue']
    duration = profile['walltime']
    if scheduler == 'pbs':
        header = ['#PBS -N fpilot-program','#PBS -S /bin/bash',f'#PBS -l nodes={node or "1"}:ppn={cores}',
                  '#PBS -l walltime='+duration]
        if queue: header.append('#PBS -q '+queue)
        directory = 'PBS_O_WORKDIR'
    else:
        header = ['#SBATCH --job-name=fpilot-program','#SBATCH --nodes=1',f'#SBATCH --ntasks={cores}',
                  '#SBATCH --time='+duration]
        if node: header.append('#SBATCH --nodelist='+node)
        if queue: header.append('#SBATCH --partition='+queue)
        directory = 'SLURM_SUBMIT_DIR'
    return '\n'.join(['#!/bin/bash',*header,'cd "${'+directory+':?}" || exit 125',
                      '/bin/bash _fpilot/program.sh > _fpilot/program.stdout 2> _fpilot/program.stderr',
                      'rc=$?', 'printf "%s\\n" "$rc" > _fpilot/exit.tmp && mv _fpilot/exit.tmp _fpilot/exit',
                      'exit "$rc"',''])


def refresh(record, poll_receipt=None):
    old = record.get('evidence',{})
    if record['state'] in ('PREPARED','CANCELLED') or (old.get('file_evidence_checked') and old.get('scheduler_exit_code') is not None):
        return record
    from .program_tasks import output_evidence
    ex.assert_recovery_target(record)
    previous = record.get('updated_at')
    scheduler = ex.scheduler_name(record)
    if poll_receipt and record.get('job_id') and poll_receipt.get('exit_code') == 0:
        phase,_,facts = batch_scheduler.observation(scheduler,poll_receipt.get('stdout',''))
        if phase in ('RUNNING','QUEUED','EXITING','HELD'):
            record.update(state=phase,evidence=facts,updated_at=ex.now(),tracking_error='')
            return ex.save(record,expected_updated_at=previous)
    command = ('cd '+shlex.quote(record['remote_path_absolute'])+' || exit 3\n'
               +'if test -r _fpilot/exit; then printf "FPILOT_EXIT="; cat _fpilot/exit; fi\n'
               +batch_scheduler.query(scheduler,record))
    receipt = ex.ssh(record['config']['alias'],command,timeout=30,mutating=False)
    if receipt.get('exit_code') != 0:
        record.update(tracking_error=receipt.get('stderr') or '未取得状态回执',updated_at=ex.now())
        return ex.save(record,expected_updated_at=previous)
    output = receipt['stdout']
    identity = batch_scheduler.job_identity(scheduler,output)
    if not record.get('job_id'): record.update(identity)
    phase, finished, evidence = batch_scheduler.observation(scheduler,output)
    match = re.search(r'^FPILOT_EXIT=(-?\d+)$',output,re.M)
    code = int(match[1]) if match else None
    evidence['wrapper_exit'] = code
    if not record.get('job_id'):
        phase = 'UNKNOWN'
    elif phase == 'FAILED' or code not in (None,0):
        phase = 'FAILED'
    elif finished and code == 0:
        phase = 'COMPLETED'
    if record['state'] in ('COMPLETED','FAILED') and phase != record['state']:
        return record
    if phase in ('COMPLETED','FAILED'):
        evidence.update({'file_evidence_checked':True,'outputs':old['outputs']} if old.get('file_evidence_checked') else output_evidence(record))
    record.update(state=phase,evidence=evidence,status_receipt=receipt,updated_at=ex.now(),tracking_error='')
    return ex.save(record,expected_updated_at=previous)

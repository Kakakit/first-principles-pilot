"""PBS and Slurm commands and receipts shared by calculation types."""
import re
import shlex


def submit_command(scheduler,script,seed=None):
    if scheduler not in ('pbs','slurm'):
        raise ValueError('需要 PBS 或 Slurm 调度器')
    command='sbatch --parsable' if scheduler=='slurm' else 'qsub'
    if seed is not None:
        command+=(' --export=ALL,' if scheduler=='slurm' else ' -v ')+shlex.quote('W90_SEED='+seed)
    return command+' '+shlex.quote(script)


def job_identity(scheduler,output):
    token=r'[0-9]+(?:_[0-9]+)?(?:;[A-Za-z0-9_.-]+)?' if scheduler=='slurm' else r'[0-9]+(?:\.[A-Za-z0-9_.-]+)?'
    match=re.search(r'^FPILOT_JOB_ID=('+token+r')[ \t]*$',output,re.M)
    if not match:return {}
    job,_,cluster=match[1].partition(';')
    if cluster.lower()=='all':return {}
    return {'job_id':job,**({'job_cluster':cluster} if cluster else {})}


def cluster_for(record):
    cluster=record.get('job_cluster')
    if cluster is None:
        original=job_identity('slurm',(record.get('dispatch_receipt') or {}).get('stdout',''))
        if original.get('job_id')==record.get('job_id'):cluster=original.get('job_cluster')
    if cluster and (cluster.lower()=='all' or not re.fullmatch(r'[A-Za-z0-9_.-]+',cluster)):
        raise ValueError('Slurm 集群名无效')
    return cluster


def slurm_query(job,options):
    return (f"squeue{options} -h -j {job} -o 'FPILOT_SLURM_LIVE=%T' || true\n"
            +f"sacct{options} -n -P -j {job} --format=JobIDRaw,State,ExitCode 2>/dev/null | "
            +f"awk -F'|' -v job={job} '$1 == job {{print \"FPILOT_SLURM_ACCOUNT=\"$2\"|\"$3; exit}}' || true\n")


def query(scheduler,record):
    job=record.get('job_id')
    if not job:return recovered_job_command(scheduler)
    if scheduler=='pbs':return pbs_history_command(job)
    cluster=cluster_for(record)
    return slurm_query(shlex.quote(job),' -M '+shlex.quote(cluster) if cluster else '')


def pbs_history_command(job_id):
    """A bounded, exact-job fallback after qstat has aged out the job."""
    return _pbs_query(shlex.quote(job_id))


def _pbs_query(job):
    return (f'''if qstat -f {job} 2>/dev/null; then :; else
  if command -v tracejob >/dev/null 2>&1; then
    trace=$(tracejob -n 30 {job} 2>/dev/null || true)
    if printf '%s\\n' "$trace" | awk -v job={job} '$1 == "Job:" && $2 == job {{found=1}} END {{exit !found}}'; then
      printf 'FPILOT_PBS_TRACE_EXIT='
      printf '%s\\n' "$trace" | sed -n -E 's/.*Exit_status=([0-9]+).*/\\1/p' | tail -1
      printf 'FPILOT_PBS_TRACE_COMPLETE='
      printf '%s\\n' "$trace" | grep -c 'dequeuing from .*state COMPLETE' || true
    fi
  fi
fi
''')


def pbs_history_evidence(output):
    exit_match = re.search(r'^FPILOT_PBS_TRACE_EXIT=(\d+)$', output, re.M)
    complete_match = re.search(r'^FPILOT_PBS_TRACE_COMPLETE=([1-9]\d*)$', output, re.M)
    return (int(exit_match.group(1)) if exit_match else None, bool(complete_match))


def recovered_job_command(scheduler):
    """Query the original receipt in the same read, with no shell evaluation."""
    if scheduler=='pbs':
        command="""if test -s .job-id; then
  fpilot_job=$(head -c 256 .job-id | awk '/^[0-9]+([.][A-Za-z0-9_.-]+)?$/ {print; exit}')
  if test -n "$fpilot_job"; then
    printf 'FPILOT_JOB_ID=%s\\n' "$fpilot_job"
"""
        command+=_pbs_query('"$fpilot_job"')
    else:
        command="""if test -s .job-id; then
  marker=$(head -c 256 .job-id | awk '/^[0-9]+(;[A-Za-z0-9_.-]+)?$/ {print; exit}')
  if test -n "$marker"; then
    fpilot_job=${marker%%;*}
    set --
    case "$marker" in *';'[aA][lL][lL]) exit 1 ;; esac
    case "$marker" in *';'*) set -- -M "${marker#*;}" ;; esac
    printf 'FPILOT_JOB_ID=%s\\n' "$marker"
"""
        command+=slurm_query('"$fpilot_job"', ' "$@"')
    return command+'  fi\nfi\n'


def observation(scheduler, output):
    """Return scheduler facts; the program caller decides scientific completion."""
    state = source = exit_code = None
    if scheduler == 'pbs':
        live = re.search(r'job_state\s*=\s*([A-Z])', output)
        trace_exit, complete = pbs_history_evidence(output)
        if live:
            state, source = live[1], 'qstat'
        elif complete:
            state, source = 'C', 'tracejob'
        reported = re.search(r'^\s*Exit_status\s*=\s*(-?\d+)\s*$', output, re.M)
        status = int(reported[1]) if live and reported else trace_exit if complete else None
        finished = state == 'C' and status in (None, 0) and (live is not None or trace_exit == 0)
        if state == 'F':finished = status == 0
        phase = {'Q':'QUEUED','R':'RUNNING','H':'HELD','E':'EXITING'}.get(state,'UNKNOWN')
        if state in ('C','F') and status is not None:
            exit_code = str(status)
            if status != 0:phase = 'FAILED'
    else:
        live = re.search(r'^FPILOT_SLURM_LIVE=([A-Z_+]+)', output, re.M)
        account = re.search(r'^FPILOT_SLURM_ACCOUNT=([A-Z_+]+)\|(\S+)', output, re.M)
        if live:
            state, source = live[1], 'squeue'
        elif account:
            state, source = account[1], 'sacct'
        if account:exit_code = account[2]
        terminal = account[1].rstrip('+') if account else None
        finished = terminal == 'COMPLETED' and exit_code == '0:0'
        phase = {'PENDING':'QUEUED','CONFIGURING':'QUEUED','RUNNING':'RUNNING',
                 'COMPLETING':'EXITING','SUSPENDED':'HELD'}.get(state,'UNKNOWN')
        if terminal in {'FAILED','CANCELLED','TIMEOUT','NODE_FAIL','OUT_OF_MEMORY','PREEMPTED','BOOT_FAIL','DEADLINE','REVOKED'} or terminal == 'COMPLETED' and not finished:
            phase = 'FAILED'
    evidence = {'scheduler':scheduler,'scheduler_state':state,
                'scheduler_evidence_source':source,'scheduler_exit_code':exit_code}
    return phase, finished, evidence

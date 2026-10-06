"""Platform adapter for the same CLI protocol over SSH or PBS/Slurm.

Only explicitly requested analyses are staged. Durable job.id and a local
dispatch state make recovery read-only: an uncertain submission is never retried.
"""
import copy
import io
import json
import re
import shlex
import subprocess
import sys
import tarfile
import uuid
from pathlib import Path
from . import execution as ex, remote_paths, servers
from . import extension_protocol as protocol
from .storage import ROOT

SAFE_PULL = Path(__file__).resolve().parents[1]/'resources/preflight/safe_pull_from_cluster.py'


def runtimes():
    path=ROOT/'extensions-v2/runtimes.json'
    return json.loads(path.read_text()) if path.exists() else {}


def save_runtime(value):
    if not isinstance(value,dict) or set(value)!={'alias','python','config','node','cores','reason'}: raise ValueError('远端运行时字段无效')
    value={**value,'config':servers.validate(value['config'])}
    if value['alias']!=value['config']['alias']: raise ValueError('主机与配置不一致')
    if not isinstance(value['python'],str) or not value['python'].startswith('/') or any(c in value['python'] for c in '\n\r\0'): raise ValueError('请输入已有 Python 的单行绝对路径')
    if value['node'] and not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',value['node']): raise ValueError('计算节点名称无效')
    if value['cores'] is not None and (type(value['cores']) is not int or value['cores']<1): raise ValueError('核数须为正整数或默认')
    result=runtimes(); result[value['alias']]=value; protocol.atomic_json(ROOT/'extensions-v2/runtimes.json',result)
    return value


def cli_command(runtime,entrypoint,action,seconds):
    runner="import os,signal,subprocess,sys; p=subprocess.Popen(sys.argv[2:],start_new_session=True)\ntry: code=p.wait(timeout=int(sys.argv[1]))\nexcept subprocess.TimeoutExpired:\n os.killpg(p.pid,signal.SIGKILL); p.wait(); print('Extension CLI timeout; process group stopped',file=sys.stderr); code=124\nsys.exit(code)"
    return ' '.join(shlex.quote(str(s)) for s in [runtime['python'],'-c',runner,seconds,runtime['python'],'package/'+entrypoint,action])


def doctor(receipt,runtime):
    """Run only doctor in an isolated small-package directory; never dispatch."""
    directory=remote_paths.validate_root(runtime['config'].get('remote_workspace_root')).rstrip('/')+'/extension-checks/'+uuid.uuid4().hex
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w:gz') as archive:
        archive.add(receipt['root'],arcname='package',filter=lambda info:None if '__pycache__' in info.name else info)
    command='set -e\ndest='+remote_paths.shell_path(directory)+'\ntest ! -e "$dest"\nmkdir -p "$dest"\ntar -xzf - -C "$dest"\ncd "$dest"\nexport PYTHONDONTWRITEBYTECODE=1\n'+cli_command(runtime,receipt['manifest']['entrypoint'],'doctor',30)+'\n'
    try:
        response=ex.ssh(runtime['alias'],command,stream.getvalue(),timeout=75)
        if response.get('exit_code')!=0:raise ValueError(response.get('stderr') or '服务器环境检查失败')
        result=json.loads(response['stdout'])
        if result.get('schema')!='fpilot.extension-doctor.v2' or type(result.get('ready')) is not bool:raise ValueError('doctor 回执格式无效')
        return protocol.doctor_result(result,runtime['python'])
    except Exception as exc:
        return {'schema':'fpilot.extension-doctor.v2','ready':False,'python':runtime['python'],'missing':[],'reason':str(exc)[-1500:]}


def resource_preview(runtime):
    config=runtime['config']
    report=servers.capacity_probe(config,purpose='analysis')
    if report['availability']['exit_code'] or report['diagnostics']['exit_code']: raise ValueError('主机离线或容量/队列诊断失败')
    if config['alias']=='jutai-1' and runtime['node']=='node06': raise ValueError('node06 已排除')
    try:
        selection=servers.selector.select_resources(report['preflight'],config['alias'],runtime['node'],user_override=runtime['cores'],user_override_evidence=runtime['reason'])
    except ValueError as exc:
        nodes=[n for h in report['preflight']['hosts'] for n in h.get('nodes',[]) if n.get('NODE')==runtime['node']]
        if len(nodes)==1 and (int(nodes[0].get('CPUS_FREE',0))<=0 or runtime['cores'] and int(nodes[0].get('CPUS_FREE',0))<runtime['cores']):
            return {'ready':False,'waiting':True,'reason':'所选节点繁忙，等待资源；尚未提交','probe':report['id']}
        raise ValueError(str(exc))
    queue=next((q for q in report['queues'] if q['name']==config['queue']),None)
    if not queue: raise ValueError('未发现所选队列/分区')
    scheduler=servers.normalize_scheduler(config['scheduler'])
    if scheduler=='pbs' and (queue.get('enabled')!='True' or queue.get('started')!='True'): raise ValueError('PBS 队列未启用')
    if scheduler=='slurm' and queue.get('available')!='up': raise ValueError('Slurm 分区不可用')
    maximum=queue.get('resources_max.walltime')
    if maximum and servers.seconds(config['walltime'])>servers.seconds(maximum): raise ValueError('运行时间超过队列上限')
    return {'ready':True,'waiting':False,'selection':selection,'probe':report['id']}


def script(runtime, directory, entrypoint, mode, cores=None, timeout=300):
    config=runtime['config'];scheduler=servers.normalize_scheduler(config['scheduler'])
    header=['#!/bin/bash']
    if mode=='scheduler':
        if not runtime['node']: raise ValueError('调度运行需明确计算节点')
        cores=cores or runtime['cores']
        if not cores: raise ValueError('调度运行需经过实时资源预览')
        if scheduler=='pbs':
            header += ['#PBS -N fp_analysis','#PBS -q '+config['queue'],'#PBS -l nodes='+runtime['node']+':ppn='+str(cores),'#PBS -l walltime='+config['walltime']]
        else:
            header += ['#SBATCH --job-name=fp_analysis','#SBATCH --partition='+config['queue'],'#SBATCH --nodelist='+runtime['node'],'#SBATCH --cpus-per-task='+str(cores),'#SBATCH --time='+config['walltime']]
    def command(action, seconds):
        return cli_command(runtime,entrypoint,action,seconds)
    # Keep command failures observable: errexit would skip the durable exit receipt.
    header += ['set -u','cd '+remote_paths.shell_path(directory)+' || exit "$?"','export PYTHONDONTWRITEBYTECODE=1',
               command('doctor',30)+' > doctor.json 2> doctor.err',
               'code=$?', 'if test "$code" -eq 0; then',
               shlex.quote(runtime['python'])+" -c 'import json,sys; sys.exit(0 if json.load(open(\"doctor.json\")).get(\"ready\") is True else 4)'",'code=$?','fi', 'if test "$code" -eq 0; then',
               command('check',min(timeout,60))+' --request request.json > check.json 2> check.err','code=$?','fi',
               'if test "$code" -eq 0; then',
               shlex.quote(runtime['python'])+" -c 'import json; import sys; sys.exit(0 if json.load(open(\"check.json\")).get(\"status\")==\"APPLICABLE\" else 3)'",'code=$?','fi',
               'if test "$code" -eq 0; then',
               command('run',timeout)+' --request request.json > stdout.log 2> stderr.log','code=$?','fi',
               "printf '{\"exit_code\":%s}\\n' \"$code\" > exit.json.tmp",'mv exit.json.tmp exit.json','exit "$code"']
    return '\n'.join(header)+'\n'


def stage(record,directory,request):
    runtime=record['runtime'];raw_root=runtime['config'].get('remote_workspace_root') or remote_paths.DEFAULT_WORKSPACE_ROOT
    remote_dir=remote_paths.validate_root(raw_root).rstrip('/')+'/analyses/'+record['id']
    request=copy.deepcopy(request)
    request['output_dir']='__WORKDIR__/outputs';request['result_file']='__WORKDIR__/outputs/result.json'
    copies=[]
    for role,items in request['inputs'].items():
        for index,item in enumerate(items):
            name=str(index)+'-'+role+'-'+item['name']; target='inputs/'+name
            if item.get('remote_path'):
                source=remote_paths.shell_path(item['remote_path'])
                expected=str(item['size_bytes'])+':'+str(item['mtime_epoch'])
                copies += ['test -f '+source+'; test ! -L '+source,
                           'before=$(stat -c \'%s:%Y\' -- '+source+'); test "$before" = '+shlex.quote(expected),
                           'cp -- '+source+' '+shlex.quote(target),
                           'test "$(stat -c \'%s:%Y\' -- '+source+')" = "$before"',
                           'test "$(stat -c %s -- '+shlex.quote(target)+')" = '+str(item['size_bytes'])]
            item['path']='__WORKDIR__/'+target
    protocol.atomic_json(directory/'request.remote.json',request)
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w:gz') as archive:
        archive.add(directory/'package',arcname='package',filter=lambda info: None if '__pycache__' in info.name else info)
        archive.add(directory/'request.remote.json',arcname='request.json')
        archive.add(directory/'launch.sh',arcname='launch.sh')
        if (directory/'inputs').exists(): archive.add(directory/'inputs',arcname='inputs')
    python=shlex.quote(runtime['python'])
    rewrite="import json,os; p='request.json'; v=json.load(open(p)); t=json.dumps(v).replace('__WORKDIR__',os.getcwd()); open(p,'w').write(t)"
    command='set -e\ndest='+remote_paths.shell_path(remote_dir)+'\ntest ! -e "$dest"\nmkdir -p "$dest"\ntar -xzf - -C "$dest"\ncd "$dest"\nmkdir -p inputs outputs\n'+python+' -c '+shlex.quote(rewrite)+'\n'+'\n'.join(copies)+'\npwd -P\n'
    receipt=ex.ssh(runtime['alias'],command,stream.getvalue(),timeout=180)
    if receipt.get('exit_code')!=0: raise ValueError('独立分析目录准备失败：'+receipt.get('stderr','')[-1000:])
    absolute=receipt['stdout'].strip().splitlines()[-1]
    if not absolute.startswith('/') or not absolute.endswith('/'+record['id']): raise ValueError('分析目录回执无效')
    return absolute


def submit(record):
    runtime=record['runtime'];directory=record['remote_dir'];scheduler=servers.normalize_scheduler(runtime['config']['scheduler'])
    command='set -e\ncd '+remote_paths.shell_path(directory)+'\nif test -s job.id; then cat job.id; else '+('qsub launch.sh' if scheduler=='pbs' else 'sbatch --parsable launch.sh')+' > job.id; cat job.id; fi\n'
    result=ex.ssh(runtime['alias'],command,timeout=90)
    job=result.get('stdout','').strip().split(';')[0]
    pattern=r'\d+(?:\.[A-Za-z0-9_.-]+)?' if scheduler=='pbs' else r'\d+'
    if result.get('exit_code')!=0 or not re.fullmatch(pattern,job): raise ValueError('未收到精确 job ID，状态未知；只允许核对回执，不重复提交')
    return job


def direct(record):
    return ex.ssh(record['runtime']['alias'],'set -e\ncd '+remote_paths.shell_path(record['remote_dir'])+'\nbash launch.sh',timeout=min(record['manifest']['execution']['timeout_seconds']+60,900))


def inspect(record):
    directory=record['remote_dir'];runtime=record['runtime']
    python=shlex.quote(runtime['python'])
    code="import json,pathlib; p=pathlib.Path('.'); print(json.dumps({'exit':json.load(open('exit.json')) if (p/'exit.json').is_file() else None,'job_id':(p/'job.id').read_text().strip().split(';')[0] if (p/'job.id').is_file() else None,'check':json.load(open('check.json')) if (p/'check.json').is_file() else None,'doctor':json.load(open('doctor.json')) if (p/'doctor.json').is_file() else None}))"
    result=ex.ssh(runtime['alias'],'set -e\ncd '+remote_paths.shell_path(directory)+'\n'+python+' -c '+shlex.quote(code),timeout=30)
    if result.get('exit_code')!=0: raise ValueError('无法读取分析回执；保留原 job ID')
    return json.loads(result['stdout'])


def pull(record,name,destination):
    if not name or Path(name).is_absolute() or '..' in Path(name).parts: raise ValueError('产物路径越界')
    destination.parent.mkdir(parents=True,exist_ok=True)
    remote=record['remote_dir']+'/'+name
    from .safe_files import pull_many
    pull_many(record['runtime']['alias'],[{'remote_path':remote,'destination':str(destination)}],timeout=120)
    return destination


def source_pull(source,name,destination):
    fact=next(f for f in source['files'] if f['name']==name)
    from datetime import datetime
    expected=str(fact['size_bytes'])+':'+str(int(datetime.fromisoformat(fact['modified_at']).timestamp()))
    path=source['remote_root'].rstrip('/')+'/'+name
    command='set -e\ntest -f '+remote_paths.shell_path(path)+'; test ! -L '+remote_paths.shell_path(path)+'\nstat -c \'%s:%Y\' -- '+remote_paths.shell_path(path)+'\ncd '+remote_paths.shell_path(source['remote_root'])+'\nprintf \'%s/%s\\n\' "$(pwd -P)" '+shlex.quote(name)
    stat=ex.ssh(source['alias'],command,timeout=30);lines=stat.get('stdout','').strip().splitlines()
    if stat.get('exit_code')!=0 or len(lines)!=2 or lines[0]!=expected or not lines[1].startswith('/'):raise ValueError('来源大小或修改时间变化，请刷新文件清单后重新准备')
    path=lines[1]
    destination.parent.mkdir(parents=True,exist_ok=True)
    from .safe_files import pull_many
    pull_many(source['alias'],[{'remote_path':path,'destination':str(destination)}],timeout=120)
    if destination.stat().st_size!=fact['size_bytes']:raise ValueError('来源文件回传大小变化，请重新准备')
    return destination.read_bytes()


def output_info(record,names):
    # One bounded stat operation for the complete output list; no large hashing.
    code="""import json,pathlib,sys
root=pathlib.Path('outputs').resolve(); out={}
for name in json.loads(sys.argv[1]):
 p=root/name
 if not name or pathlib.Path(name).is_absolute() or '..' in pathlib.Path(name).parts or p.is_symlink() or not p.is_file() or root not in p.resolve().parents: raise ValueError('invalid output path')
 out[name]={'size_bytes':p.stat().st_size}
print(json.dumps(out))"""
    command='set -e\ncd '+remote_paths.shell_path(record['remote_dir'])+'\n'+shlex.quote(record['runtime']['python'])+' -c '+shlex.quote(code)+' '+shlex.quote(json.dumps(names))
    result=ex.ssh(record['runtime']['alias'],command,timeout=30)
    if result.get('exit_code')!=0:raise ValueError('分析产物清单无效或无法读取')
    return json.loads(result['stdout'])


def queued(record):
    job=record.get('job_id')
    if not job:return False
    if not re.fullmatch(r'\d+(?:\.[A-Za-z0-9_.-]+)?',job):raise ValueError('job ID 无效')
    scheduler=servers.normalize_scheduler(record['runtime']['config']['scheduler'])
    command=('qstat -f '+shlex.quote(job)) if scheduler=='pbs' else ('squeue -h -j '+shlex.quote(job)+' -o %T')
    response=ex.ssh(record['runtime']['alias'],command,timeout=30)
    if scheduler=='pbs':
        # Only explicit live queue states count; historical finished records don't.
        return response.get('exit_code')==0 and bool(re.search(r'job_state\s*=\s*[QRHETW]',response.get('stdout','')))
    return response.get('exit_code')==0 and bool(response.get('stdout','').strip())

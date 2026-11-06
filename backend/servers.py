"""Saved site configuration, read-only probes, and PBS/Slurm previews."""
import hashlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from .storage import ROOT
from .inputs import put_file

APP_ROOT = Path(__file__).resolve().parents[1]
RESOURCES = APP_ROOT / 'resources'
CHECKER = RESOURCES / 'preflight' / 'check_cluster_availability.sh'
REGISTRY_PATH = RESOURCES / 'preflight' / 'hpc_cluster_registry.json'


def helper(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


pbs = helper('web_pbs', RESOURCES/'jobs/pbs.py')
slurm = helper('web_slurm', RESOURCES/'jobs/slurm.py')
parser = helper('web_preflight', RESOURCES/'preflight/parse_preflight.py')
selector = helper('web_resources', RESOURCES/'preflight/select_node_resources.py')
DEFAULT = {'alias':'jutai-1', 'name':'FP_VASP', 'queue':'batch', 'walltime':'01:00:00',
           'mpi_executable':'/opt/intel-2020/compilers_and_libraries_2020.0.163/linux/mpi/intel64/bin/mpirun',
           'vasp_executable':'/opt/software/vasp.6.5.1/bin/vasp_std',
           'environment_script':'/opt/intel-2020/parallel_studio_xe_2020/psxevars.sh',
           'scheduler':'pbs','module':'','launcher':'','runtime_environment':'{}',
           'pmi_library':'','pmi_type':''}


def load_registry(path=None, include_removed=False):
    """Read the local cluster registry without contacting or changing a host."""
    registry_path = Path(path or REGISTRY_PATH)
    if path is None and not registry_path.exists():
        registry_path = REGISTRY_PATH.with_name('hpc_cluster_registry.example.json')
    try:
        value = json.loads(registry_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('集群登记表不可读：{}'.format(exc)) from exc
    if not isinstance(value, dict) or not isinstance(value.get('hosts'), dict):
        raise ValueError('集群登记表缺少 hosts 对象')
    if path is None:
        from .site_registration import records, removed_records
        removed=removed_records()
        hosts={**value['hosts'],**records()}
        if include_removed:
            for alias,record in removed.items():
                snapshot=record.get('host_snapshot') or record.get('host')
                if snapshot is not None:hosts[alias]=snapshot
        else:hosts={k:v for k,v in hosts.items() if k not in removed}
        value={**value,'hosts':hosts}
    return value


def _absolute_path(value):
    return isinstance(value, str) and value.startswith('/') and not any(
        char in value for char in '\r\n\x00'
    )


def _runtime_record(host, variant='vasp_std'):
    runtimes = host.get('runtimes', []) if isinstance(host, dict) else []
    if not isinstance(runtimes, list):
        return None
    for runtime in runtimes:
        if isinstance(runtime, dict) and runtime.get('variant') == variant:
            return runtime
    return next((item for item in runtimes if isinstance(item, dict)), None)


def _registered_profile(alias, host, variant='vasp_std'):
    from .execution_modes import command_mode
    runtime = _runtime_record(host, variant)
    ncl_runtime = next((item for item in host.get('runtimes', []) if isinstance(item,dict)
                        and item.get('variant') == 'vasp_ncl'), None)
    scheduler = str(host.get('scheduler', '') or '').lower()
    host_status = str(host.get('status', '') or '')
    runtime_variant = str(runtime.get('variant', '') if runtime else '')
    runtime_status = str(runtime.get('status', '') if runtime else '')
    is_pbs = scheduler in {'pbs', 'pbs/torque', 'torque'}
    is_slurm = scheduler == 'slurm'
    values = {
        'mpi_executable': runtime.get('mpi', '') if runtime else '',
        'vasp_executable': runtime.get('executable', '') if runtime else '',
        'environment_script': runtime.get('environment_script', '') if runtime else '',
        'module': runtime.get('module', '') if runtime else '',
        # The registry's srun recipe was disproved by the 1.0 live closure:
        # singleton without PMI, SIGSEGV in MPI_Ibarrier with PMI-2.  The
        # module's own Intel MPI Hydra launcher is the supported adapter.
        'launcher': 'mpirun' if is_slurm and runtime else (runtime.get('launcher', '') if runtime else ''),
        'runtime_environment': json.dumps(runtime.get('environment', {}) if runtime else {}, sort_keys=True),
        'pmi_library': '',
        'pmi_type': '',
    }
    if is_pbs:
        missing = [key for key in ('mpi_executable','vasp_executable','environment_script')
                   if not _absolute_path(values[key])]
    elif is_slurm:
        missing = []
        if not _absolute_path(values['vasp_executable']): missing.append('vasp_executable')
        if not re.fullmatch(r'[A-Za-z0-9_.+-]+/[A-Za-z0-9_.+-]+', values['module']): missing.append('module')
        if values['launcher'] != 'mpirun': missing.append('launcher')
        try:
            environment = json.loads(values['runtime_environment'])
            if not isinstance(environment, dict): missing.append('runtime_environment')
            else:
                # Intel MPI 2021 uses OFI; the old registry's bare ``tcp``
                # value produced singleton processes during the 1.0 live test.
                # Every supported Slurm preview is one-node only.  Using the
                # shared-memory fabric avoids an unnecessary OFI dependency.
                environment['I_MPI_FABRICS'] = 'shm'
                environment.pop('I_MPI_PMI_LIBRARY', None)
                values['runtime_environment'] = json.dumps(environment, sort_keys=True)
        except ValueError:
            missing.append('runtime_environment')
    else:
        missing = ['scheduler']
    reason_code = 'ready'
    reason = '{} vasp_std 运行时完整且已通过 SMOKE_CONFIRMED'.format('Slurm' if is_slurm else 'PBS')
    selectable = True
    if not (is_pbs or is_slurm):
        selectable = False
        reason_code = 'unsupported_scheduler'
        reason = '调度器 {} 暂不支持'.format(host.get('scheduler', '未登记'))
    elif runtime is None or runtime_variant != variant:
        selectable = False
        reason_code = 'unsupported_variant'
        reason = '未登记可用的 {} 运行时'.format(variant)
    elif missing:
        selectable = False
        reason_code = 'runtime_incomplete'
        reason = '登记运行时缺少 {}；不会猜测路径'.format('、'.join(missing))
    elif runtime_status != 'SMOKE_CONFIRMED':
        selectable = False
        reason_code = 'runtime_unconfirmed'
        reason = '运行时状态为 {}，未达到 SMOKE_CONFIRMED'.format(runtime_status or '未登记')
    elif host_status != 'CONFIRMED_CPU':
        selectable = False
        reason_code = 'host_unconfirmed'
        reason = '主机登记状态为 {}，未确认 CPU 配置'.format(host_status or '未登记')
    queue_value = host.get('queue') if is_pbs else host.get('partition')
    queue = queue_value if isinstance(queue_value, str) else ''
    if is_pbs or is_slurm or scheduler=='custom':
        # A registered host may be selected with a user-provided script. Missing
        # runtime paths are supplied explicitly and checked by the live probe.
        selectable = True
    if host.get('status')=='USER_CONFIGURED':
        reason_code='user_configured';reason='用户登记的运行时；只读诊断核对路径和资源，尚无程序运行验收证据'
    return {
        'alias': alias,
        'scheduler': host.get('scheduler', ''),
        **({'command_mode': command_mode(host), 'direct_limit': host.get('direct_limit', 1)}
           if command_mode(host) else {}),
        'queue': queue,
        'variant': runtime_variant or variant,
        'status': host_status,
        'runtime_status': runtime_status,
        'runtime_source':host.get('runtime_source','registered'),
        'remote_workspace_root':host.get('remote_workspace_root','$HOME'),
        'pyw90_python':(host.get('pyw90') or {}).get('python',''),
        'pyw90_package':(host.get('pyw90') or {}).get('package',''),
        'ncl_runtime_status': ncl_runtime.get('status') if ncl_runtime else 'UNREGISTERED',
        'ncl_executable': ncl_runtime.get('executable') if ncl_runtime else '',
        'runtime_complete': not missing and runtime is not None,
        'selectable': selectable,
        'enabled': selectable,
        'available': selectable,
        'supports_pbs_preview': selectable and is_pbs,
        'supports_slurm_preview': selectable and is_slurm,
        'reason_code': reason_code,
        'reason': reason,
        **values,
    }


def registered_profiles():
    """Return read-only, UI-safe choices derived from the cluster registry."""
    registry = load_registry()
    profiles = [
        _registered_profile(alias, host)
        for alias, host in registry['hosts'].items()
        if isinstance(host, dict)
    ]
    # Keep the two complete CPU profiles first while retaining every disabled
    # registry entry as an explicit, inspectable choice in the UI.
    priority = {'jutai-1': 0, 'jutai-5': 1}
    return sorted(profiles, key=lambda item: (0 if item['selectable'] else 1,
                                               priority.get(item['alias'], 99),
                                               item['alias']))


def select_profile(alias, variant='vasp_std'):
    """Materialize a validated site config from registered runtime fields."""
    if not isinstance(alias, str) or not alias:
        raise ValueError('SSH 别名必须是非空字符串')
    registry = load_registry()
    host = registry['hosts'].get(alias)
    if not isinstance(host, dict):
        raise ValueError('登记表中没有该 SSH 别名：{}'.format(alias))
    selected = _registered_profile(alias, host, variant)
    if not selected['selectable']:
        raise ValueError('{}：{}'.format(alias, selected['reason']))
    config = {
        'alias': alias,
        'pyw90_python':selected.get('pyw90_python',''),
        'pyw90_package':selected.get('pyw90_package',''),
        'name': 'FP_VASP',
        'queue': selected['queue'],
        'walltime': DEFAULT['walltime'],
        'mpi_executable': selected['mpi_executable'],
        'vasp_executable': selected['vasp_executable'],
        'environment_script': selected['environment_script'],
        'scheduler': str(selected['scheduler']).lower(),
        'module': selected['module'],
        'launcher': selected['launcher'],
        'runtime_environment': selected['runtime_environment'],
        'pmi_library': selected['pmi_library'],
        'pmi_type': selected['pmi_type'],
    }
    if host.get('runtime_source')=='script':
        config.update(runtime_source='script',program=variant,vasp_executable=variant)
    if selected.get('command_mode'):
        config['command_mode'] = selected['command_mode']
    if host.get('remote_workspace_root'):config['remote_workspace_root']=host['remote_workspace_root']
    return config


def default_config():
    """Use the preferred registered profile while retaining a legacy fallback."""
    try:
        profiles=registered_profiles()
        if profiles:return select_profile(next((p['alias'] for p in profiles if p['selectable']),profiles[0]['alias']))
        return {**DEFAULT,'alias':'','queue':'','mpi_executable':'','vasp_executable':'','environment_script':'','remote_workspace_root':'$HOME/fpilot'}
    except ValueError:
        # Keep imports and existing workspaces readable if the adjacent
        # registry is temporarily unavailable; validation still requires a
        # currently registered, eligible alias before probe/preview.
        return dict(DEFAULT)


def validate(config):
    from .execution_modes import validate_mode
    mode = validate_mode(config)
    extra = {k:config[k] for k in ('remote_workspace_root','runtime_source','program','template_id','script_templates','resource_request','script_edit_mode','pyw90_python','pyw90_package','resource_policy') if config.get(k)}
    explicit_mode = 'command_mode' in config
    config = {k: config.get(k, '') for k in DEFAULT}
    config.update(extra)
    if config.get('resource_policy','wait_idle') not in ('wait_idle','scheduler_queue'):raise ValueError('资源提交方式无效')
    if config.get('remote_workspace_root'):
        from .remote_paths import validate_root
        validate_root(config['remote_workspace_root'])
    # Eligibility comes from the registry. User-edited absolute paths remain
    # valid input, but the backend never invents a path for an incomplete entry.
    registered = select_profile(config['alias'])
    if normalize_scheduler(config.get('scheduler')) != normalize_scheduler(registered.get('scheduler')):
        raise ValueError('调度器必须与登记主机一致')
    from . import site_registration
    if config['alias'] not in site_registration.ssh_aliases():
        if site_registration.connection_identity(config['alias']).get('hostname')==config['alias']:
            raise ValueError('SSH 配置中没有该明确别名，请先配置本机 SSH')
    scheduler = normalize_scheduler(config['scheduler'])
    if scheduler == 'custom':
        if explicit_mode:
            config['command_mode'] = mode
        config.update(scheduler='custom',runtime_source='script',program=config.get('program') or 'vasp_std',vasp_executable=config.get('program') or 'vasp_std')
        return config
    if config.get('runtime_source') not in (None,'registered','script'):raise ValueError('运行方式无效')
    script_mode=config.get('runtime_source')=='script'
    if script_mode:
        program=program_name(config)
        if program not in ('vasp_std','vasp_ncl','wannier90','gpu_vasp','other'):raise ValueError('脚本程序分类无效')
        config['program']=program
        config['vasp_executable']=program
        if config.get('template_id') and not re.fullmatch('[a-f0-9]{64}',config['template_id']):raise ValueError('脚本 ID 无效')
        if config.get('script_templates'):
            try:choices=json.loads(config['script_templates'])
            except (ValueError,TypeError):raise ValueError('程序脚本选择无效')
            if not isinstance(choices,dict) or any(k not in ('vasp_std','vasp_ncl','wannier90') or not isinstance(v,str) or not re.fullmatch('[a-f0-9]{64}',v) for k,v in choices.items()):raise ValueError('程序脚本选择无效')
    for key in (() if script_mode else (('environment_script','mpi_executable','vasp_executable') if scheduler == 'pbs' else ('vasp_executable',))):
        value = config[key]
        if not isinstance(value,str) or not value.startswith('/') or any(c in value for c in '\r\n\x00'):
            raise ValueError('运行时路径必须为单行绝对路径')
    if scheduler == 'slurm' and not script_mode:
        if not re.fullmatch(r'[A-Za-z0-9_.+-]+/[A-Za-z0-9_.+-]+',config.get('module','')) or config.get('launcher') != 'mpirun':
            raise ValueError('Slurm 模块和启动器必须来自已登记运行时')
    if not re.fullmatch(r'\d{2,8}:[0-5]\d:[0-5]\d',config['walltime']) or seconds(config['walltime']) <= 0:
        raise ValueError('walltime 需为正的 HH:MM:SS')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,120}',config['name']):raise ValueError('作业名称只允许英文、数字、点、横线和下划线')
    if config['queue'] or not script_mode:
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',config['queue']):raise ValueError('队列/分区名称无效')
        profile(config,'preview',1)
    return config


def program_name(config):
    return config.get('program') if config.get('runtime_source')=='script' and config.get('program') else PurePosixPath(config.get('vasp_executable','')).name


def with_script(config, template_id, program=None):
    """The selected immutable script supplies the runtime, without inventing paths."""
    from . import job_templates
    record=job_templates.get_template(template_id)
    expected=program or program_name(config) or record['program']
    return {**config,'runtime_source':'script','program':expected,'vasp_executable':expected,'template_id':template_id}


def selected_template(config):
    if config.get('runtime_source') == 'registered': return None
    if config.get('template_id'):return config['template_id']
    from .workbench_settings import read
    return read()['defaults'].get(config['alias']+':'+program_name(config))


def resource_request(config):
    from . import gpu_requests, job_templates
    key = selected_template(config) if config.get('runtime_source') == 'script' else None
    record = job_templates.get_template(key, False) if key else None
    request = gpu_requests.resolve(config, record)
    if request and not key:
        raise ValueError('GPU 提交需要选择当前程序的已导入脚本')
    return request


def profile(config,node,ppn):
    scheduler = normalize_scheduler(config['scheduler'])
    if config.get('runtime_source')=='script':
        result={'schema':'fpilot.'+scheduler+'-site.v1','name':config['name'],'node':node,'walltime':config['walltime'],
                'vasp_executable':program_name(config),'runtime_source':'script','environment':[] if scheduler=='pbs' else {}}
        result.update({'queue':config['queue'],'ppn':ppn,'mpi_executable':''} if scheduler=='pbs' else
                      {'partition':config['queue'],'ntasks':ppn,'module':'','launcher':''})
        return result
    if scheduler == 'pbs':
        return pbs.load_profile(dict(schema='fpilot.pbs-site.v1', name=config['name'],queue=config['queue'],
            walltime=config['walltime'],node=node,ppn=ppn,mpi_executable=config['mpi_executable'],
            vasp_executable=config['vasp_executable'],environment=[
                'set -eo pipefail', 'cd "${PBS_O_WORKDIR:?PBS_O_WORKDIR is not set}"',
                'source '+shlex.quote(config['environment_script'])]))
    try:
        environment = json.loads(config['runtime_environment'])
    except ValueError as exc:
        raise ValueError('Slurm 环境登记无效') from exc
    return slurm.load_profile(dict(schema='fpilot.slurm-site.v1',name=config['name'],
        partition=config['queue'],walltime=config['walltime'],node=node,ntasks=ppn,
        vasp_executable=config['vasp_executable'],module=config['module'],launcher=config['launcher'],
        environment=environment))


def normalize_scheduler(value):
    value = str(value or '').lower()
    if not value: return 'pbs'  # read old PBS-only records/configuration
    if value in {'pbs','pbs/torque','torque'}: return 'pbs'
    if value == 'slurm': return 'slurm'
    if value in ('custom','other'): return 'custom'
    raise ValueError('只支持登记的 PBS/Torque 或 Slurm 调度器')


def seconds(value):
    days,clock=value.split('-',1) if '-' in value else ('0',value)
    parts = [int(p) for p in clock.split(':')]
    if len(parts)==1:return int(days)*86400+parts[0]*60
    if len(parts) == 2: parts.insert(0,0)
    return int(days)*86400+parts[0]*3600+parts[1]*60+parts[2]


def config_hash(config):
    return hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()


def capacity_config(config):
    return {'alias':config.get('alias'),'scheduler':normalize_scheduler(config.get('scheduler'))}


def runtime_config(config):
    from .execution_modes import is_direct
    result=capacity_config(config)
    result['runtime_source']=config.get('runtime_source','registered')
    if is_direct(config):
        result['command_mode'] = 'direct'
    if result['runtime_source']!='script':
        result.update({k:config.get(k,'') for k in ('mpi_executable','vasp_executable','environment_script','module','launcher','runtime_environment','pmi_library','pmi_type')})
    return result


def read_config():
    from .execution_modes import command_mode
    path = ROOT/'server-config.json'
    config=json.loads(path.read_text(encoding='utf-8')) if path.exists() else default_config()
    removed_alias=None
    hosts = load_registry()['hosts']
    if config.get('alias') not in hosts:
        removed_alias=config.get('alias');config=default_config()
    elif command_mode(hosts[config['alias']]):
        config['command_mode'] = command_mode(hosts[config['alias']])
    else:
        config.pop('command_mode', None)
    latest=None
    for probe_path in sorted((ROOT/'server-probes').glob('*'),key=lambda p:p.stat().st_mtime,reverse=True):
        raw=probe_path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=probe_path.name:continue
        report=json.loads(raw)
        if runtime_config(report['config'])==runtime_config(config):
            latest=dict(report,id=probe_path.name);break
    return {'config':config, 'saved':path.exists() and removed_alias is None,'probe':latest,'removed_alias':removed_alias,
            'registered_profiles':registered_profiles()}


def save_config(config):
    config = validate(config)
    # Versioned records preserve all previously saved configurations.
    raw = json.dumps(config,ensure_ascii=False,sort_keys=True).encode()
    put_file(ROOT/'server-configs'/config_hash(config),raw)
    import tempfile
    with tempfile.NamedTemporaryFile(dir=ROOT,delete=False) as f:
        f.write(raw); f.flush(); os.fsync(f.fileno()); name=f.name
    os.replace(name,ROOT/'server-config.json')
    return {'config':config,'saved':True}


def run(argv, input_text=None, timeout=45):
    if argv and argv[0]=='ssh' and os.environ.get('FPILOT_SSH_MODE','session')=='session':
        from .execution import ssh
        return ssh(argv[-2],argv[-1],input_text.encode() if input_text else None,timeout)
    if argv and argv[0]=='ssh':
        from .remote_transport import legacy
        return legacy(argv,input_text.encode() if input_text else None,timeout,262144)
    try:
        result = subprocess.run(argv,input=input_text,text=True,capture_output=True,timeout=timeout)
        return {'exit_code':result.returncode,'stdout':result.stdout[:262144],'stderr':result.stderr[:8192]}
    except subprocess.TimeoutExpired:
        return {'exit_code':None,'stdout':'','stderr':'只读诊断超时；未提交任何任务'}


def queue_records(text):
    queues = []; current=None
    for line in text.splitlines():
        if line.startswith('Queue:'):
            current={'name':line.split(':',1)[1].strip()};queues.append(current)
        elif current is not None and '=' in line:
            key,value=line.strip().split('=',1);current[key.strip()]=value.strip()
    return queues


def partition_records(text):
    records=[]
    for line in text.splitlines():
        if not line.startswith('FPILOT_PARTITION='): continue
        fields=line.split('=',1)[1].split('|')
        if len(fields)==3: records.append({'name':fields[0].rstrip('*'),'available':fields[1],'time_limit':fields[2]})
    return records


def availability_command(config):
    """Reuse the registered checker's exact resource rules inside one session."""
    text=CHECKER.read_text(encoding='utf-8')
    body=text.split("<<'REMOTE_CHECK'",1)[1].split('\n',1)[1].split('\nREMOTE_CHECK',1)[0]
    excluded=' '.join(excluded_nodes(config))
    from .remote_transport import _interpreter
    try: interpreter = _interpreter(config['alias'])
    except ValueError: interpreter = 'python3'
    gpu_source = (RESOURCES/'preflight/gpu_node_facts.py').read_text(encoding='utf-8')
    gpu_command = ('if command -v nvidia-smi >/dev/null 2>&1; then\n'
                   + shlex.quote(interpreter) + ' -c ' + shlex.quote(gpu_source) + ' || true\nfi\n')
    if not resource_request(config):gpu_command=''
    return ("printf '\\n===== %s =====\\n' "+shlex.quote(config['alias'])+'\n'
            +'(\nMOM_MAX_AGE=300\nUNTRACKED_LOAD_THRESHOLD=auto\nEXCLUDED_NODES='+shlex.quote(excluded)+'\n'+body+'\n)\n' + gpu_command)


def _probe_parts(config, commands):
    command=availability_command(config)+"printf 'FPILOT_DIAGNOSTICS_BEGIN\\n'\n"+'\n'.join(commands)+'\n'
    receipt=run(['ssh',config['alias'],command])
    before,separator,after=receipt['stdout'].partition('FPILOT_DIAGNOSTICS_BEGIN\n')
    if receipt['exit_code']==0 and not separator:
        receipt={**receipt,'exit_code':1,'stderr':'资源诊断回执不完整'}
    return ({**receipt,'stdout':before},{**receipt,'stdout':after})


def _runtime_cache(config, purpose):
    return ROOT / 'runtime-probes' / (config_hash(runtime_config(config)) + '-' + purpose + '.json')


def _store_probe(report):
    raw = json.dumps(report, ensure_ascii=False, sort_keys=True).encode()
    key = hashlib.sha256(raw).hexdigest()
    put_file(ROOT / 'server-probes' / key, raw)
    return dict(report, id=key)


def capacity_probe(config, purpose='vasp', context=None):
    if normalize_scheduler(config.get('scheduler'))=='custom':
        return {'id':'custom','observed_at':datetime.now(timezone.utc).isoformat(),'config':config,'preflight':{'hosts':[]},'queues':[],'checks':{},'runtime_evidence':'用户自定义命令，不检测队列资源','availability':{'stdout':'','stderr':'','exit_code':0},'diagnostics':{'stdout':'','stderr':'','exit_code':0}}
    """Always query live capacity and queues; reuse only unchanged runtime checks."""
    path = _runtime_cache(config, purpose)
    if not path.exists():
        return probe(config, purpose=purpose) if purpose != 'vasp' else probe(config)
    config = validate(config)
    try:
        cached = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        invalidate_runtime(config, purpose)
        return probe(config, purpose=purpose)
    argv = ['env'] + (['EXCLUDED_NODES='+' '.join(excluded_nodes(config))] if excluded_nodes(config) else [])
    scheduler = normalize_scheduler(config['scheduler'])
    command = 'qstat -Qf' if scheduler == 'pbs' else "sinfo -h -o 'FPILOT_PARTITION=%P|%a|%l'"
    shared_key = (config['alias'], scheduler,tuple(sorted(excluded_nodes(config))),bool(resource_request(config)))
    if context is not None and shared_key in context:
        availability, diagnostics = context[shared_key]
    else:
        availability,diagnostics=_probe_parts(config,['set -e',command])
        if context is not None:
            context[shared_key] = availability, diagnostics
    if availability['exit_code'] or diagnostics['exit_code']:
        invalidate_runtime(config, purpose)
    from .site_registration import connection_identity
    if cached.get('connection') and cached['connection']!=connection_identity(config['alias']):
        invalidate_runtime(config,purpose)
        return probe(config,purpose=purpose,force=True)
    report = {**cached, 'config':config,'config_hash':config_hash(config),'observed_at': datetime.now(timezone.utc).isoformat(),
              'preflight': parser.parse_preflight(availability['stdout']+'\n'+availability['stderr']),
              'availability': availability, 'diagnostics': diagnostics,
              'queues': queue_records(diagnostics['stdout']) if scheduler == 'pbs' else partition_records(diagnostics['stdout']),
              'diagnostic_mode': 'live-capacity',
              'runtime_checked_at': cached['observed_at'],
              'runtime_evidence': '运行时路径检查复用 '+cached['observed_at']+' 的同配置结果；容量和队列为本次实时检查。'}
    return _store_probe(report)


def invalidate_runtime(config, purpose='vasp'):
    _runtime_cache(config, purpose).unlink(missing_ok=True)
    if purpose == 'vasp' and config.get('vasp_executable'):
        for variant in ('vasp_std', 'vasp_ncl'):
            _runtime_cache({**config, 'vasp_executable': str(PurePosixPath(config['vasp_executable']).with_name(variant))}, purpose).unlink(missing_ok=True)


def probe_many(configs):
    """One host/runtime diagnostic can describe both standard and SOC programs."""
    result, reports = {}, []
    for config in configs:
        key = config_hash(config)
        if key in result:
            continue
        candidate = next((item for item in reports if item.get('config') and
                          (config.get('runtime_source')=='script' and runtime_config(item['config'])==runtime_config(config) or
                          {k: v for k, v in item['config'].items() if k != 'vasp_executable'} ==
                          {k: v for k, v in validate(config).items() if k != 'vasp_executable'} and
                          item.get('checks', {}).get(PurePosixPath(config['vasp_executable']).name) == 'OK')), None)
        if candidate:
            normalized = validate(config)
            report = {**candidate, 'config': normalized, 'config_hash': config_hash(normalized),
                      'checks': {**candidate['checks'], 'vasp_executable': 'OK'}}
            report.pop('id', None)
            result[key] = _store_probe(report)
        else:
            result[key] = probe(config)
            reports.append(result[key])
    return result


def probe(config, purpose='vasp', force=False):
    if normalize_scheduler(config.get('scheduler'))=='custom':
        return {'id':'custom','observed_at':datetime.now(timezone.utc).isoformat(),'config':config,'preflight':{'hosts':[]},'queues':[],'checks':{},'runtime_evidence':'用户自定义命令，不检测队列资源','availability':{'stdout':'','stderr':'','exit_code':0},'diagnostics':{'stdout':'','stderr':'','exit_code':0}}
    if purpose not in ('vasp', 'wannier90', 'analysis'):
        raise ValueError('诊断用途无效')
    config = validate(config)
    if force:
        invalidate_runtime(config, purpose)
    if not force and _runtime_cache(config, purpose).exists():
        return capacity_probe(config, purpose)
    checker_argv = ['env']
    if excluded_nodes(config):
        checker_argv.append('EXCLUDED_NODES='+' '.join(excluded_nodes(config)))
    checker_argv += ['bash',str(CHECKER),config['alias']]
    scheduler=normalize_scheduler(config['scheduler'])
    check_program=purpose=='vasp' and config.get('runtime_source')!='script'
    if scheduler == 'pbs':
        commands = ['set -e', 'command -v qstat', 'command -v qsub']
        path_keys=('mpi_executable','vasp_executable','environment_script') if check_program else ()
        for key in path_keys:
            flag = '-r' if key=='environment_script' else '-x'
            commands.append(f"if test {flag} {shlex.quote(config[key])}; then echo CHECK_{key}=OK; else echo CHECK_{key}=MISSING; fi")
        commands += ['echo QUEUES_BEGIN','qstat -Qf']
    else:
        commands=['set -e','command -v sbatch','command -v squeue','command -v sacct',
                  "sinfo -h -o 'FPILOT_PARTITION=%P|%a|%l'"]
        if check_program:
            commands[4:4]=['source /etc/profile.d/lmod.sh','module load '+shlex.quote(config['module']),'command -v mpirun',
                           'if test -x '+shlex.quote(config['vasp_executable'])+'; then echo CHECK_vasp_executable=OK; else echo CHECK_vasp_executable=MISSING; fi']
        path_keys=('vasp_executable',) if check_program else ()
    if check_program:
        variants = []
        for variant in ('vasp_std', 'vasp_ncl'):
            path = str(PurePosixPath(config['vasp_executable']).with_name(variant))
            variants.append('if test -x '+shlex.quote(path)+'; then echo CHECK_'+variant+'=OK; else echo CHECK_'+variant+'=MISSING; fi')
        commands[1:1] = variants
    availability,diagnostics=_probe_parts(config,commands)
    parsed=parser.parse_preflight(availability['stdout']+'\n'+availability['stderr'])
    checks = {k:v for k,v in re.findall(r'^CHECK_(\w+)=(\w+)$',diagnostics['stdout'],re.M)}
    from .site_registration import connection_identity
    report = {'config':config,'config_hash':config_hash(config),'connection':connection_identity(config['alias']),'purpose':purpose,'observed_at':datetime.now(timezone.utc).isoformat(),
              'preflight':parsed,'availability':availability,'diagnostics':diagnostics,'checks':checks,
              'queues':queue_records(diagnostics['stdout']) if scheduler=='pbs' else partition_records(diagnostics['stdout']),
              'runtime_evidence':('只读检查调度器、队列和容量；环境与程序命令使用导入脚本，尚无本次程序运行证据。' if config.get('runtime_source')=='script' else
                                  '只读检查调度器、队列和容量；Wannier90 程序与 MPI 命令由所选脚本负责，提交前请核对预览。'
                                  if purpose=='wannier90' else '只读检查调度器、队列和容量；分析 Python 与依赖由扩展 doctor 检查。' if purpose=='analysis' else
                                  '路径/命令存在性是本次检查；具体 VASP/MPI 运行能力来自登记的 SMOKE_CONFIRMED，本次尚未执行 VASP'),
              'remote_mutation':False}
    required_checks = path_keys if purpose == 'vasp' else ()
    if availability['exit_code'] == 0 and diagnostics['exit_code'] == 0 and all(checks.get(key) == 'OK' for key in required_checks):
        path = _runtime_cache(config, purpose)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True), encoding='utf-8')
        if check_program:
            for variant in ('vasp_std', 'vasp_ncl'):
                if checks.get(variant) != 'OK':
                    continue
                variant_config = {**config, 'vasp_executable': str(PurePosixPath(config['vasp_executable']).with_name(variant))}
                variant_report = {**report, 'config': variant_config, 'config_hash': config_hash(variant_config),
                                  'checks': {**checks, 'vasp_executable': 'OK'}}
                _runtime_cache(variant_config, purpose).write_text(json.dumps(variant_report, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    else:
        invalidate_runtime(config, purpose)
    return _store_probe(report)


def preview(config, probe_id, node, cores=None, reason='', purpose='vasp'):
    config=validate(config)
    if config['scheduler']=='custom':
        from .custom_commands import preview
        return preview(config)
    queued=config.get('resource_policy')=='scheduler_queue'
    if queued:
        report={'config':config,'preflight':{'hosts':[]},'queues':[], 'checks':{},
                'diagnostics':{'exit_code':0},'availability':{'exit_code':0}}
        if probe_id and re.fullmatch('[a-f0-9]{64}',probe_id):
            path=ROOT/'server-probes'/probe_id
            if path.exists():
                try:
                    cached=json.loads(path.read_text())
                    if capacity_config(cached['config'])==capacity_config(config): report=cached
                except (ValueError,KeyError,OSError): pass
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',node or ''):raise ValueError('请填写计算节点名称')
    else:
        if not re.fullmatch('[a-f0-9]{64}',probe_id):raise ValueError('请先运行诊断')
        raw=(ROOT/'server-probes'/probe_id).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=probe_id:raise ValueError('诊断记录校验失败')
        report=json.loads(raw)
        if capacity_config(report['config'])!=capacity_config(config):raise ValueError('服务器或调度器已改变，请检测新服务器')
        from .site_registration import connection_identity
        if report.get('connection') and report['connection']!=connection_identity(config['alias']):raise ValueError('实际 SSH 目标已改变，请重新检测节点')
        if node in excluded_nodes(config):raise ValueError(node+' 已由此服务器的配置排除')
    nodes=[n for h in report['preflight']['hosts'] for n in h.get('nodes',[]) if n.get('NODE')==node]
    if len(nodes)!=1 and not (queued and re.fullmatch(r'[A-Za-z0-9_.-]+',node) and type(cores) is int and cores>0):raise ValueError('请选择本次诊断中的节点，或填写明确节点和请求核数')
    facts=nodes[0] if len(nodes)==1 else {'NODE':node,'CPUS_TOTAL':str(cores),'STATUS':'UNKNOWN'}
    blockers=[]; selection=None
    gpu = resource_request(config)
    try:
        selection=(selector.scheduler_resources(facts,cores,gpu) if queued else
                   selector.gpu_resources(report['preflight'],config['alias'],node,gpu,allow_wait=True) if gpu else
                   selector.select_resources(report['preflight'],config['alias'],node,user_override=cores,user_override_evidence=reason or '用户选择请求核数'))
        blockers.extend(selection.get('blockers', []))
        ppn=selection['requested_cores']
    except ValueError as exc:
        blockers.append(str(exc))
        # Draft capacity is explicit; never used as submission authorization.
        ppn=int(facts.get('CPUS_TOTAL',0))
        if ppn<=0:raise ValueError('节点总核数未知，无法预览')
    scheduler=normalize_scheduler(config['scheduler'])
    queue=next((q for q in report['queues'] if q['name']==config['queue']),None)
    if not queue: blockers.append('未发现所选{}'.format('分区' if scheduler=='slurm' else '队列'))
    elif scheduler=='pbs':
        if queue.get('enabled')!='True' or queue.get('started')!='True':blockers.append('队列未同时 enabled/started')
        maximum=queue.get('resources_max.walltime')
        if maximum and seconds(config['walltime'])>seconds(maximum):blockers.append('walltime 超过队列上限')
    elif queue.get('available') != 'up': blockers.append('Slurm 分区当前不可用')
    elif queue.get('time_limit','').lower() not in ('','infinite','unlimited','n/a') and seconds(config['walltime'])>seconds(queue['time_limit']):
        blockers.append('walltime 超过分区上限')
    if scheduler=='slurm' and facts.get('PARTITIONS') and config['queue'] not in facts['PARTITIONS'].split(','):
        blockers.append('所选节点不属于该 Slurm 分区')
    check_program=not queued and purpose=='vasp' and config.get('runtime_source')!='script'
    if check_program and runtime_config(report['config'])!=runtime_config(config):
        # Only changed runtime paths are checked. Keep the node/queue observation.
        cached=_runtime_cache(config,purpose)
        if cached.exists():runtime=json.loads(cached.read_text(encoding='utf-8'))
        else:
            keys=('mpi_executable','vasp_executable','environment_script') if scheduler=='pbs' else ('vasp_executable',)
            commands=['set -e']
            if scheduler=='slurm':commands+=['source /etc/profile.d/lmod.sh','module load '+shlex.quote(config['module'])]
            for key in keys:
                commands.append('if test '+('-r ' if key=='environment_script' else '-x ')+shlex.quote(config[key])+'; then echo CHECK_'+key+'=OK; else echo CHECK_'+key+'=MISSING; fi')
            receipt=run(['ssh','-o','BatchMode=yes',config['alias'],'\n'.join(commands)])
            runtime={**report,'config':config,'checks':dict(re.findall(r'^CHECK_(\w+)=(\w+)$',receipt['stdout'],re.M)),'diagnostics':receipt,'observed_at':datetime.now(timezone.utc).isoformat()}
            if receipt['exit_code']==0 and all(runtime['checks'].get(k)=='OK' for k in keys):
                cached.parent.mkdir(parents=True,exist_ok=True);cached.write_text(json.dumps(runtime,ensure_ascii=False), encoding='utf-8')
        report={**report,'checks':runtime['checks'],'diagnostics':runtime['diagnostics']}
    required_checks=(('mpi_executable','vasp_executable','environment_script') if scheduler=='pbs' else ('vasp_executable',)) if check_program else ()
    if report['diagnostics']['exit_code']!=0 or any(report['checks'].get(k)!='OK' for k in required_checks):
        blockers.append('运行时路径或调度器诊断未通过')
    if report['availability']['exit_code']!=0: blockers.append('容量诊断异常')
    warnings=list(blockers) if queued else []
    if queued:
        if str(facts.get('CPUS_TOTAL','')).isdigit() and ppn>int(facts['CPUS_TOTAL']):warnings.append('请求 CPU 核数超过缓存中的节点总量；提交由调度器决定')
        if gpu and str(facts.get('GPUS_TOTAL','')).isdigit() and gpu['gpus']>int(facts['GPUS_TOTAL']):warnings.append('请求 GPU 数量超过缓存中的节点总量；提交由调度器决定')
        if facts.get('STATUS')!='FULLY_IDLE':warnings.append('节点当前状态 '+str(facts.get('STATUS') or '未知')+'；按明确请求交由调度器决定')
        if facts.get('GPU_ACTIVE_PROCESSES') not in (None,'','0'):warnings.append('GPU 活动进程 '+str(facts['GPU_ACTIVE_PROCESSES'])+'；直接提交可能等待或遇到已有进程占用')
        if gpu and facts.get('GPUS_TOTAL') in (None,'','unknown'):warnings.append('调度器 GPU 总量未知，按填写的 CPU 与 GPU 数量提交，由调度器决定分配')
    if queued:
        if selection is None:raise ValueError('请求资源无效：'+'；'.join(blockers))
        blockers=[]
    p=profile(config,node,ppn)
    if gpu and selection:
        p.update(resource_mode='gpu', mpi_ranks=gpu['mpi_ranks'], gpus=selection['requested_gpus'],
                 required_gpus=gpu['gpus'], gpu_resource_registered=selection['gpu_resource_registered'],
                 resource_request=gpu, allocation='requested' if queued else 'whole_node')
        if gpu.get('gpu_type'): p['gpu_type'] = gpu['gpu_type']
    renderer=pbs.render_script if scheduler=='pbs' else slurm.render_script
    script='' if config.get('runtime_source')=='script' else renderer(p)
    selected = None
    if purpose=='vasp' or config.get('runtime_source')=='script':
        template_id=selected_template(config)
        if template_id:
            from . import job_templates
            from .execution import template_resources
            selected=job_templates.render_template(template_id,template_resources(p),config['alias'],config)
            blockers.extend(selected.get('execution_blockers',[]))
            script=selected['rendered_text'] or selected['script_text']
        elif config.get('runtime_source')=='script':blockers.append('请先选择当前程序的已导入脚本')
    return {'script':script,'profile':p,'resource_selection':selection,'blockers':blockers,
            'program_adaptation':selected.get('program_adaptation') if selected else None,
            'program_diff':selected.get('program_diff','') if selected else '',
            'script_diff':selected.get('resource_diff','') if selected else '',
            'script_warnings':warnings+(selected.get('warnings',[]) if selected else []),
            'capacity_wait': bool(blockers) and all(str(v).startswith(selector.CAPACITY_BLOCKERS) for v in blockers),
            'node_status':facts.get('STATUS'),
            'resource_ready':not blockers,'submission_enabled':False,'probe_id':probe_id,
            'note':'资源与脚本预览已生成；提交由调度器接收并决定排队，节点诊断仅供参考。' if queued else '资源与脚本预览已生成；派发前会再次检查节点容量。'}


def dispatch_probe(config, purpose='vasp', context=None):
    """Submission uses explicit requests; diagnostics remain a separate user action."""
    if config.get('resource_policy')=='scheduler_queue':
        return {'id':'','config':config,'preflight':{'hosts':[]}}
    return capacity_probe(config,purpose=purpose,context=context)


def submission_probe(config,purpose='vasp'):
    if config.get('resource_policy')=='scheduler_queue':return dispatch_probe(config,purpose)
    return probe(config,purpose=purpose)


def dispatch_probes(configs):
    if all(c.get('resource_policy')!='scheduler_queue' for c in configs):return probe_many(configs)
    return {config_hash(config):dispatch_probe(config) for config in configs}


def preview_for_prepare(config, probe_id, node, cores=None, reason='', purpose='vasp'):
    result=preview(config,probe_id,node,cores,reason,purpose=purpose) if purpose!='vasp' else preview(config,probe_id,node,cores,reason)
    if any('stale' in str(v).lower() or '过期' in str(v) for v in result.get('blockers',[])):
        fresh=capacity_probe(config,purpose=purpose)
        return preview(config,fresh['id'],node,cores,reason,purpose=purpose)
    return result


def scratch_root(alias, purpose):
    from .remote_paths import normalize_workspace_root
    host=load_registry(include_removed=True)['hosts'].get(alias,{})
    return normalize_workspace_root(host.get('remote_workspace_root')).rstrip('/')+'/.fpilot/cache/'+purpose


def excluded_nodes(config):
    return load_registry()['hosts'].get(config['alias'],{}).get('excluded_nodes',[])

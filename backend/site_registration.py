"""Machine-local SSH aliases and runtime paths, without inherited run evidence."""
import glob,json,re,shlex,subprocess
from pathlib import Path
from .storage import ROOT
from .execution_control import write_json

ALIAS = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}')
_RESOLVED = {}


def ssh_config_files():
    """Discover concrete Host names in ordinary Include files; OpenSSH resolves semantics."""
    base=Path.home()/'.ssh';pending=[base/'config'];seen=set();result=[]
    while pending and len(seen)<128:
        path=pending.pop().resolve()
        if path in seen or not path.is_file():continue
        seen.add(path);result.append(path)
        for line in path.read_text(encoding='utf-8',errors='replace').splitlines():
            try:parts=shlex.split(line,comments=True)
            except ValueError:continue
            if parts and parts[0].lower()=='include':
                for pattern in parts[1:]:
                    value=Path(pattern).expanduser()
                    if not value.is_absolute():value=base/value
                    pending.extend(Path(p) for p in glob.glob(str(value)))
    return result


def connection_identity(alias):
    files=ssh_config_files()
    revision=tuple((str(p),p.stat().st_size,p.stat().st_mtime_ns) for p in files)
    key=(str(Path.home()),alias)
    if key not in _RESOLVED or _RESOLVED[key][0]!=revision:
        _RESOLVED[key]=(revision,check_ssh(alias)['resolved'])
    return _RESOLVED[key][1]


def records():
    path=ROOT/'site-registry.json'
    return json.loads(path.read_text(encoding='utf-8')).get('hosts',{}) if path.exists() else {}


def register(value):
    alias=value.get('alias','')
    if not isinstance(alias,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',alias):raise ValueError('请输入 SSH config 中的明确别名，只允许英文、数字、点、横线与下划线')
    if alias not in ssh_aliases():
        resolved=check_ssh(alias)['resolved']
        if resolved.get('hostname')==alias:raise ValueError('SSH 配置中没有该明确别名，请先配置本机 SSH')
    scheduler=str(value.get('scheduler','')).lower()
    if scheduler not in ('pbs','slurm'):raise ValueError('请选择 PBS 或 Slurm')
    queue=value.get('queue','')
    script_mode=value.get('runtime_source','registered')=='script'
    if (queue or not script_mode) and not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',queue):raise ValueError('请填写队列或分区名称')
    for key in ('python','mpi_executable','vasp_executable','environment_script','ncl_executable'):
        item=value.get(key,'')
        if not isinstance(item,str) or any(c in item for c in '\r\n\x00'):raise ValueError('路径包含无效字符：'+key)
        if item and not item.startswith('/'):raise ValueError('服务器路径应为 Linux 绝对路径：'+key)
    if not script_mode and (not value.get('python') or not value.get('vasp_executable')):raise ValueError('请填写服务器已有 Python 和 VASP 程序路径')
    if not script_mode and scheduler=='pbs' and (not value.get('mpi_executable') or not value.get('environment_script')):raise ValueError('PBS 需填写 MPI 与环境脚本路径')
    module=value.get('module','')
    if not script_mode and scheduler=='slurm' and not re.fullmatch(r'[A-Za-z0-9_.+-]+/[A-Za-z0-9_.+-]+',module):raise ValueError('Slurm 需填写 module/version')
    from .remote_paths import normalize_workspace_root
    remote_root=normalize_workspace_root(value.get('remote_workspace_root'))
    runtimes=[]
    for variant,key in (('vasp_std','vasp_executable'),('vasp_ncl','ncl_executable')):
        if value.get(key):runtimes.append({'variant':variant,'status':'USER_CONFIGURED','executable':value[key],'mpi':value.get('mpi_executable',''),
            'environment_script':value.get('environment_script',''),'module':module,'launcher':'mpirun','environment':{}})
    host={'status':'USER_CONFIGURED','scheduler':scheduler,'queue':queue,'partition':queue,'runtimes':runtimes,
        'python':{'known_interpreter':value.get('python','')},'remote_workspace_root':remote_root,
        'registration_evidence':'USER_CONFIGURATION_ONLY','program_validation':'NOT_PERFORMED'}
    if script_mode:host['runtime_source']='script'
    from . import servers
    current=servers.load_registry()['hosts'].get(alias)
    if current and current.get('status')!='USER_CONFIGURED':raise ValueError('内置服务器原记录保留；请使用另一个 SSH 别名，或在运行时设置中修改本次配置')
    path=ROOT/'site-registry.json';previous=path.read_bytes() if path.exists() else None
    sites=records();sites[alias]=host
    write_json(path,{'schema':'fpilot.site-registry.v1','hosts':sites})
    try:
        config=servers.select_profile(alias);config['remote_workspace_root']=remote_root
        servers.save_config(config)
    except Exception:
        if previous is None:path.unlink(missing_ok=True)
        else:write_json(path,json.loads(previous))
        raise
    return {'alias':alias,'config':config,'registered_profiles':servers.registered_profiles(),'evidence':'USER_CONFIGURATION_ONLY'}


def ssh_aliases():
    aliases=[]
    for config in ssh_config_files():
        for line in config.read_text(encoding='utf-8',errors='replace').splitlines():
            try:parts=shlex.split(line,comments=True)
            except ValueError:continue
            if parts and parts[0].lower()=='host':aliases += [v for v in parts[1:] if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',v)]
    return sorted(set(aliases))


def check_ssh(alias):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',str(alias)):raise ValueError('SSH 别名无效')
    argv=['ssh','-G',alias]
    path=Path.home()/'.ssh/config'
    if path.is_file():argv[1:1]=['-F',str(path)]
    try:result=subprocess.run(argv,capture_output=True,text=True,timeout=10)
    except (OSError,subprocess.TimeoutExpired) as exc:raise ValueError('无法读取 OpenSSH 配置，请启用 Windows OpenSSH Client 并确认 ssh 在 PATH 中') from exc
    if result.returncode:raise ValueError('SSH 配置解析失败：'+result.stderr[-500:])
    fields={}
    for line in result.stdout.splitlines():
        parts=line.split(None,1)
        if len(parts)==2 and parts[0] in ('hostname','user','port','proxyjump','proxycommand','identityfile'):
            fields[parts[0]]=parts[1] if parts[0] not in fields else fields[parts[0]]+';'+parts[1]
    return {'alias':alias,'resolved':fields,'evidence':'LOCAL_CONFIG_ONLY','note':'本机配置已解析，未连接服务器。首次连接请在终端执行 ssh '+alias+' exit，核对主机指纹并完成认证。'}


def test_connection(alias, python='', remote_workspace_root=None):
    resolved=check_ssh(alias)
    if python and (not isinstance(python,str) or not python.startswith('/') or any(c in python for c in '\r\n\x00')):
        raise ValueError('服务器 Python 应为单行 Linux 绝对路径')
    candidates=[python] if python else ['python3','python']
    code='import sys,json,base64,concurrent.futures,subprocess,signal; print("FPILOT_PYTHON="+json.dumps({"path":sys.executable,"version":list(sys.version_info[:3]),"supported":sys.version_info >= (3,8)}))'
    command="printf 'FPILOT_SSH_OK\\n'\n"
    for name in ('qsub','qstat','sbatch','squeue','sacct','sinfo'):
        command+='printf '+shlex.quote('FPILOT_TOOL_'+name+'=%s\n')+' "$(command -v '+name+' || true)"\n'
    for candidate in candidates:
        command+=shlex.quote(candidate)+' -c '+shlex.quote(code)+' 2>/dev/null || true\n'
    if remote_workspace_root:
        from .remote_paths import workspace_probe_command
        command+='(\n'+workspace_probe_command(remote_workspace_root)+"\n) && printf 'FPILOT_ROOT_READY=1\\n' || printf 'FPILOT_ROOT_READY=0\\n'\n"
    try:
        result=subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','ControlMaster=no','-o','ControlPath=none',alias,command],capture_output=True,text=True,timeout=25)
    except subprocess.TimeoutExpired as exc:raise ValueError('连接超时；请检查 VPN/校园网、服务器地址与端口') from exc
    except OSError as exc:raise ValueError('无法启动系统 OpenSSH Client') from exc
    if result.returncode or not re.search(r'^FPILOT_SSH_OK$',result.stdout,re.M):
        error=result.stderr[-1200:]
        if 'Host key verification failed' in error:note='首次主机指纹尚未确认，请先在终端执行 ssh '+alias+' exit'
        elif 'Permission denied' in error:note='认证失败，请检查公钥安装、私钥或 ssh-agent；软件进程需能免交互连接'
        else:note='连接未通过，请检查网络、端口和 SSH 配置'
        raise ValueError(note+'。'+error[-500:])
    interpreters=[]
    for line in result.stdout.splitlines():
        if line.startswith('FPILOT_PYTHON='):
            try:interpreters.append(json.loads(line.split('=',1)[1]))
            except ValueError:continue
    interpreter=next((p for p in interpreters if p.get('supported') and str(p.get('path','')).startswith('/')),None)
    tools=dict(re.findall(r'^FPILOT_TOOL_(\w+)=(.*)$',result.stdout,re.M))
    schedulers=[]
    if tools.get('qsub') and tools.get('qstat'):schedulers.append('pbs')
    if tools.get('sbatch') and tools.get('sinfo'):schedulers.append('slurm')
    workspace=None
    if remote_workspace_root:
        from .remote_paths import parse_workspace_probe
        if re.search(r'^FPILOT_ROOT_READY=1$',result.stdout,re.M):
            workspace={'ready':True,**parse_workspace_probe({'exit_code':0,'stdout':result.stdout},remote_workspace_root)}
        else:workspace={'ready':False,'note':'工作区路径或最近存在父目录不可用/不可写，请检查目录与权限；尚未创建目录。'}
    return {**resolved,'evidence':'SSH_CONNECTION_ONLY','connected':True,'python':interpreter,
            'tools':tools,'schedulers':schedulers,'workspace':workspace,'remote_mutation':False,
            'note':'软件可免交互连接；'+('已发现控制通道 Python，未执行计算程序。' if interpreter else '未发现可用的 Python 3.8+；请展开高级设置填写服务器已有解释器。')}

"""Machine-local SSH aliases and runtime paths, without inherited run evidence."""
import json,re,shlex,subprocess
from pathlib import Path
from .storage import ROOT
from .execution_control import write_json


def records():
    path=ROOT/'site-registry.json'
    return json.loads(path.read_text(encoding='utf-8')).get('hosts',{}) if path.exists() else {}


def register(value):
    alias=value.get('alias','')
    if not isinstance(alias,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',alias):raise ValueError('请输入 SSH config 中的明确别名，只允许英文、数字、点、横线与下划线')
    if alias not in ssh_aliases():raise ValueError('SSH 配置中没有该明确别名，请先配置本机 SSH')
    scheduler=str(value.get('scheduler','')).lower()
    if scheduler not in ('pbs','slurm'):raise ValueError('请选择 PBS 或 Slurm')
    queue=value.get('queue','')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',queue):raise ValueError('请填写队列或分区名称')
    for key in ('python','mpi_executable','vasp_executable','environment_script','ncl_executable'):
        item=value.get(key,'')
        if not isinstance(item,str) or any(c in item for c in '\r\n\x00'):raise ValueError('路径包含无效字符：'+key)
        if item and not item.startswith('/'):raise ValueError('服务器路径应为 Linux 绝对路径：'+key)
    if not value.get('python') or not value.get('vasp_executable'):raise ValueError('请填写服务器已有 Python 和 VASP 程序路径')
    if scheduler=='pbs' and (not value.get('mpi_executable') or not value.get('environment_script')):raise ValueError('PBS 需填写 MPI 与环境脚本路径')
    module=value.get('module','')
    if scheduler=='slurm' and not re.fullmatch(r'[A-Za-z0-9_.+-]+/[A-Za-z0-9_.+-]+',module):raise ValueError('Slurm 需填写 module/version')
    from .remote_paths import normalize_workspace_root
    remote_root=normalize_workspace_root(value.get('remote_workspace_root'))
    runtimes=[]
    for variant,key in (('vasp_std','vasp_executable'),('vasp_ncl','ncl_executable')):
        if value.get(key):runtimes.append({'variant':variant,'status':'USER_CONFIGURED','executable':value[key],'mpi':value.get('mpi_executable',''),
            'environment_script':value.get('environment_script',''),'module':module,'launcher':'mpirun','environment':{}})
    host={'status':'USER_CONFIGURED','scheduler':scheduler,'queue':queue,'partition':queue,'runtimes':runtimes,
        'python':{'known_interpreter':value['python']},'remote_workspace_root':remote_root,
        'registration_evidence':'USER_CONFIGURATION_ONLY','program_validation':'NOT_PERFORMED'}
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
    config=Path.home()/'.ssh/config';aliases=[]
    if config.is_file():
        for line in config.read_text(encoding='utf-8',errors='replace').splitlines():
            try:parts=shlex.split(line,comments=True)
            except ValueError:continue
            if parts and parts[0].lower()=='host':aliases += [v for v in parts[1:] if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',v)]
    return sorted(set(aliases))


def check_ssh(alias):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',str(alias)):raise ValueError('SSH 别名无效')
    try:result=subprocess.run(['ssh','-G',alias],capture_output=True,text=True,timeout=10)
    except (OSError,subprocess.TimeoutExpired) as exc:raise ValueError('无法读取 OpenSSH 配置，请启用 Windows OpenSSH Client 并确认 ssh 在 PATH 中') from exc
    if result.returncode:raise ValueError('SSH 配置解析失败：'+result.stderr[-500:])
    fields={}
    for line in result.stdout.splitlines():
        parts=line.split(None,1)
        if len(parts)==2 and parts[0] in ('hostname','user','port'):fields[parts[0]]=parts[1]
    return {'alias':alias,'resolved':fields,'evidence':'LOCAL_CONFIG_ONLY','note':'本机配置已解析，未连接服务器。首次连接请在终端执行 ssh '+alias+' exit，核对主机指纹并完成认证。'}

"""Interactive first connection. OpenSSH owns all password and MFA prompts."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

from . import site_registration as sites
from .platform_runtime import exclusive_lock


def endpoint(data):
    if not isinstance(data, dict) or set(data) != {'host', 'user', 'port'}:
        raise ValueError('请填写地址、用户名和端口')
    host, user, port = (data[k] for k in ('host','user','port'))
    if not all(isinstance(v,str) for v in (host,user,port)):
        raise ValueError('服务器连接字段格式无效')
    if not re.fullmatch(r'[A-Za-z0-9:][A-Za-z0-9.:-]{0,252}',host) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@-]{0,127}',user):
        raise ValueError('地址或用户名无效')
    if not re.fullmatch(r'[0-9]{1,5}',port) or not 1 <= int(port) <= 65535:
        raise ValueError('端口须为 1–65535')
    return host,user,int(port)


def profile(data):
    if not isinstance(data,dict) or set(data)-{'target','jump','alias'}:
        raise ValueError('服务器连接字段无效')
    alias=data.get('alias','')
    if not isinstance(alias,str) or not sites.ALIAS.fullmatch(alias) or len(alias)>52:
        raise ValueError('连接名称限 1–52 位英文、数字、点、横线或下划线')
    target=endpoint(data.get('target'))
    jump=data.get('jump')
    sections=[]
    if isinstance(jump,str):
        if not sites.ALIAS.fullmatch(jump) or jump==alias or jump not in sites.ssh_aliases():
            raise ValueError('请选择已有跳板连接')
        through=jump
    elif jump is not None:
        through=alias+'-jump'
        sections.append((through,endpoint(jump)))
    else:
        through=None
    sections.append((alias,target))
    lines=[]
    for name,(host,user,port) in sections:
        lines.extend([f'Host {name}',f'    HostName {host}',f'    User {user}',f'    Port {port}',
                      f'    IdentityFile ~/.ssh/id_ed25519_fpilot_{name}', '    IdentitiesOnly yes'])
        if through and name==alias:lines.append('    ProxyJump '+through)
    return alias,sections,'\n'.join(lines)+'\n'


def save_config(data):
    alias,sections,body=profile(data)
    root=Path.home()/'.ssh'
    root.mkdir(mode=0o700,exist_ok=True)
    path=root/'config'
    marker=f'# fpilot connection {alias}\n'.encode()
    block=marker+body.encode()+b'Host *\n'
    with exclusive_lock(root/'.fpilot-config.lock') as locked:
        if not locked:raise ValueError('另一项连接配置正在保存')
        if path.is_symlink():raise ValueError('SSH 配置由符号链接管理，请在现有配置中添加别名')
        old=path.read_bytes() if path.exists() else b''
        if block in old:return sections
        aliases={a.lower() for a in sites.ssh_aliases()}
        if marker in old or any(name.lower() in aliases for name,_ in sections):
            raise ValueError('连接名称已存在，请直接使用已有连接或换一个名称')
        with tempfile.NamedTemporaryFile(dir=root,delete=False) as f:
            tmp=Path(f.name)
            f.write(block+old)
        try:
            if old:
                with tempfile.NamedTemporaryFile(prefix='config.before-fpilot-',dir=root,delete=False) as f:f.write(old)
            if (path.read_bytes() if path.exists() else b'')!=old:raise ValueError('SSH 配置已变化，请重试')
            os.replace(tmp,path)
        finally:tmp.unlink(missing_ok=True)
    return sections


def reauth_alias(data):
    if isinstance(data,dict) and set(data)=={'reauth'}:
        alias=data['reauth']
        if not isinstance(alias,str) or alias not in sites.ssh_aliases() or not sites.ALIAS.fullmatch(alias):
            raise ValueError('请选择本机已有的 SSH 别名')
        return alias
    return None


def native_command(data):
    if not reauth_alias(data):
        profile(data)
    # Only validated address metadata crosses into the terminal, never secrets.
    argv=[sys.executable,'-m','backend.ssh_onboarding',json.dumps(data,separators=(',',':'))]
    cwd=Path(__file__).resolve().parents[1]
    if os.name=='nt':
        subprocess.Popen(argv,cwd=cwd,creationflags=subprocess.CREATE_NEW_CONSOLE)
    elif sys.platform=='darwin':
        command='cd '+shlex.quote(str(cwd))+' && '+shlex.join(argv)
        subprocess.run(['osascript','-e','on run argv','-e','tell application "Terminal"',
                        '-e','activate','-e','do script (item 1 of argv)','-e','end tell','-e','end run',command],
                       check=True,timeout=15,capture_output=True)
    else:raise ValueError('首次认证助手支持 macOS 和 Windows，其他平台请使用已有 SSH 连接')
    return {'alias':data.get('alias') or data['reauth']}


def interactive(data):
    if os.environ.get('FPILOT_REMOTE_DISABLED')=='1':raise ValueError('当前工作区已禁用远端连接')
    existing=reauth_alias(data)
    if existing:
        from .platform_runtime import ssh_options
        print('重新认证 '+existing+'。完成后返回工作台检查原连接。',flush=True)
        subprocess.run(['ssh',*ssh_options(existing),'-o','StrictHostKeyChecking=ask',existing,'true'],check=True)
        return
    alias,sections,_=profile(data)
    for name,(host,user,port) in sections:print(f'{name} → {user}@{host}:{port}')
    if input('保存以上连接到本机 SSH 配置并登录？[y/N] ').lower()!='y':return
    sections=save_config(data)
    for name,_ in sections:
        print('正在连接'+('计算服务器 ' if name==alias else '跳板机 ')+name,flush=True)
        fresh=['ssh','-S','none','-o','ControlMaster=no','-o','ConnectTimeout=12','-o','StrictHostKeyChecking=ask']
        if subprocess.run(['ssh','-o','StrictHostKeyChecking=yes',*fresh[1:],'-o','BatchMode=yes',name,'true'],stdin=subprocess.DEVNULL).returncode==0:
            continue
        login=subprocess.run(fresh+[name,'true'])
        if login.returncode and name!=alias:
            if input('跳板机可能仅允许转发。直接验证目标服务器？[y/N] ').lower()=='y':
                continue
        login.check_returncode()
        if input('已登录。允许这台电脑以后使用专用公钥连接？[y/N] ').lower()!='y':return
        path=Path.home()/'.ssh'/('id_ed25519_fpilot_'+name)
        pub=Path(str(path)+'.pub')
        if not path.exists() and not pub.exists():
            subprocess.run(['ssh-keygen','-t','ed25519','-f',str(path),'-C','fpilot-'+name],check=True)
        if not path.is_file() or not pub.is_file():raise ValueError('专用密钥不完整，请先核对本机文件')
        key=pub.read_text().strip()
        if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]*)?',key):raise ValueError('公钥格式无效')
        print('如需通过学校门户登记，请复制此公钥：\n'+key,flush=True)
        if input('现在把公钥追加到该账号？[y/N] ').lower()!='y':return
        # Send only a public key over an authenticated SSH session.
        install='set -eu; umask 077; mkdir -p "$HOME/.ssh"; cd "$HOME/.ssh"; touch authorized_keys; IFS= read -r key; grep -Fqx -- "$key" authorized_keys || printf "\\n%s\\n" "$key" >> authorized_keys'
        subprocess.run(fresh+['-T',name,install],input=(key+'\n').encode(),check=True)
        if shutil.which('ssh-add') and input('将专用密钥载入本机 ssh-agent？带口令的密钥建议选 y。[y/N] ').lower()=='y':
            subprocess.run(['ssh-add',str(path)])
    print('返回工作台，点击「验证并继续」检查后台连接。')


if __name__=='__main__':
    try:interactive(json.loads(sys.argv[1]))
    except (ValueError,OSError,subprocess.SubprocessError,KeyboardInterrupt) as exc:print('首次连接未完成：',exc)
    finally:
        if os.name=='nt':input('按回车关闭窗口')

import {useEffect,useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
import type {ServerConfig,RegisteredProfile} from './executionUi';

const initial={alias:'',scheduler:'pbs',queue:'',python:'',mpi_executable:'',vasp_executable:'',ncl_executable:'',environment_script:'',module:'',remote_workspace_root:'$HOME/fpilot',runtime_source:'script'};
type Connection={resolved:Record<string,string>;python?:{path:string;version:number[]};schedulers?:string[];note:string;connected?:boolean;workspace?:{ready:boolean;absolute_path?:string;existing_parent?:string;note?:string}};

export default function SiteRegistration({onRegistered}:{onRegistered:(config:ServerConfig,profiles:RegisteredProfile[])=>void}){
  const [form,setForm]=useState(initial),[aliases,setAliases]=useState<string[]>([]),[message,setMessage]=useState(''),[busy,setBusy]=useState(false),[connection,setConnection]=useState<Connection|null>(null);
  const [newHost,setNewHost]=useState({alias:'my-cluster',host:'',user:'',port:'22'});
  useEffect(()=>{void requestJson('/api/sites').then(v=>setAliases(v.ssh_aliases)).catch(e=>setMessage(errorMessage(e)));},[]);
  const field=(key:keyof typeof initial,value:string)=>setForm(old=>({...old,[key]:value}));
  async function run(action:'parse'|'connect'|'register'){
    setBusy(true);setMessage('');
    try{
      let value=form;
      if(action==='parse'){
        const result=await requestJson('/api/sites/check-ssh',{alias:form.alias});setConnection(result);setMessage(result.note);return;
      }
      if(action==='connect'||action==='register'&&form.runtime_source==='script'&&!form.python){
        const result:Connection=await requestJson('/api/sites/test-connection',{alias:form.alias,python:form.python,remote_workspace_root:form.remote_workspace_root});
        setConnection(result);value={...form,python:result.python?.path||form.python};
        if(result.schedulers?.length===1&&result.schedulers[0]!==form.scheduler){setForm(value);setMessage(result.note+' 检测到 '+result.schedulers[0]+'；请核对并修改调度器后再登记。');return;}
        setForm(value);setMessage(result.note);
        if(action==='connect')return;
      }
      const result=await requestJson('/api/sites',value);
      onRegistered(result.config,result.registered_profiles);window.dispatchEvent(new Event('fpilot:settings-changed'));
      setMessage(value.python?'服务器已登记。下一步在服务器详情中导入可用脚本，按程序分类保存；提交页可以直接选择。':'服务器基本信息已保存；还需填写可用的服务器 Python，才能进行节点查询。');
    }catch(e){setMessage(errorMessage(e));}finally{setBusy(false);}
  }
  const isWindows=navigator.userAgent.includes('Windows');
  const validHost=/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(newHost.alias)&&/^[A-Za-z0-9_.:-]+$/.test(newHost.host)&&/^[A-Za-z_][A-Za-z0-9_.-]*$/.test(newHost.user)&&/^[0-9]+$/.test(newHost.port)&&Number(newHost.port)>=1&&Number(newHost.port)<=65535;
  const snippet=`Host ${newHost.alias}\n    HostName ${newHost.host}\n    User ${newHost.user}\n    Port ${newHost.port}\n    IdentityFile ~/.ssh/id_ed25519_fpilot\n    IdentitiesOnly yes\n    AddKeysToAgent yes\n${navigator.userAgent.includes('Mac')?'    UseKeychain yes\n':''}    ServerAliveInterval 60\n`;
  return <section className="input-submit-card site-registration"><h3>添加服务器</h3>
    <p>已有 SSH 别名和能在该服务器运行的任务脚本即可。环境脚本、MPI 和程序位置沿用导入脚本。</p>
    <fieldset disabled={busy}>
      <h4>1 · 连接服务器</h4>
      <label>本机 SSH 别名<input list="fpilot-ssh-aliases" value={form.alias} onChange={e=>{field('alias',e.target.value);setConnection(null);}}/></label>
      <datalist id="fpilot-ssh-aliases">{aliases.map(a=><option key={a} value={a}/>)}</datalist>
      <div className="button-row"><button className="button" disabled={!form.alias} onClick={()=>void run('parse')}>读取 SSH 配置</button><button className="button" disabled={!form.alias} onClick={()=>void run('connect')}>测试免交互连接与服务器工具</button></div>
      {connection&&<p>{connection.connected?'软件连接已通过':'配置已解析，尚未连接'} · {connection.resolved.user}@{connection.resolved.hostname}:{connection.resolved.port}{connection.python&&` · Python ${connection.python.version.join('.')}：${connection.python.path}`}</p>}
      {connection?.workspace&&<p>{connection.workspace.ready?`工作区检查通过：${connection.workspace.absolute_path}；已有父目录 ${connection.workspace.existing_parent}。尚未创建目录。`:connection.workspace.note}</p>}
      <details><summary>还没有 SSH 别名：配置与密钥操作指引</summary>
        <p>使用本机已有密钥时，可在下面配置中改为它的路径。首次连接需在终端核对服务器指纹；软件不保存密码或私钥。</p>
        <div className="submit-grid">{([['alias','SSH 别名'],['host','服务器地址'],['user','用户名'],['port','端口']] as const).map(([key,label])=><label key={key}>{label}<input value={newHost[key]} onChange={e=>setNewHost({...newHost,[key]:e.target.value})}/></label>)}</div>
        {validHost&&<><p>把以下片段追加到 {isWindows?'%USERPROFILE%\\.ssh\\config':'~/.ssh/config'}：</p><pre className="input-text">{snippet}</pre><button className="button" onClick={()=>void navigator.clipboard.writeText(snippet).then(()=>setMessage('SSH 配置片段已复制，请追加到本机 config，保留原有条目。'))}>复制配置片段</button>
          <ol><li>需要新密钥时，在终端用独立文件名创建，已有文件请复用：<code>ssh-keygen -t ed25519 -f {isWindows?'"$env:USERPROFILE\\.ssh\\id_ed25519_fpilot"':'~/.ssh/id_ed25519_fpilot'}</code>。文件已存在时不要确认覆盖。</li>
            <li>将对应 <code>id_ed25519_fpilot.pub</code> 公钥追加到服务器账号的 <code>~/.ssh/authorized_keys</code>，或请管理员安装公钥；私钥留在本机。</li>
            <li>在终端运行 <code>ssh {newHost.alias} exit</code>，核对主机指纹并完成认证。带口令的密钥需通过 <code>ssh-add</code> 加入系统 agent；Windows 需启动 OpenSSH Authentication Agent 服务。</li>
            <li>回到上方填写别名并测试软件免交互连接；校园服务器通常还需要先连接 VPN。</li></ol>
        </>}
      </details>
      <h4>2 · 登记与选择运行方式</h4>
      <div className="submit-grid"><label>调度器<select value={form.scheduler} onChange={e=>field('scheduler',e.target.value)}><option value="pbs">PBS / Torque</option><option value="slurm">Slurm</option></select></label>
        <label>运行方式<select value={form.runtime_source} onChange={e=>field('runtime_source',e.target.value)}><option value="script">导入可用任务脚本（推荐）</option><option value="registered">高级：填写程序和环境路径</option></select></label>
        <label>{form.scheduler==='pbs'?'队列':'分区'}{form.runtime_source==='script'?'（可由脚本填写）':''}<input value={form.queue} onChange={e=>field('queue',e.target.value)}/></label>
        <label>服务器工作区根目录<input value={form.remote_workspace_root} onChange={e=>field('remote_workspace_root',e.target.value)}/></label></div>
      <p>任务将保存在此根目录内的 <code>material-projects/项目目录名/</code>。支持 $HOME/fpilot、包含 fpilot 的专用绝对目录及原登记工作区。原有服务器使用自己的已保存目录。</p>
      <details open={form.runtime_source==='registered'||Boolean(connection?.connected&&!connection.python)}><summary>高级路径与服务器 Python</summary>
        <p>连接测试会发现服务器已有 Python 3.8+，供软件查询与传输使用；这与任务脚本中的计算环境分别配置。</p>
        <label>服务器已有 Python 绝对路径<input value={form.python} onChange={e=>field('python',e.target.value)} placeholder="自动发现；找不到时填写"/></label>
        {form.runtime_source==='registered'&&([['vasp_executable','VASP 常规程序绝对路径'],['ncl_executable','VASP SOC 程序绝对路径（可选）'],...(form.scheduler==='pbs'?[['mpi_executable','MPI 启动器绝对路径'],['environment_script','环境脚本绝对路径']]:[['module','module/version']])] as [keyof typeof initial,string][]).map(([key,label])=><label key={key}>{label}<input value={form[key]} onChange={e=>field(key,e.target.value)}/></label>)}
      </details>
      <p>保存连接后进入服务器详情，再导入这台服务器的任务脚本。连接测试不提交计算任务。</p>
      <button className="button primary" disabled={!form.alias} onClick={()=>void run('register')}>登记并使用</button>
    </fieldset>
    {busy&&<p role="status">正在检查服务器配置…</p>}{message&&<p role="status">{message}</p>}
  </section>;
}

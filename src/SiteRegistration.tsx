import NewSshConnection from './NewSshConnection';
import DisclosureLabel from './DisclosureLabel';
import {useEffect,useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
import type {ServerConfig,RegisteredProfile} from './executionUi';

const initial={alias:'',scheduler:'pbs',resource_policy:'scheduler_queue',command_mode:'opaque',queue:'',python:'',mpi_executable:'',vasp_executable:'',ncl_executable:'',environment_script:'',module:'',remote_workspace_root:'$HOME/fpilot',runtime_source:'script',pyw90_python:'',pyw90_package:''};
type Connection={resolved:Record<string,string>;python?:{path:string;version:number[]};schedulers?:string[];note:string;connected?:boolean;workspace?:{ready:boolean;absolute_path?:string;existing_parent?:string;note?:string}};

export default function SiteRegistration({onRegistered}:{onRegistered:(config:ServerConfig,profiles:RegisteredProfile[])=>void}){
  const [form,setForm]=useState(initial),[aliases,setAliases]=useState<string[]>([]),[message,setMessage]=useState(''),[busy,setBusy]=useState(false),[connection,setConnection]=useState<Connection|null>(null);
  const [temporary,setTemporary]=useState(false),[offerReauth,setOfferReauth]=useState(false);
  const [mode,setMode]=useState<'existing'|'new'>('existing');
  useEffect(()=>{void requestJson('/api/sites').then(v=>{setAliases(v.ssh_aliases);if(!v.ssh_aliases.length)setMode("new");if(v.ssh_aliases.length===1)setForm(old=>old.alias?old:{...old,alias:v.ssh_aliases[0]});}).catch(e=>setMessage(errorMessage(e)));},[]);
  useEffect(()=>{void requestJson('/api/workbench-settings').then(v=>setForm(old=>({...old,remote_workspace_root:v.workspace_root||'$HOME/fpilot'}))).catch(()=>{});},[]);
  const field=(key:keyof typeof initial,value:string)=>setForm(old=>({...old,[key]:value}));
  async function run(action:'parse'|'connect'|'register'|'session'|'reauth'){
    setBusy(true);setMessage('');
    try{
      let value=form;
      if(action==='reauth'){
        await requestJson('/api/sites/first-login',{reauth:form.alias});setMessage('完成认证后点击「检查当前会话」。');return;
      }
      if(action==='parse'){
        const result=await requestJson('/api/sites/check-ssh',{alias:form.alias});setConnection(result);setMessage(result.note);return;
      }
      if(action==='connect'||action==='session'||action==='register'&&(!connection?.workspace?.ready||form.python!==(connection?.python?.path||''))){
        const result:Connection=await requestJson('/api/sites/test-connection',{alias:form.alias,python:form.python,remote_workspace_root:form.remote_workspace_root,fresh:action==='connect'||action==='register'&&!temporary});
        setConnection(result);value={...form,python:result.python?.path||form.python};
        if(action!=='register'&&result.schedulers?.length===1)value={...value,scheduler:result.schedulers[0]};
        setForm(value);setMessage(result.note);
        if(action==='connect'||action==='session'){setTemporary(action==='session');setOfferReauth(false);return;}
        if(!result.workspace?.ready)throw new Error('工作目录未通过检查，请修改后再保存。');
      }
      const result=await requestJson('/api/sites',value);
      onRegistered(result.config,result.registered_profiles);window.dispatchEvent(new Event('fpilot:settings-changed'));
      setMessage(value.python?'服务器已登记。下一步在服务器详情中导入可用脚本，用熟悉的名称保存；提交页可以直接选择。':'服务器基本信息已保存。请在服务器详情中核对可用的远端 Python。');
    }catch(e){if(action==='connect'||action==='session'){setConnection(null);setOfferReauth(true);}setMessage(errorMessage(e));}finally{setBusy(false);}
  }
  return <section className="input-submit-card site-registration"><h3>添加服务器</h3>
    <fieldset disabled={busy}>
      <div className="button-row" role="group" aria-label="连接方式">{(['existing','new'] as const).map(m=><button className="button" key={m} aria-pressed={mode===m} onClick={()=>{setMode(m);setConnection(null);setOfferReauth(false);setTemporary(false);setMessage('');}}>{m==='existing'?'使用已有 SSH 连接':'添加新连接'}</button>)}</div>
      {mode==='new'&&<NewSshConnection aliases={aliases} onStarted={alias=>{setForm({...form,alias,python:''});setConnection(null);setMode('existing');setAliases(old=>Array.from(new Set([...old,alias])));setMessage('完成终端中的指纹核对与认证后，点击验证并继续。');}}/>}
      {mode==='existing'&&<>
      <label className="workbench-field-line"><span className="field-line-name">本机 SSH 别名</span><input list="fpilot-ssh-aliases" value={form.alias} onChange={e=>{setForm({...form,alias:e.target.value,python:''});setConnection(null);setOfferReauth(false);setTemporary(false);}}/></label>
      <datalist id="fpilot-ssh-aliases">{aliases.map(a=><option key={a} value={a}/>)}</datalist>
      <div className="button-row"><button className="button" disabled={!form.alias} onClick={()=>void run('parse')}>查看连接信息</button><button className="button" disabled={!form.alias} onClick={()=>void run('connect')}>验证并继续</button></div>
      {offerReauth&&<div><p>如学校要求动态码，可重新认证，再检查现有会话。会话失效后需要再次验证。</p><button className="button" onClick={()=>void run('reauth')}>重新认证</button><button className="text-button" onClick={()=>void run('session')}>检查当前会话</button></div>}
      {connection&&<p>{connection.connected?'软件连接已通过':'配置已解析，尚未连接'} · {connection.resolved.user}@{connection.resolved.hostname}:{connection.resolved.port}{connection.python&&` · Python ${connection.python.version.join('.')}：${connection.python.path}`}</p>}
      {connection?.workspace&&<p>{connection.workspace.ready?`工作区检查通过：${connection.workspace.absolute_path}；已有父目录 ${connection.workspace.existing_parent}。尚未创建目录。`:connection.workspace.note}</p>}
      </>}
      {mode==='existing'&&connection?.connected&&<><h4>工作目录与作业脚本</h4>
      <label>服务器工作目录<input value={form.remote_workspace_root} onChange={e=>{field('remote_workspace_root',e.target.value);setConnection(old=>old?{...old,workspace:undefined}:null);}}/></label>
      <p>{temporary?'当前会话可用，失效后需重新认证':'已验证新的后台连接'} · {connection.python?'基础 Python 已发现':'基础 Python 待配置，部分任务查询和传输暂不可用'}。后处理依赖可在首次分析时检查。</p>
      <details open={connection.schedulers?.length!==1||undefined}><DisclosureLabel>调度器与运行方式 · {form.scheduler.toUpperCase()}</DisclosureLabel>
      <div className="submit-grid"><label>执行方式<select value={form.scheduler==='custom'?form.command_mode:form.scheduler} onChange={e=>{
        const value=e.target.value;
        setForm(value==='direct'||value==='opaque'?{...form,scheduler:'custom',command_mode:value,runtime_source:'script'}:{...form,scheduler:value});
      }}><option value="pbs">PBS / Torque</option><option value="slurm">Slurm</option><option value="direct">无调度器 · 直接运行</option><option value="opaque">其它 · 自定义命令</option></select></label>
        {form.scheduler!=='custom'&&<label>默认提交方式<select value={form.resource_policy} onChange={e=>field('resource_policy',e.target.value)}><option value="scheduler_queue">交给调度器排队</option><option value="wait_idle">等待节点空闲后提交</option></select></label>}
        {form.scheduler!=='custom'&&<label>运行方式<select value={form.runtime_source} onChange={e=>field('runtime_source',e.target.value)}><option value="script">导入可用任务脚本（推荐）</option><option value="registered">高级：填写程序和环境路径</option></select></label>}
        {form.scheduler!=='custom'&&<label>{form.scheduler==='pbs'?'队列':'分区'}{form.runtime_source==='script'?'（可由脚本填写）':''}<input value={form.queue} onChange={e=>field('queue',e.target.value)}/></label>}
        </div></details>
      <p>填写你有读写权限的 Linux 绝对目录，不限制目录名。项目也可另外指定绝对目录；已有任务保持原路径。</p>
      <details open={form.runtime_source==='registered'||undefined}><DisclosureLabel>高级路径与服务器 Python</DisclosureLabel>
        <p>连接测试会发现服务器已有 Python 3.8+，供软件查询与传输使用；这与任务脚本中的计算环境分别配置。</p>
        <label className="workbench-field-line"><span className="field-line-name">服务器已有 Python 绝对路径</span><input value={form.python} onChange={e=>field('python',e.target.value)} placeholder="自动发现；找不到时填写"/></label>
        {form.runtime_source==='registered'&&([['vasp_executable','VASP 常规程序绝对路径'],['ncl_executable','VASP SOC 程序绝对路径（可选）'],...(form.scheduler==='pbs'?[['mpi_executable','MPI 启动器绝对路径'],['environment_script','环境脚本绝对路径']]:[['module','module/version']])] as [keyof typeof initial,string][]).map(([key,label])=><label className="workbench-field-line" key={key}><span className="field-line-name">{label}</span><input value={form[key]} onChange={e=>field(key,e.target.value)}/></label>)}
      </details>
      <p>保存连接后进入服务器详情，再导入任务脚本或保存自定义命令。连接测试不提交计算任务。</p>
      <button className="button primary" disabled={!form.alias} onClick={()=>void run('register')}>保存并继续配置脚本</button></>}
    </fieldset>
    {busy&&<p role="status">正在检查服务器配置…</p>}{message&&<p role="status">{message}</p>}
  </section>;
}

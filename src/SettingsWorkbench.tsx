import Feedback from './Feedback';
import {useDialogFocus} from './useDialogFocus';
import {useEffect,useRef,useState} from 'react';
import type {ReactNode} from 'react';
import {settingsSections} from './navigationHistory';
import {settingsDrafts} from './submissionDrafts';
import CommonWorkspacePath from './CommonWorkspacePath';
import AppearancePanel from './AppearancePanel';
import RemotePerformance from './RemotePerformance';
import LocalSetup from './LocalSetup';
import SiteRegistration from './SiteRegistration';
import ServerRegistrationActions from './ServerRegistrationActions';
import ExtensionManager from './ExtensionManager';
import JobTemplatePanel from './JobTemplatePanel';
import CommandModeSettings from './CommandModeSettings';
import ExecutionControl from './ExecutionControl';
import PotcarLibrary from './PotcarLibrary';
import {openRecycleBin} from './RecordCleanup';
import {requestJson,errorMessage} from './apiClient';
import {useRequestScope} from './useRequestScope';
import type {ServerConfig,RegisteredProfile} from './executionUi';
import {emptySettings,runtimeFor} from './submissionDefaults';
import type {WorkbenchSettings,SettingsContext} from './submissionDefaults';
import {importedChoice,programLabels,withResource} from './scriptChoices';
import type {ScriptRecord} from './scriptChoices';
const shortcuts=[
 ['appearance','外观和字号','theme-preferences','主题 字体 大小 显示 历史 条数'],
 ['server','服务器与脚本','scripts','主机 ssh 队列 分区 预设 脚本 vasp'],
 ['server','服务器工具环境','server-tools','PyW90 Wannier Python MPI 程序路径'],
 ['data','目录和备份恢复','backup-recovery','数据 路径 默认 工作区 备份 导入 导出 回收站 检查点'],
 ['data','高级运行环境','local-runtime','端口 缓存 耗时 诊断 Python 解释器 研究目录'],
 ['config','POTCAR 库','potcar-settings','POTCAR 赝势 导入 维护'],
 ['operations','任务与监控','worker-settings','后台 自动 推进 周期 刷新 核数 并发 配额 上限'],
 ['extensions','扩展与环境','extension-settings','扩展 插件 安装 更新 环境'],
] as const;
type Connection={resolved:Record<string,string>;connected?:boolean;note:string;python?:{path:string;version:number[]}};
type Props={section:string;context?:SettingsContext;focus?:string;onSection:(id:string,focus?:string)=>void;
 onContext:(alias:string,program:string,replace?:boolean)=>void;onBack:()=>void;backup:ReactNode};
export default function SettingsWorkbench({section,context,focus,onSection,onContext,onBack,backup}:Props){
 const scroll=useRef<HTMLDivElement>(null);
 const requests=useRequestScope('settings');
 const [profiles,setProfiles]=useState<RegisteredProfile[]>([]),[settings,setSettings]=useState<WorkbenchSettings>(emptySettings),[base,setBase]=useState<ServerConfig>({});
 const [,setRevision]=useState(0);
 const [query,setQuery]=useState('');
 const [preferred,setPreferred]=useState(()=>{try{return localStorage.getItem('fpilot-settings-server')||'';}catch{return '';}});
 const [message,setMessage]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[adding,setAdding]=useState(false),[connections,setConnections]=useState<Record<string,Connection>>({});
 const [loaded,setLoaded]=useState(false),[serverError,setServerError]=useState('');
 const [defaultsLoaded,setDefaultsLoaded]=useState(false),[defaultsError,setDefaultsError]=useState('');
 const program=context?.program||'vasp_std';
 const requested=context?.alias||preferred;
 const hasRequested=profiles.some(profile=>profile.alias===requested);
 const missingServer=Boolean(loaded&&context?.alias&&!hasRequested);
 const alias=hasRequested?requested:context?.alias?undefined:base.alias;
 const configKey='server:'+alias+':'+program,pathsKey='server-paths:'+alias;
 const cached=settingsDrafts.get<ServerConfig|null>(configKey,null);
 const baseline=alias?runtimeFor(base,profiles,settings,alias,program):null;
 const config=cached||baseline;
 const paths=settingsDrafts.get<Partial<ServerConfig>>(pathsKey,{});
 const redraw=()=>setRevision(value=>value+1);
 const setConfig=(next:ServerConfig|null)=>{
  const changed=next&&(!baseline||Object.keys({...baseline,...next}).some(key=>JSON.stringify(next[key as keyof ServerConfig])!==JSON.stringify(baseline[key as keyof ServerConfig])));
  if(changed)settingsDrafts.set(configKey,next);else settingsDrafts.delete(configKey);
  redraw();
 };
 useDialogFocus(adding,'.server-script-dialog',()=>setAdding(false));
 function select(alias:string,p:string){
  onContext(alias,p);setPreferred(alias);setMessage('');setError('');
  try{localStorage.setItem('fpilot-settings-server',alias);}catch{/* Keep the current route. */}
 }
 useEffect(()=>{
  if(section==='server'&&loaded&&alias&&!context?.alias)onContext(alias,program,true);
 },[section,loaded,alias,context?.alias,program]);
 async function loadServers(){
  const request=requests.start('servers');
  setServerError('');
  try{
   const server=await requestJson('/api/server',undefined,undefined,{signal:request.signal});
   if(!request.current())return;
   setProfiles(server.registered_profiles);
   setBase(server.config);
   setLoaded(true);
  }catch(caught){if(request.current())setServerError(errorMessage(caught));}
 }
 async function loadDefaults(){
  const request=requests.start('defaults');
  setDefaultsError('');
  try{
   const saved=await requestJson('/api/workbench-settings',undefined,undefined,{signal:request.signal});
   if(!request.current())return;
   setSettings(saved);
   setDefaultsLoaded(true);
  }catch(caught){if(request.current())setDefaultsError(errorMessage(caught));}
 }
 useEffect(()=>{void loadServers();void loadDefaults();},[requests]);

 useEffect(()=>{
  if(!focus)return;
  const area=scroll.current;
  if(!area)return;
  let observer:MutationObserver;
  const find=()=>{
   const target=focus==='scripts'?area.querySelector<HTMLElement>('.server-script-library'):
    Array.from(area.querySelectorAll<HTMLElement>('[id]')).find(element=>element.id===focus);
   if(!target)return;
   if(target instanceof HTMLDetailsElement)target.open=true;
   target.closest('details')?.setAttribute('open','');
   target.scrollIntoView({block:'start'});
   observer?.disconnect();
  };
  observer=new MutationObserver(find);observer.observe(area,{childList:true,subtree:true});find();
  return()=>observer.disconnect();
 },[focus,section,loaded,defaultsLoaded,alias]);
 const set=(key:string,value:string)=>{if(config){setConfig({...config,[key]:value});setMessage('');}};
 const setPath=(key:'pyw90_python'|'pyw90_package'|'remote_workspace_root',value:string)=>{
  const next={...paths,[key]:value};if(value===(profile?.[key]??''))delete next[key];
  settingsDrafts.set(pathsKey,next);redraw();setMessage('');
 };
 async function test(action:'parse'|'connect'){
  if(!config)return;const host=config.alias;setBusy(true);setError('');
  try{const result:Connection=await requestJson(action==='parse'?'/api/sites/check-ssh':'/api/sites/test-connection',
   action==='parse'?{alias:host}:{alias:host,remote_workspace_root:config.remote_workspace_root});setConnections(old=>({...old,[host]:result}));}
  catch(e){setError(errorMessage(e));}finally{setBusy(false);}
 }
 async function save(){
  if(!config)return;setBusy(true);setMessage('');setError('');
  try{
   if(config.runtime_source==='script'&&!config.template_id)throw new Error('请选择默认脚本，或切换到已登记运行方式');
   const saved=await requestJson('/api/workbench-settings',{runtime:config,program,template_id:config.runtime_source==='script'?config.template_id:null},'PUT');
   setSettings(saved);
   if(settingsDrafts.get(configKey,null)===cached)settingsDrafts.delete(configKey);
   redraw();setMessage('已保存 '+config.alias+' 的 '+programLabels[program]+' 新草稿默认值');
   window.dispatchEvent(new Event('fpilot:settings-changed'));
  }catch(e){setError(errorMessage(e));}finally{setBusy(false);}
 }
 async function savePaths(){
  if(!config||!Object.keys(paths).length)return;
  setBusy(true);setMessage('');setError('');
  try{
   const site=await requestJson('/api/sites/'+encodeURIComponent(config.alias)+'/paths',paths,'PUT');
   setProfiles(site.registered_profiles);
   if(settingsDrafts.get(pathsKey,null)===paths)settingsDrafts.delete(pathsKey);
   redraw();setMessage('已保存 '+config.alias+' 的目录和工具环境');
   window.dispatchEvent(new Event('fpilot:settings-changed'));
  }catch(e){setError(errorMessage(e));}finally{setBusy(false);}
 }
 function choose(id:string,script?:ScriptRecord){if(!config)return;const next=script?importedChoice(config,program,script):{...config,template_id:id,runtime_source:'script',program,vasp_executable:program};setConfig(next);setMessage('');}
 const profile=profiles.find(p=>p.alias===config?.alias),connection=config?connections[config.alias]:null;
 const defaultsFeedback=!defaultsLoaded&&(defaultsError?<div><p role="alert" className="structure-error">默认设置读取失败。{defaultsError}</p><button className="button" onClick={()=>void loadDefaults()}>重试读取默认设置</button></div>:<p role="status">正在读取默认设置…</p>);
 const terms=query.toLowerCase().trim();
 const matches=shortcuts.filter(([,title,,words])=>(title+' '+words).toLowerCase().includes(terms));
 return <div className="settings-workbench">
 <header><div><h2>全局设置</h2><p>本机工作台与服务器默认值</p></div><button className="button" onClick={onBack}>返回来处</button></header>
 <div className="settings-layout"><nav aria-label="设置分类">
  <label className="settings-search">查找设置<input type="search" value={query} maxLength={100} onChange={e=>setQuery(e.target.value)} placeholder="例如 核数、备份"/></label>
  {terms&&<div className="settings-search-results">
   {matches.map(([section,title,id])=><button key={id} onClick={()=>{setQuery('');onSection(section,id);}}>{title}</button>)}
   {profiles.filter(host=>[host.alias,host.remote_workspace_root,host.pyw90_python,host.vasp_executable].some(value=>value?.toLowerCase().includes(terms))).map(host=><button key={host.alias} onClick={()=>{setQuery('');select(host.alias,program);}}>{host.alias} · 服务器</button>)}
   {!matches.length&&!profiles.some(host=>[host.alias,host.remote_workspace_root,host.pyw90_python,host.vasp_executable].some(value=>value?.toLowerCase().includes(terms)))&&<p>没有匹配的设置。</p>}
  </div>}
  <div className="settings-nav-group">{settingsSections.map(([id,title])=><button key={id} className={section===id?'selected':''} aria-current={section===id?'page':undefined} onClick={()=>onSection(id)}>{title}</button>)}</div>
 </nav><div className="settings-content" data-navigation-scroll ref={scroll}>
 {section==='appearance'&&<><div id="theme-preferences"><AppearancePanel/></div>{defaultsFeedback}<section className="input-submit-card"><h3>显示偏好</h3><label>列表初始显示条数<select disabled={!defaultsLoaded} value={settings.history_limit} onChange={async e=>{const n=Number(e.target.value);try{setSettings(await requestJson('/api/workbench-settings',{history_limit:n},'PUT'));localStorage.setItem('fpilot-history-limit',String(n));window.dispatchEvent(new Event('fpilot:settings-changed'));setMessage('列表显示偏好已应用');}catch(e){setError(errorMessage(e));}}}>{[3,5,10].map(n=><option key={n}>{n}</option>)}</select></label><p>影响列表首次展开时的显示条数。</p></section></>}
 {section==='data'&&<><nav className="settings-jump" aria-label="数据与恢复分区"><button className="text-button" onClick={()=>onSection('data','data-directories')}>目录</button><button className="text-button" onClick={()=>onSection('data','backup-recovery')}>备份恢复</button><button className="text-button" onClick={()=>onSection('data','data-diagnostics')}>诊断</button></nav><div id="data-directories"><LocalSetup/>{defaultsLoaded?<CommonWorkspacePath initialRoot={settings.workspace_root} onSaved={root=>setSettings(current=>({...current,workspace_root:root}))}/>:defaultsFeedback}</div><section className="input-submit-card" id="backup-recovery"><h3>工作区备份与恢复</h3>{backup}<button className="button" onClick={openRecycleBin}>打开回收站</button></section><details id="data-diagnostics"><summary>诊断和缓存</summary><RemotePerformance/></details></>}
 {section==='config'&&<section className="input-submit-card" id="potcar-settings"><h3>POTCAR 库</h3><PotcarLibrary maintenance species={[]} functional={null} onApply={()=>{}}/></section>}
 {section==='server'&&<>{defaultsFeedback}<div className="server-detail-heading"><div><h3>服务器与脚本</h3><p>选择服务器，再配置连接、导入任务脚本并保存默认值。</p></div></div><div className="server-settings-layout"><aside className="server-list" aria-label="服务器列表">{profiles.map(p=><button className={config?.alias===p.alias?'selected':''} key={p.alias} disabled={busy} aria-pressed={config?.alias===p.alias} onClick={()=>select(p.alias,program)}><strong>{p.alias}</strong><span>{p.scheduler.toUpperCase()} · {p.runtime_complete?'已有登记方式':'使用导入脚本'}</span></button>)}<button className="button" disabled={busy} onClick={()=>setAdding(true)}>添加服务器</button><ServerRegistrationActions alias={config?.alias} disabled={busy} onChanged={value=>{requests.cancel('servers');setProfiles(value.registered_profiles);setBase(value.config);setLoaded(true);setServerError('');setMessage('服务器登记已更新；已有任务和脚本保留');}}/></aside><div className="server-detail">{serverError?<div><p role="alert" className="structure-error">服务器登记读取失败。{serverError}</p><button className="button" onClick={()=>void loadServers()}>重试读取服务器</button></div>:!loaded&&<p role="status">正在读取服务器登记…</p>}{missingServer?<p role="status">原服务器 <strong>{context?.alias}</strong> 已不在登记列表中。请选择另一台服务器，或恢复原登记。</p>:loaded&&!config&&<p>尚未登记服务器，请点击“添加服务器”，或恢复已移除的登记。</p>}{config&&<>
 <section className="input-submit-card"><h3>1 · 连接</h3><p>当前 SSH 别名：<strong>{config.alias}</strong> · {config.scheduler==='custom'?(profile?.command_mode==='direct'?'无调度器 · 直接运行':'自定义命令'):config.scheduler.toUpperCase()}</p><div className="action-row"><button className="button" disabled={busy} onClick={()=>void test('parse')}>读取本机 SSH 配置</button><button className="button" disabled={busy} onClick={()=>void test('connect')}>测试免交互连接</button></div>{connection?<><p role="status">{connection.connected?'免交互连接已通过':'已解析本机配置，尚未验证连接'} · {connection.resolved.user}@{connection.resolved.hostname}:{connection.resolved.port}</p><p>{connection.note}</p>{connection.python&&<p>服务器 Python {connection.python.version.join('.')} · {connection.python.path}</p>}</>:<p>尚未进行本次连接测试。已有登记与历史任务保留；测试只读取服务器信息。</p>}</section>
 <label className="server-program-choice">配置适用程序<select value={program} disabled={busy} onChange={e=>select(config.alias,e.target.value)}>{['vasp_std','vasp_ncl','wannier90'].map(p=><option key={p} value={p}>{programLabels[p]}</option>)}</select></label>
 {defaultsLoaded&&<>
 {profile&&config.scheduler==='custom'&&<CommandModeSettings key={config.alias} profile={profile} onSaved={setProfiles}/>}
 <JobTemplatePanel key={config.alias+":"+program+":"+config.scheduler} alias={config.alias} scheduler={config.scheduler} program={program} selectedKey={config.template_id||''} disabled={busy} onBusy={setBusy} onSelect={choose} onInvalidate={()=>{const next={...config};delete next.template_id;setConfig(withResource(next,program,undefined));}}/>
 <section className="input-submit-card"><h3>3 · 提交默认值</h3><p>只影响这台服务器上此程序的新草稿；提交时仍可修改。</p><fieldset disabled={busy}><label>运行方式<select value={config.runtime_source||'registered'} onChange={e=>{if(e.target.value==='registered'){const next=runtimeFor(base,profiles,{...settings,defaults:{...settings.defaults,[config.alias+':'+program]:null},runtimes:{...settings.runtimes,[config.alias+':'+program]:{...config,runtime_source:'registered'}}},config.alias,program);delete next.template_id;delete next.resource_request;next.vasp_executable=program==='vasp_ncl'?profile?.ncl_executable||'':profile?.vasp_executable||'';setConfig({...next,runtime_source:'registered'});}else set('runtime_source','script');}}><option value="script">使用上方已导入脚本</option>{config.scheduler!=='custom'&&<option value="registered">高级：使用已登记运行方式</option>}</select></label><p>{config.runtime_source==='script'?'默认脚本：'+(config.template_id?'上方已选择，保存后生效':'尚未选择'):profile?.runtime_complete?'已有运行路径登记，可用于新草稿；提交前检查实际条件。':'运行路径尚不完整，可改用已导入脚本。'}</p><div className="submit-grid"><label hidden={config.scheduler==='custom'}>默认作业名称<input value={config.name||''} onChange={e=>set('name',e.target.value)}/></label><label hidden={config.scheduler==='custom'}>{config.scheduler==='slurm'?'默认分区':'默认队列'}<input value={config.queue||''} onChange={e=>set('queue',e.target.value)}/></label><label hidden={config.scheduler==='custom'}>默认最长运行时间（HH:MM:SS）<input value={config.walltime||''} onChange={e=>set('walltime',e.target.value)}/></label></div>{config.runtime_source!=='script'&&<details><summary>高级：登记运行路径</summary>{(['vasp_executable','mpi_executable','environment_script','module'] as const).filter(key=>config.scheduler==='slurm'?['vasp_executable','module'].includes(key):key!=='module').map(key=><label key={key}>{({vasp_executable:'程序路径',mpi_executable:'MPI 启动器',environment_script:'环境脚本',module:'module/version'})[key]}<input value={config[key]||''} onChange={e=>set(key,e.target.value)}/></label>)}</details>}<div className="action-row"><button className="button primary" onClick={()=>void save()}>保存此服务器与程序的默认值</button>{cached&&<><button className="button" onClick={()=>setConfig(null)}>放弃修改</button><span role="status">有未保存修改</span></>}</div></fieldset></section>
 <details data-visit-detail="server-tools" id="server-tools"><summary>工具环境与旧项目兼容目录</summary><fieldset disabled={busy}>
  <label>PyW90 专用 Python 绝对路径<input value={paths.pyw90_python??profile?.pyw90_python??''} onChange={e=>setPath('pyw90_python',e.target.value)}/></label>
  <label>PyW90 安装目录<input value={paths.pyw90_package??profile?.pyw90_package??''} onChange={e=>setPath('pyw90_package',e.target.value)}/></label>
  <label>旧项目兼容根目录<input value={paths.remote_workspace_root??profile?.remote_workspace_root??''} onChange={e=>setPath('remote_workspace_root',e.target.value)}/></label>
  <p>这组设置适用于 {config.alias}。兼容根目录供没有项目通用根目录的旧项目使用。</p>
  <div className="action-row"><button className="button" disabled={!Object.keys(paths).length} onClick={()=>void savePaths()}>保存工具环境与兼容目录</button>{Object.keys(paths).length>0&&<><button className="button" onClick={()=>{settingsDrafts.delete(pathsKey);redraw();}}>放弃修改</button><span role="status">有未保存修改</span></>}</div>
 </fieldset></details>

 </>}
 </>}</div></div></>}
 {section==='operations'&&<section id="worker-settings"><h3>任务与监控</h3><p>启动选项适用于本机工作台，任务并发与各服务器的直接运行上限分别设置。</p><LocalSetup group="worker"/><ExecutionControl/><button className="text-button" onClick={()=>onSection('server')}>查看各服务器的直接运行上限</button></section>}
 {section==='extensions'&&<section id="extension-settings"><ExtensionManager/></section>}
 {adding&&<div className="modal-scrim"><section className="modal server-script-dialog" role="dialog" aria-modal="true" aria-label="添加服务器"><button className="button" onClick={()=>setAdding(false)}>取消添加</button><SiteRegistration onRegistered={(c,p)=>{requests.cancel('servers');setProfiles(p);setBase(c);onContext(c.alias,program);setAdding(false);setLoaded(true);setServerError('');}}/></section></div>}
 <Feedback busy={busy} label="正在处理当前服务器设置…" message={message} error={error}/></div></div></div>;
}

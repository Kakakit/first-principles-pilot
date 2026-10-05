import {sameCapacityTarget} from './executionUi';
import {openRecycleBin} from './RecordCleanup';
import {useEffect,useRef,useState} from 'react';
import type {ReactNode} from 'react';
import AppearancePanel from './AppearancePanel';
import RemotePerformance from './RemotePerformance';
import LocalSetup from './LocalSetup';
import SiteRegistration from './SiteRegistration';
import ExtensionManager from './ExtensionManager';
import ServerPanel from './ServerPanel';
import JobTemplatePanel from './JobTemplatePanel';
import ExecutionControl from './ExecutionControl';
import PotcarLibrary from './PotcarLibrary';
import {requestJson,errorMessage} from './apiClient';
import type {ServerConfig,RegisteredProfile,ServerProbe} from './executionUi';
import {runtimeFor} from './submissionDefaults';
const sections=[['appearance','概览与外观','本机设置'],['files','结构与文件','本机设置'],['flow','计算流程','当前项目'],['config','计算配置','当前节点'],['server','服务器与运行','本机设置'],['extensions','结果与扩展','本机设置']];
export default function SettingsWorkbench({section,onSection,overview,flow,configuration,files,branches}:{section:string;onSection:(id:string)=>void;overview:ReactNode;flow:ReactNode;configuration:ReactNode;files?:ReactNode;branches?:ReactNode}){
 const scroll=useRef<HTMLDivElement>(null);
 useEffect(()=>{if(scroll.current)scroll.current.scrollTop=0;},[section]);
 const [config,setConfig]=useState<ServerConfig|null>(null),[profiles,setProfiles]=useState<RegisteredProfile[]>([]),[probe,setProbe]=useState<ServerProbe|null>(null),[template,setTemplate]=useState(''),[program,setProgram]=useState('vasp_std'),[message,setMessage]=useState(''),[busy,setBusy]=useState(false),[limit,setLimit]=useState(5);
 useEffect(()=>{void Promise.all([requestJson('/api/server'),requestJson('/api/workbench-settings')]).then(([server,settings])=>{setProfiles(server.registered_profiles);setConfig(runtimeFor(server.config,server.registered_profiles,settings,server.config.alias,program));setTemplate(settings.defaults[server.config.alias+':'+program]||'');setLimit(settings.history_limit);}).catch(e=>setMessage(errorMessage(e)));},[]);
 const save=async()=>{if(!config)return;setBusy(true);try{await requestJson('/api/server',{config},'PUT');await requestJson('/api/workbench-settings',{runtime:config,program,template_id:template||null},'PUT');setMessage('已保存当前服务器与程序的默认配置');window.dispatchEvent(new Event('fpilot:settings-changed'));}catch(e){setMessage(errorMessage(e));}finally{setBusy(false);}};
 const detect=async()=>{if(!config)return;setBusy(true);try{setProbe(await requestJson('/api/server/probe',{config,purpose:program==='wannier90'?'wannier90':'vasp'}));setMessage('只读诊断完成');}catch(e){setMessage(errorMessage(e));}finally{setBusy(false);}};
 return <div className="settings-workbench"><header><h2>设置</h2><p>按使用范围保存。科学参数和本次来源在计算配置中填写。</p></header><div className="settings-layout"><nav aria-label="设置分类">{sections.map(([id,title,scope])=><button key={id} className={section===id?'active':''} aria-pressed={section===id} onClick={()=>onSection(id)}>{title}<span>{scope}</span></button>)}</nav><div ref={scroll} className="settings-content" tabIndex={0} aria-label="设置内容" data-section={section}>
 {section==='appearance'&&<><LocalSetup/><AppearancePanel/><section className="input-submit-card"><h3>本机路径、备份与恢复</h3>{overview}</section></>}
 {section==='files'&&<section className="input-submit-card"><h3>结构与文件</h3>{files}<p>结构的导入、坐标和晶胞仍在结构页编辑；本次赝势版本在计算配置中选择。</p><button className="button" onClick={()=>onSection('config')}>配置本机 POTCAR 库</button></section>}
 {section==='flow'&&<><section className="input-submit-card"><h3>流程模板与目录命名</h3>{flow}</section><section className="input-submit-card"><h3>分支与服务器</h3>{branches}</section></>}
 {section==='config'&&<><section className="input-submit-card"><h3>预设、个人模板与高级来源 · 当前节点</h3>{configuration}</section><section className="input-submit-card"><h3>POTCAR 库 · 本机设置</h3><PotcarLibrary maintenance species={[]} functional={null} onApply={()=>{}}/></section></>}
 {section==='server'&&<><SiteRegistration onRegistered={(c,p)=>{setConfig(c);setProfiles(p);setProbe(null);setTemplate('');}}/><label>默认配置适用程序<select value={program} onChange={async e=>{const p=e.target.value;setProgram(p);if(config){const settings=await requestJson('/api/workbench-settings');setConfig(runtimeFor(config,profiles,settings,config.alias,p));setTemplate(settings.defaults[config.alias+':'+p]||'');}}}><option value="vasp_std">VASP 常规</option><option value="vasp_ncl">VASP SOC／非共线</option><option value="wannier90">Wannier90</option></select></label><ServerPanel maintenance program={program} config={config} profiles={profiles} probe={probe} preview={null} node="" cores="" reason="" busy={busy} onConfigChange={async c=>{if(c.alias!==config?.alias){const settings=await requestJson('/api/workbench-settings');setConfig(runtimeFor(c,profiles,settings,c.alias,program));setTemplate(settings.defaults[c.alias+':'+program]||'');}else setConfig(c);if(!sameCapacityTarget(config,c))setProbe(null);}} onNodeChange={()=>{}} onCoresChange={()=>{}} onReasonChange={()=>{}} onSaveAndProbe={()=>void save().then(detect)} onProbe={()=>void detect()} onPreview={()=>{}}/><JobTemplatePanel program={program} alias={config?.alias||''} selectedKey={template} onSelect={id=>{setTemplate(id);if(config&&id)setConfig({...config,runtime_source:'script',program,vasp_executable:program,template_id:id});}} onInvalidate={()=>{setTemplate('');if(config){const next={...config};delete next.template_id;setConfig(next);}}}/><button className="button primary" disabled={busy} onClick={()=>void save()}>保存此服务器与程序的默认配置</button><ExecutionControl/><RemotePerformance/></>}
 {section==='extensions'&&<><label>历史默认显示条数<select value={limit} onChange={async e=>{const n=Number(e.target.value);await requestJson('/api/workbench-settings',{history_limit:n},'PUT');setLimit(n);localStorage.setItem('fpilot-history-limit',String(n));window.dispatchEvent(new Event('fpilot:settings-changed'));}}>{[3,5,10].map(n=><option key={n}>{n}</option>)}</select></label><p>本地记录可在各页面的历史中回收，统一回收站支持恢复和清空本地文件。</p><button className="button" onClick={openRecycleBin}>打开回收站</button><ExtensionManager/></>}
 {message&&<p role="status">{message}</p>}</div></div></div>;
}

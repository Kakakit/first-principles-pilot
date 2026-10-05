import {useEffect,useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
import {openSettings} from './submissionDefaults';
import type {ServerConfig} from './executionUi';

type Script={id:string;name:string;alias:string;scheduler:string;program:string;source:string;resources?:Record<string,string|number>};
const labels:Record<string,string>={vasp_std:'VASP 常规',vasp_ncl:'VASP SOC／非共线',wannier90:'Wannier90'};

export default function ScriptPicker({config,programs,disabled,onChange,onSelect,registeredExecutable}:{config:ServerConfig;programs:string[];disabled?:boolean;onChange:(value:ServerConfig)=>void;onSelect?:(id:string)=>void;registeredExecutable?:string}){
  const [scripts,setScripts]=useState<Script[]>([]),[error,setError]=useState(''),[original,setOriginal]=useState<{name:string;text:string}|null>(null);
  useEffect(()=>{
    let active=true;
    const load=()=>void requestJson('/api/job-templates').then(value=>{if(active){setScripts(value);setError('');}}).catch(e=>{if(active)setError(errorMessage(e));});
    load();window.addEventListener('fpilot:settings-changed',load);window.addEventListener('fpilot:history-changed',load);
    return()=>{active=false;window.removeEventListener('fpilot:settings-changed',load);window.removeEventListener('fpilot:history-changed',load);};
  },[config.alias]);
  useEffect(()=>{
    if(config.queue||!config.template_id)return;
    const queue=scripts.find(s=>s.id===config.template_id)?.resources?.queue;
    if(typeof queue==='string')onChange({...config,queue});
  },[scripts,config.template_id,config.queue]);
  let selected:Record<string,string>={};
  try{selected=JSON.parse(config.script_templates||'{}');}catch{/* No persisted choice yet. */}
  if(config.template_id)selected[config.program||programs[0]]=config.template_id;
  function choose(program:string,id:string){
    setOriginal(null);onSelect?.(id);
    if(!id){
      const next:ServerConfig={...config,runtime_source:'registered',program:programs[0],vasp_executable:registeredExecutable||''};
      delete next.template_id;delete next.script_templates;onChange(next);return;
    }
    const script=scripts.find(s=>s.id===id);if(!script)return;
    const choices={...selected,[program]:id};
    const base=programs[0],resources=script.resources||{};
    onChange({...config,runtime_source:'script',program:base,vasp_executable:base,template_id:choices[base]||'',script_templates:JSON.stringify(choices),
      ...(typeof resources.queue==='string'?{queue:resources.queue}:{}),
      ...(programs.length===1&&typeof resources.name==='string'?{name:resources.name}:{}),
      ...(programs.length===1&&typeof resources.walltime==='string'?{walltime:resources.walltime}:{})});
  }
  return <div className="script-picker">
    {programs.map(program=>{
      const choices=scripts.filter(s=>s.alias===config.alias&&s.scheduler===config.scheduler&&(s.program===program||program==='vasp_std'&&s.program==='gpu_vasp'));
      const id=selected[program]||'';
      return <div key={program}><label>{programs.length>1?labels[program]+' 脚本':'任务脚本'}<select value={id} disabled={disabled} onChange={e=>choose(program,e.target.value)}>
        <option value="">{config.runtime_source==='script'?'请选择已导入脚本':'使用服务器已登记运行方式'}</option>
        {choices.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}
        {id&&!choices.some(s=>s.id===id)&&<option value={id} disabled>当前脚本不适用于此程序，请重新选择</option>}
      </select></label>
      {id&&<button className="text-button" disabled={disabled} onClick={()=>void requestJson('/api/job-templates/'+id).then(s=>setOriginal({name:s.name,text:s.original_text})).catch(e=>setError(errorMessage(e)))}>查看原始脚本</button>}
      {!choices.length&&<p>此服务器尚无{labels[program]||program}脚本，请先导入。环境加载和程序命令沿用脚本正文。</p>}
      </div>;
    })}
    <button className="text-button" onClick={()=>openSettings('server')}>导入或管理任务脚本</button>
    {original&&<details open><summary>{original.name} · 原始脚本</summary><pre className="input-text">{original.text}</pre></details>}
    {error&&<p role="alert">{error}</p>}
  </div>;
}

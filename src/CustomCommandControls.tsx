import {useEffect,useState} from 'react';
import type {ServerConfig} from './executionUi';
import type {ScriptRecord} from './scriptChoices';
import {requestJson,errorMessage} from './apiClient';
import {openSettings} from './submissionDefaults';
export default function CustomCommandControls({config,onChange,disabled}:{config:ServerConfig;onChange:(value:ServerConfig)=>void;disabled?:boolean}){
 const [presets,setPresets]=useState<ScriptRecord[]>([]),[error,setError]=useState('');
 useEffect(()=>{let active=true;const load=()=>void requestJson('/api/job-templates').then(rows=>{if(active)setPresets(rows.filter((s:ScriptRecord)=>s.alias===config.alias&&s.scheduler==='custom'));}).catch(e=>setError(errorMessage(e)));load();window.addEventListener('fpilot:settings-changed',load);return()=>{active=false;window.removeEventListener('fpilot:settings-changed',load);};},[config.alias]);
 return <section className="input-submit-card"><h3>自定义命令</h3><p>在本次运行目录原样执行，不检测队列、核数或 GPU。命令退出不代表队列里的计算已完成；本轮不自动推进链和迭代。</p><label>命令预设<select aria-label="命令预设" disabled={disabled} value={config.template_id||''} onChange={e=>onChange({...config,runtime_source:'script',template_id:e.target.value})}><option value="">请选择已保存的命令</option>{presets.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label><button className="text-button" onClick={()=>openSettings('server',{alias:config.alias,program:config.program||'vasp_std',focus:'scripts',returnToRun:true})}>保存或管理命令</button>{error&&<p role="alert">{error}</p>}</section>;
}

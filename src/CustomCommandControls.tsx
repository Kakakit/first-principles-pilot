import {useEffect,useState} from 'react';
import {commandMode,type ServerConfig} from './executionUi';
import {programLabels,type ScriptRecord} from './scriptChoices';
import {requestJson,errorMessage} from './apiClient';
import {openSettings} from './submissionDefaults';
export default function CustomCommandControls({config,onChange,disabled}:{config:ServerConfig;onChange:(value:ServerConfig)=>void;disabled?:boolean}){
 const [presets,setPresets]=useState<ScriptRecord[]>([]),[error,setError]=useState('');
 const direct=commandMode(config)==='direct';
 useEffect(()=>{let active=true;const load=()=>void requestJson('/api/job-templates').then(rows=>{if(active)setPresets(rows.filter((s:ScriptRecord)=>s.alias===config.alias&&s.scheduler==='custom'));}).catch(e=>setError(errorMessage(e)));load();window.addEventListener('fpilot:settings-changed',load);return()=>{active=false;window.removeEventListener('fpilot:settings-changed',load);};},[config.alias]);
 return <section className="input-submit-card"><h3>{direct?'直接运行':'自定义命令'}</h3><p>{direct?'在本次任务目录运行脚本，可跟踪进程、查看日志和停止。此方式支持单任务。':'在本次运行目录原样执行，不检测队列、核数或 GPU。命令退出不代表队列里的计算已完成；本轮不自动推进链和迭代。'}</p>{direct&&<p>当前输入需要 {programLabels[config.program||'vasp_std']}。请选用对应的启动脚本。</p>}<label>命令预设<select aria-label="命令预设" disabled={disabled} value={config.template_id||''} onChange={e=>onChange({...config,runtime_source:'script',template_id:e.target.value})}><option value="">请选择已保存的命令</option>{presets.map(p=><option key={p.id} value={p.id}>{p.name}{direct?' · '+(programLabels[p.program]||p.program):''}</option>)}</select></label><button className="text-button" onClick={()=>openSettings('server',{alias:config.alias,program:config.program||'vasp_std',focus:'scripts',returnToRun:true})}>保存或管理命令</button>{error&&<p role="alert">{error}</p>}</section>;
}

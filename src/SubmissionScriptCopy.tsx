import {literalSlurmFields} from './scriptDirectives';
import {useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
import type {ScriptRecord} from './scriptChoices';

export default function SubmissionScriptCopy({script,onApply,disabled}:{script:ScriptRecord;onApply:(copy:ScriptRecord)=>void;disabled?:boolean}){
 const [editing,setEditing]=useState(false),[name,setName]=useState(script.name+' · 副本');
 const [body,setBody]=useState(script.original_text||''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function save(){
  setBusy(true);setError('');
  try{
   const copy:ScriptRecord=await requestJson('/api/job-templates',{
    alias:script.alias,scheduler:script.scheduler,name:name.trim(),source:script.source,parent_id:script.id,script:body
   });
   onApply(copy);
   window.dispatchEvent(new Event('fpilot:settings-changed'));
  }catch(e){setError(errorMessage(e));}finally{setBusy(false);}
 }
 if(!editing)return <><pre className="input-text">{script.original_text}</pre><button className="text-button" disabled={disabled} onClick={()=>setEditing(true)}>编辑并另存副本</button></>;
 return <fieldset disabled={disabled||busy}><label>副本名称<input value={name} onChange={e=>setName(e.target.value)}/></label>
  {script.scheduler==='slurm'&&<div className="submit-grid">{literalSlurmFields(body).map(field=><label key={field.index}>{field.label}<input key={field.label+field.value} defaultValue={field.value} onBlur={e=>{
   const value=e.target.value.trim();
   if(!(field.label==='任务名称'?/^[A-Za-z0-9_.-]+$/:/^[1-9]\d*$/).test(value)){e.target.value=field.value;return;}
   setBody(old=>{const lines=old.split(/\r?\n/);lines[field.index]=field.prefix+value+field.suffix;return lines.join(old.includes('\r\n')?'\r\n':'\n');});
  }}/></label>)}</div>}
  <label>脚本正文<textarea rows={14} spellCheck={false} value={body} onChange={e=>setBody(e.target.value)}/></label>
  <button className="button primary" disabled={!body.trim()||!name.trim()} onClick={()=>void save()}>保存到脚本库并用于本次任务</button>
  <button className="text-button" onClick={()=>setEditing(false)}>取消编辑</button>{error&&<p role="alert">{error}</p>}</fieldset>;
}

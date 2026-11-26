import {useEffect,useRef,useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
import {DeleteRecord,RecycleBin} from './RecordCleanup';
import {availableScript,recentTime,schedulerName,scriptFits} from './scriptChoices';
import {settingsDrafts} from './submissionDrafts';
import {useSettingsDraft} from './useSubmissionDraft';
import type {ScriptRecord} from './scriptChoices';
type Draft={name:string;text:string;source:string;parent:string};
type Verified={key:string;name:string;alias:string;scheduler:string;program:string;source:string;executable:boolean;reason?:string};
type Props={startImport?:boolean;onImportOpened?:()=>void;alias:string;scheduler:string;program?:string;selectedKey:string;disabled?:boolean;onSelect:(id:string,script?:ScriptRecord)=>void;onInvalidate:()=>void;onBusy?:(busy:boolean)=>void};
export default function JobTemplatePanel({alias,scheduler,program='vasp_std',selectedKey,disabled,onSelect,onInvalidate,onBusy,startImport,onImportOpened}:Props){
 const [scripts,setScripts]=useState<ScriptRecord[]>([]),[verified,setVerified]=useState<Verified[]>([]),[status,setStatus]=useState<'loading'|'ready'|'error'>('loading');
 const [error,setError]=useState(''),[busy,setBusyState]=useState(false),[editing,setEditing]=useState<string|null>(null),[catalog,setCatalog]=useState(false);
 const draftKey='script:'+JSON.stringify([alias,scheduler]);
 const [drafts,setDrafts]=useSettingsDraft<Record<string,Draft>>(draftKey,{});
 const [initial,setInitial]=useState<Draft|null>(null);
 const {name,text,source,parent}=(editing&&drafts[editing])||initial||{name:'',text:'',source:'',parent:''};
 const update=(patch:Partial<Draft>,key=editing)=>{
  if(!key)return;
  setDrafts(old=>({...old,[key]:{...(old[key]||{name,text,source,parent}),...patch}}));
 };
 const clear=(key:string,expected:Draft|undefined)=>{
  const saved=settingsDrafts.get<Record<string,Draft>>(draftKey,{});
  if(saved[key]!==expected)return;
  const next={...saved};delete next[key];settingsDrafts.set(draftKey,next);setDrafts(next);
 };
 const [original,setOriginal]=useState<ScriptRecord|null>(null),[preview,setPreview]=useState<{script_text:string;execution_blockers:string[]}|null>(null);
 const setBusy=(value:boolean)=>{setBusyState(value);if(context.current)onBusy?.(value);};
 const generation=useRef(0),context=useRef('');context.current=alias+':'+program;
 const load=async()=>{const id=++generation.current;setStatus('loading');try{const [list,candidates]=await Promise.all([requestJson('/api/job-templates'),requestJson('/api/job-templates/verified')]);if(id!==generation.current)return;setScripts(list);setVerified(candidates);setError('');setStatus('ready');}catch(e){if(id===generation.current){setError(errorMessage(e));setStatus('error');}}};
 useEffect(()=>{context.current=alias+':'+program;void load();setEditing(null);setCatalog(false);setOriginal(null);setPreview(null);return()=>{generation.current++;context.current='';onBusy?.(false);};},[alias,program,scheduler]);
 useEffect(()=>{if(startImport){newImport();onImportOpened?.();}},[startImport]);
 const choices=scripts.filter(s=>s.alias===alias).sort((a,b)=>recentTime(b.id)-recentTime(a.id)||a.name.localeCompare(b.name));
 const candidates=verified.filter(s=>s.alias===alias&&!scripts.some(existing=>existing.alias===s.alias&&existing.program===s.program&&existing.name===s.name));
 const announce=()=>window.dispatchEvent(new Event('fpilot:settings-changed'));
 async function importOriginal(item:Verified){const target=context.current;setBusy(true);setError('');try{const result=await requestJson('/api/job-templates/verified/'+encodeURIComponent(item.key)+'/import',{},'POST');const record:ScriptRecord=result.template||result.original;await load();announce();if(target===context.current&&record&&scriptFits(record,alias,scheduler,program))onSelect(record.id,record);setCatalog(false);}catch(e){setError(errorMessage(e));}finally{setBusy(false);}}
 function newImport(){setInitial(null);setEditing('new');setError('');}
 async function show(id:string,copy=false){const target=context.current;setBusy(true);setError('');try{const record:ScriptRecord=await requestJson('/api/job-templates/'+id);if(target!==context.current)return;if(copy){setInitial({name:record.name+' · 适配副本',text:record.original_text||'',source:record.source,parent:record.id});setEditing(record.id);}else setOriginal(record);}catch(e){setError(errorMessage(e));}finally{setBusy(false);}}
 async function save(){if(!editing)return;const target=context.current,key=editing,sent=drafts[key];setBusy(true);setError('');try{const record:ScriptRecord=await requestJson('/api/job-templates',{alias,name,script:text,source:source||name,parent_id:parent||null,scheduler:schedulerName(scheduler)});clear(key,sent);if(target!==context.current)return;await load();announce();if(target===context.current&&scriptFits(record,alias,scheduler,program))onSelect(record.id,record);setEditing(null);}catch(e){setError(errorMessage(e));}finally{setBusy(false);}}
 const [renaming,setRenaming]=useState(''),[newName,setNewName]=useState('');
 async function rename(id:string){
  setBusy(true);setError('');
  try{await requestJson('/api/job-templates/'+id+'/name',{name:newName.trim()},'PUT');setRenaming('');setOriginal(null);await load();announce();}
  catch(e){setError(errorMessage(e));}finally{setBusy(false);}
 }
 const detected=/^\s*#SBATCH\b/m.test(text)&&!/^\s*#PBS\b/m.test(text)?'slurm':/^\s*#PBS\b/m.test(text)&&!/^\s*#SBATCH\b/m.test(text)?'pbs':'';
 const conflict=Boolean(detected&&schedulerName(scheduler)!==detected);
 return <section className="input-submit-card server-script-library" aria-label="当前服务器任务脚本"><div className="server-detail-heading"><div><h3>2 · 任务脚本</h3><p>{alias} · 上传或粘贴平时使用的脚本。</p></div><button className="button primary" disabled={disabled||busy} onClick={newImport}>{drafts.new?'继续编辑':scheduler==='custom'?'保存命令':'导入脚本'}</button></div>
 {!editing&&status!=='error'&&error&&<p role="alert">{error}</p>}{status==='loading'&&<p role="status">正在读取脚本…</p>}{status==='error'&&<p role="alert">脚本读取失败：{error} <button className="text-button" onClick={()=>void load()}>重试</button></p>}
 {status==='ready'&&<>{!choices.length&&<p>当前服务器尚未导入脚本。可上传、粘贴，或选择已有原稿。</p>}<div className="server-script-list">{choices.map(script=><article key={script.id} className={selectedKey===script.id?'selected':''}><div><button className="text-button" onClick={()=>void show(script.id)}>{script.name}</button><span>{script.scheduler.toUpperCase()}{script.resource_request?.gpus ? " · "+script.resource_request.gpus+" GPU" : ""}</span></div><details><summary>更多</summary><div className="action-row"><button className="button" disabled={disabled||busy||!availableScript(script,alias,scheduler)} aria-pressed={selectedKey===script.id} onClick={()=>onSelect(script.id,script)}>{selectedKey===script.id?'默认候选已选':'选择为默认'}</button><button className="text-button" disabled={busy} onClick={()=>{if(drafts[script.id]){setEditing(script.id);setError('');}else void show(script.id,true);}}>{drafts[script.id]?'继续编辑副本':'编辑副本'}</button>{renaming===script.id?<label>新名称<input autoFocus value={newName} onChange={e=>setNewName(e.target.value)}/><button className="button" disabled={busy||!newName.trim()} onClick={()=>void rename(script.id)}>保存名称</button><button className="text-button" onClick={()=>setRenaming('')}>取消</button></label>:<button className="text-button" disabled={busy} onClick={()=>{setNewName(script.name);setRenaming(script.id);}}>重命名</button>}<DeleteRecord kind="job-templates" id={script.id} label={script.name} onChanged={async()=>{await load();announce();if(original?.id===script.id){setOriginal(null);setPreview(null);}if(script.id===selectedKey)onInvalidate();}}/></div></details></article>)}</div>
 {candidates.length>0&&<><button className="text-button" onClick={()=>setCatalog(!catalog)}>从已有原稿导入（{candidates.length}）</button>{catalog&&<div className="server-originals">{candidates.map(item=><article key={item.key}><strong>{item.name}</strong><p>{item.reason||'历史原稿；导入与资源适配不代表本次程序运行验证。'}</p><button className="button" disabled={!item.executable||disabled||busy} onClick={()=>void importOriginal(item)}>导入此原稿</button></article>)}</div>}</>}
 <RecycleBin kind="job-templates" title="脚本回收站" onChanged={async()=>{await load();announce();}}/></>}
 {original&&<div className="server-original-preview"><div className="server-detail-heading"><h4>{original.name} · 原始正文</h4><button className="text-button" onClick={()=>{setOriginal(null);setPreview(null);}}>收起</button></div><pre className="input-text">{original.original_text}</pre><button className="text-button" onClick={()=>void requestJson('/api/job-templates/'+original.id+'/preview',{alias,overrides:{}}).then(setPreview).catch(e=>setError(errorMessage(e)))}>查看资源适配检查</button>{preview&&<>{preview.execution_blockers.map((b,i)=><p key={i}>{b}</p>)}<pre className="input-text">{preview.script_text}</pre></>}</div>}
 {editing&&<div className="modal-scrim"><section className="modal server-script-dialog" role="dialog" aria-modal="true" aria-labelledby="script-dialog-title"><div className="server-detail-heading"><h3 id="script-dialog-title">{parent?'编辑副本':scheduler==='custom'?'保存自定义命令':'导入任务脚本'}</h3><button className="button" disabled={busy} onClick={()=>setEditing(null)}>关闭编辑</button>{drafts[editing]&&<button className="text-button" disabled={busy} onClick={()=>{clear(editing,drafts[editing]);setEditing(null);}}>放弃草稿</button>}</div><p>服务器：<strong>{alias}</strong> · {scheduler.toUpperCase()}。原稿保存后不会被覆盖。</p><fieldset disabled={busy}><div className="submit-grid"><label>脚本名称<input value={name} onChange={e=>update({name:e.target.value})}/></label></div><label>上传原稿<input type="file" onChange={e=>{const file=e.target.files?.[0];if(!file)return;void file.text().then(value=>{if(context.current)update({text:value,source:file.name,...(!name?{name:file.name}:{})});}).catch(e=>setError(errorMessage(e)));}}/></label><label className="workbench-field-line"><span className="field-line-name">脚本正文</span><textarea rows={16} spellCheck={false} value={text} onChange={e=>update({text:e.target.value})}/></label><p>{detected?'调度器 · '+detected.toUpperCase():'未识别到调度器指令，按原稿保存'}</p>{conflict&&<p role="alert">识别结果与当前选择不同；请核对服务器的调度器设置。</p>}<button className="button primary" disabled={!name.trim()||!text.trim()} onClick={()=>void save()}>保存{parent?'副本':'并导入'}</button></fieldset>{error&&<p role="alert" className="structure-error">{error}</p>}</section></div>}
 {!editing&&status!=='error'&&error&&<p role="alert" className="structure-error">{error}</p>}
 </section>;
}

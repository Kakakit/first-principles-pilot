import {useEffect,useRef,useState} from 'react';
import {catalog} from './model';
import type {Project,Step} from './model';
import {requestJson} from './apiClient';
import {typeSwitchPreview} from './nodeTypes';
import type {VaspKind} from './nodeTypes';
import {VASP_KINDS} from './stagePolicy';

type Eligibility={can_switch:boolean;reasons:string[]};
export default function NodeTypeSwitch({project,node,disabled,saveState,onApply,onCopy}:{project:Project;node:Step;disabled:boolean;saveState:string;onApply:(node:Step)=>void;onCopy:()=>void}){
  const [eligibility,setEligibility]=useState<Eligibility|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[target,setTarget]=useState<VaspKind|null>(null);
  const mounted=useRef(true),context=useRef('');
  context.current=project.id+':'+project.updatedAt+':'+node.id;
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  useEffect(()=>{setTarget(null);setEligibility(null);setError('');},[project.id,node.id,node.data.kind]);
  useEffect(()=>{
    if(!saveState.startsWith('已保存'))return;
    let active=true;
    const refresh=()=>requestJson(`/api/projects/${encodeURIComponent(project.id)}/node-editability`).then(result=>{if(active){setEligibility(result.nodes[node.id]||null);setError('');}}).catch(e=>{if(active){setEligibility(null);setError((e as Error).message);}});
    void refresh();window.addEventListener('fpilot:history-changed',refresh);
    return()=>{active=false;window.removeEventListener('fpilot:history-changed',refresh);};
  },[project.id,node.id,saveState]);
  useEffect(()=>{
    if(!target)return;
    const previous=document.activeElement as HTMLElement;
    const dialog=document.querySelector<HTMLElement>('.node-type-dialog');
    dialog?.querySelector<HTMLElement>('[data-cancel]')?.focus();
    const key=(e:KeyboardEvent)=>{if(e.key==='Escape'&&!busy)setTarget(null);if(e.key==='Tab'&&dialog){const items=Array.from(dialog.querySelectorAll<HTMLElement>('button:not(:disabled)'));const first=items[0],last=items.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}};
    document.addEventListener('keydown',key);return()=>{document.removeEventListener('keydown',key);previous?.focus();};
  },[target,busy]);
  if(!VASP_KINDS.includes(node.data.kind as VaspKind))return null;
  const preview=target?typeSwitchPreview(project,node,target):null;
  const apply=async()=>{
    if(!preview||disabled||busy)return;
    setBusy(true);setError('');
    const started=context.current;
    try{
      const result=await requestJson(`/api/projects/${encodeURIComponent(project.id)}/node-editability`);
      if(!mounted.current||context.current!==started)return;
      const latest:Eligibility|undefined=result.nodes[node.id];setEligibility(latest||null);
      if(!latest?.can_switch)throw new Error(latest?.reasons.join('；')||'节点尚未保存，请等待保存后重试');
      onApply(preview.node);setTarget(null);
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}
  };
  return <section className="node-type-switch"><label className="field">计算类型<select aria-label="计算类型" value={node.data.kind} disabled={disabled||busy||!saveState.startsWith('已保存')||!eligibility?.can_switch} onChange={e=>setTarget(e.target.value as VaspKind)}>{VASP_KINDS.map(kind=><option key={kind} value={kind}>{catalog[kind].label}</option>)}</select></label>
    <button className="text-button" disabled={disabled} title={eligibility?.reasons.join('；')} onClick={onCopy}>复制为新节点</button>
    {error&&<p role="alert">{error}</p>}
    {preview&&<div className="modal-backdrop" onClick={()=>{if(!busy)setTarget(null);}}><section className="modal node-type-dialog" role="dialog" aria-modal="true" aria-label="切换计算类型预览" onClick={e=>e.stopPropagation()}><h2>切换为{preview.label}</h2><p>{node.data.label} · {catalog[node.data.kind].label} → {preview.label}</p><p>阶段目录：<code>{String(node.data.stageSequence).padStart(2,'0')}-{node.data.stageSlug}</code> → <code>{String(preview.node.data.stageSequence).padStart(2,'0')}-{preview.node.data.stageSlug}</code></p>
      {preview.changes.length?<table><thead><tr><th>参数</th><th>当前</th><th>切换后</th></tr></thead><tbody>{preview.changes.map(c=><tr key={c.tag}><td>{c.tag}</td><td>{c.before??'未设置'}</td><td>{c.after??'待填写'}</td></tr>)}</tbody></table>:<p>保留当前物理参数。</p>}
      <p>名称、备注、磁矩、Hubbard、赝势和 k 点草稿保留；来源绑定、路径核验和旧审核确认清除，按新类型重新核验。</p>
      {preview.issues.length>0&&<div className="handoff-warning"><strong>连接需要重新检查</strong>{preview.issues.map((issue,i)=><p key={i}>{issue.message}</p>)}</div>}
      {error&&<p role="alert">{error}</p>}<div className="node-type-actions"><button className="button" data-cancel disabled={busy} onClick={()=>setTarget(null)}>取消</button><button className="button primary" disabled={busy||disabled} onClick={()=>void apply()}>{busy?'核查中…':'切换类型'}</button></div>
    </section></div>}
  </section>;
}

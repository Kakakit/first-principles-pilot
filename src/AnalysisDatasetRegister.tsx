import {useEffect,useRef,useState} from 'react';
import {Database} from 'lucide-react';
import {requestJson,errorMessage} from './apiClient';
import {openRecycleBin} from './RecordCleanup';
import {openDataset} from './analysisDatasetModel';
import type {AnalysisKind} from './analysisDatasetModel';

export default function AnalysisDatasetRegister({kind,sourceId,state,onRegistered}:{kind:AnalysisKind;sourceId:string;state?:string;onRegistered?:()=>void|Promise<void>}){
 const [registration,setRegistration]=useState<{dataset_id?:string;dataset_recycled?:boolean}>({}),[opened,setOpened]=useState(false),[title,setTitle]=useState(''),[note,setNote]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const dialog=useRef<HTMLDialogElement>(null),active=useRef('');active.current=kind+sourceId;
 useEffect(()=>{let live=true;setRegistration({});setOpened(false);setBusy(false);setError('');if(state==='COMPLETED')void requestJson(`/api/analysis-datasets/status/${kind}/${sourceId}`).then(data=>{if(live)setRegistration(data);}).catch(cause=>{if(live)setError(errorMessage(cause));});return()=>{live=false;};},[kind,sourceId,state]);
 useEffect(()=>{if(opened)dialog.current?.showModal();else dialog.current?.close();},[opened]);
 if(state!=='COMPLETED')return null;
 const begin=async()=>{if(registration.dataset_id){if(registration.dataset_recycled)openRecycleBin();else openDataset(registration.dataset_id);return;}const signature=active.current;setBusy(true);setError('');try{const row=await requestJson(`/api/analysis-datasets/history/${kind}/${sourceId}`);if(active.current===signature){setTitle(row.title);setNote('');setOpened(true);}}catch(cause){if(active.current===signature)setError(errorMessage(cause));}finally{if(active.current===signature)setBusy(false);}};
 const save=async()=>{const signature=active.current;setBusy(true);setError('');try{const record=await requestJson('/api/analysis-datasets',{source_kind:kind,source_id:sourceId,title,note});if(active.current===signature){setRegistration({dataset_id:record.id});setOpened(false);await onRegistered?.();}}catch(cause){if(active.current===signature)setError(errorMessage(cause));}finally{if(active.current===signature)setBusy(false);}};
 return <div className="analysis-register"><button className="button" disabled={busy} onClick={()=>void begin()}><Database size={16}/>{busy?'正在读取…':registration.dataset_id?(registration.dataset_recycled?'已登记 · 在回收区':'已登记／查看数据集'):'登记到数据集'}</button>{error&&!opened&&<p role="alert" className="structure-error">{error}</p>}<dialog ref={dialog} className="analysis-register-dialog" aria-label="登记分析结果到数据集" onCancel={event=>{event.preventDefault();if(!busy)setOpened(false);}}><h3>登记到数据集</h3><p>保存本机已有的结果、来源和交互设置，以后可从左侧“数据集”查看。</p><label>数据集名称<input value={title} maxLength={500} onChange={event=>setTitle(event.target.value)}/></label><label>备注<textarea value={note} maxLength={10000} rows={4} onChange={event=>setNote(event.target.value)}/></label>{error&&<p role="alert" className="structure-error">{error}</p>}<div className="action-row"><button className="button" disabled={busy} onClick={()=>setOpened(false)}>取消</button><button className="button primary" disabled={busy||!title.trim()} onClick={()=>void save()}>{busy?'正在保存…':'确认登记'}</button></div></dialog></div>;
}

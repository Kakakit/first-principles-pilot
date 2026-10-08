import {useEffect,useRef,useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
import {noteLabels,openProblem} from './problemRecords';
import type {ProblemNotes,ProblemRecord} from './problemRecords';
export function ProblemEditor({record,onSaved}:{record:ProblemRecord;onSaved?:(value:ProblemRecord)=>void}){
 const [notes,setNotes]=useState<ProblemNotes>(record.notes),[tags,setTags]=useState(record.incar_tags.join(' ')),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
 const active=useRef(record.id);active.current=record.id;
 useEffect(()=>{setNotes(record.notes);setTags(record.incar_tags.join(' '));setMessage('');},[record.id]);
 const save=async()=>{const id=record.id;setBusy(true);setMessage('');try{const data:ProblemRecord=await requestJson(`/api/problem-records/${id}`,{notes,incar_tags:tags.toUpperCase().split(/[\s,，]+/).filter(Boolean)},'PUT');if(active.current!==id)return;setNotes(data.notes);setTags(data.incar_tags.join(' '));setMessage('备注已保存；计算证据未改动。');onSaved?.(data);window.dispatchEvent(new Event('fpilot:problem-records-changed'));}catch(e){if(active.current===id)setMessage(errorMessage(e));}finally{if(active.current===id)setBusy(false);}};
 return <div className="problem-editor"><fieldset disabled={busy}>{Object.entries(noteLabels).map(([key,label])=><label key={key}>{label}<textarea maxLength={4000} value={notes[key as keyof ProblemNotes]} onChange={e=>setNotes({...notes,[key]:e.target.value})}/></label>)}<label>关联 INCAR 参数（可选）<input aria-label="关联 INCAR 参数" value={tags} onChange={e=>setTags(e.target.value)} placeholder="例如 NCORE LCALCPOL；用空格或逗号分隔"/></label><button type="button" className="button primary" onClick={()=>void save()}>{busy?'正在保存…':'保存问题备注'}</button></fieldset>{message&&<p role="status">{message}</p>}</div>;
}
export default function ProblemNotesButton({id,kind='attempts',excerpt}:{id:string;kind?:'attempts';excerpt?:()=>{file:string;location:string;text:string}|undefined}){
 const [record,setRecord]=useState<ProblemRecord|null>(null),[open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const identity=kind+id,active=useRef(identity);active.current=identity;
 useEffect(()=>{setRecord(null);setOpen(false);setError('');setBusy(false);},[identity]);
 const show=async()=>{if(open&&!excerpt){setOpen(false);return;}const context=identity;setBusy(true);setError('');try{const data=await requestJson('/api/problem-records/collect',{source_kind:kind,source_id:id,excerpt:excerpt?.()});if(active.current===context){setRecord(data);setOpen(true);}}catch(e){if(active.current===context)setError(errorMessage(e));}finally{if(active.current===context)setBusy(false);}};
 return <div className="problem-note-entry"><button type="button" className="button" disabled={busy} onClick={()=>void show()}>{busy?'正在读取备注…':excerpt?'记录所选日志片段':open?'收起问题备注':'记录问题 / 编辑备注'}</button>{record&&open&&<><button type="button" className="text-button" onClick={()=>openProblem(record.id)}>在问题记录中查看</button>{record.excerpts.length>0&&<p>已保留 {record.excerpts.length} 个日志片段</p>}<ProblemEditor key={record.id} record={record} onSaved={setRecord}/></>}{error&&<p role="alert">{error}</p>}</div>;
}

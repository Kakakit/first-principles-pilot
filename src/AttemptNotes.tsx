import {useEffect,useState} from 'react';
export default function AttemptNotes({id}:{id:string}){
 const [notes,setNotes]=useState<Record<string,string>>({}),[message,setMessage]=useState('');
 useEffect(()=>{setMessage('');fetch(`/api/execution/${id}/notes`).then(async r=>{if(!r.ok)throw new Error('备注读取失败');setNotes(await r.json());}).catch(e=>setMessage(e.message));},[id]);
 const save=async()=>{try{const r=await fetch(`/api/execution/${id}/notes`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(notes)});const d=await r.json();if(!r.ok)throw new Error(d.detail);setNotes(d);setMessage('备注已保存；计算证据未改动。');}catch(e){setMessage((e as Error).message);}};
 return <details><summary>故障备注与原因判断</summary>{Object.entries({user_note:'用户备注',suspected_cause:'疑似原因',confirmed_cause:'已确认原因',confirmation_evidence:'确认依据（日志位置、复现或对照结果）'}).map(([k,label])=><label key={k}>{label}<textarea maxLength={4000} value={notes[k]||''} onChange={e=>setNotes({...notes,[k]:e.target.value})}/></label>)}<button className="button" onClick={()=>void save()}>保存故障备注</button>{message&&<p role="status">{message}</p>}</details>;
}

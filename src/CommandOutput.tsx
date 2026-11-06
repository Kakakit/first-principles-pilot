import {useEffect,useRef,useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
import type {ScriptRecord} from './scriptChoices';
import {useRequestScope} from './useRequestScope';
import {appendLog} from './commandLogs';
type Output={id:string;state:string;exit_code?:number;stdout:string;stderr:string;stdout_offset?:number;stderr_offset?:number;stdout_more?:boolean;stderr_more?:boolean;note?:string;runs?:{id:string;name:string}[]};
type Props={attemptId:string;alias:string;direct?:boolean;attemptState?:string;onChanged?:()=>void|Promise<void>};
type DirectOutput=Output&{stdout_token?:string;stderr_token?:string;stdout_reset?:boolean;stderr_reset?:boolean;process_pid?:number;transport_error?:boolean;stop?:{state:string}};
const done=['EXITED','CANCELLED','LOST','START_FAILED'];
const labels:Record<string,string>={STARTING:'正在启动',RUNNING:'运行中',EXITING:'收尾中',EXITED:'执行结束',CANCELLED:'已停止',LOST:'运行中断',START_FAILED:'启动失败',UNKNOWN:'状态待核查'};

export default function CommandOutput(props:Props){
 if(props.direct)return <DirectOutputPanel key={props.attemptId} {...props}/>;
 return <OpaqueOutputPanel key={props.attemptId} {...props}/>;
}

function DirectOutputPanel({attemptId,attemptState,onChanged}:Props){
 const requests=useRequestScope(attemptId);
 const panel=useRef<HTMLElement|null>(null);
 const [output,setOutput]=useState<DirectOutput|null>(null),[warning,setWarning]=useState(''),[revision,setRevision]=useState(0);
 const current=useRef<DirectOutput|null>(null);
 const observed=useRef({attemptState,onChanged});
 observed.current={attemptState,onChanged};
 useEffect(()=>{
  let active=true,timer:number|undefined,fetching=false,tail=true,inView=typeof IntersectionObserver==='undefined';
  async function read(){
   if(!active||document.hidden||!inView||fetching)return;
   window.clearTimeout(timer);
   fetching=true;
   const previous=current.current;
   const query=new URLSearchParams({tail:String(tail),stdout_offset:String(previous?.stdout_offset??0),stderr_offset:String(previous?.stderr_offset??0),stdout_token:previous?.stdout_token||'',stderr_token:previous?.stderr_token||''});
   const request=requests.start('logs');
   try{
    const value:DirectOutput=await requestJson('/api/execution/'+attemptId+'/command-output?'+query,undefined,undefined,{signal:request.signal});
    if(!active||!request.current()||value.id!==attemptId)return;
    if(value.transport_error){setWarning(value.note||'日志连接暂时中断');return;}
    const next={...value,stdout:appendLog(previous?.stdout||'',value.stdout||'',tail||Boolean(value.stdout_reset)),
                         stderr:appendLog(previous?.stderr||'',value.stderr||'',tail||Boolean(value.stderr_reset))};
    tail=false;current.current=next;setOutput(next);setWarning('');
    const state=observed.current.attemptState;
    const mapped=value.state==='EXITED'?(value.exit_code===0?'COMPLETED':'FAILED'):
      value.state==='LOST'||value.state==='START_FAILED'?'FAILED':value.state;
    if((done.includes(value.state)||mapped==='RUNNING'||mapped==='EXITING')&&mapped!==state&&value.state!==previous?.state){
     void observed.current.onChanged?.();
    }
   }catch(e){if(active&&request.current())setWarning(errorMessage(e));}
   finally{
    fetching=false;
    const latest=current.current;
    if(active&&!document.hidden&&inView&&(tail||!latest||!done.includes(latest.state)||latest.stdout_more||latest.stderr_more)){
     timer=window.setTimeout(read,latest?.stdout_more||latest?.stderr_more?250:3000);
    }
   }
  }
  const visibility=()=>{if(!document.hidden)void read();else window.clearTimeout(timer);};
  document.addEventListener('visibilitychange',visibility);
  const observer=typeof IntersectionObserver==='undefined'?null:new IntersectionObserver(entries=>{
   inView=entries.some(entry=>entry.isIntersecting);
   if(inView)void read();else window.clearTimeout(timer);
  });
  if(panel.current)observer?.observe(panel.current);
  void read();
  return ()=>{active=false;observer?.disconnect();window.clearTimeout(timer);requests.cancel('logs');document.removeEventListener('visibilitychange',visibility);};
 },[attemptId,requests,revision]);
 return <section className="input-submit-card" ref={panel}>
  <h4>运行日志</h4>
  <p>显示输出末尾，每路最多 256 KiB。远端保留完整日志。</p>
  <button className="button" onClick={()=>setRevision(value=>value+1)}>刷新日志末尾</button>
  {warning&&<p role="alert">{warning}</p>}
  {output&&<>
   <p>{labels[output.state]||output.state}{output.process_pid?' · PID '+output.process_pid:''}{typeof output.exit_code==='number'?' · 退出码 '+output.exit_code:''}{output.stop?.state==='ACCEPTED'?' · 正在停止':''}</p>
   {output.note&&<p>{output.note}</p>}
   <details open><summary>stdout</summary><pre className="input-text">{output.stdout||'（暂无输出）'}</pre></details>
   <details open={Boolean(output.stderr)}><summary>stderr</summary><pre className="input-text">{output.stderr||'（暂无输出）'}</pre></details>
  </>}
 </section>;
}

function OpaqueOutputPanel({attemptId,alias,onChanged}:Props){
 const [output,setOutput]=useState<Output|null>(null),[runId,setRunId]=useState(''),[presets,setPresets]=useState<ScriptRecord[]>([]),[selected,setSelected]=useState(''),[proposal,setProposal]=useState<ScriptRecord|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 useEffect(()=>{setOutput(null);setRunId('');setProposal(null);setError('');},[attemptId]);
 useEffect(()=>{void requestJson('/api/job-templates').then(v=>setPresets(v.filter((p:ScriptRecord)=>p.alias===alias&&p.scheduler==='custom'))).catch(e=>setError(errorMessage(e)));},[alias]);
 async function read(append=false,id=runId){setBusy(true);setError('');try{const q=new URLSearchParams({...(id?{run_id:id}:{}),stdout_offset:String(append?output?.stdout_offset||0:0),stderr_offset:String(append?output?.stderr_offset||0:0)});const value:Output=await requestJson('/api/execution/'+attemptId+'/command-output?'+q);setOutput(append?{...value,stdout:(output?.stdout||'')+value.stdout,stderr:(output?.stderr||'')+value.stderr}:value);onChanged?.();}catch(e){setError(errorMessage(e));}finally{setBusy(false);}}
 async function execute(){if(!proposal)return;setBusy(true);setError('');try{const result=await requestJson('/api/execution/'+attemptId+'/commands',{template_id:proposal.id,confirm:true});setProposal(null);setRunId(result.id);setOutput(null);await read(false,result.id);}catch(e){setProposal(null);setError(errorMessage(e));}finally{setBusy(false);}}
 return <section className="input-submit-card"><h4>原始命令输出</h4><p>只展示这条命令的输出和退出码，不推断其它队列的计算状态。连接中断不会自动重新执行。</p><div className="button-row"><button className="button" disabled={busy} onClick={()=>void read()}>读取／刷新日志</button>{output?.runs&&<select aria-label="命令执行记录" value={runId||attemptId} disabled={busy} onChange={e=>{setRunId(e.target.value);setOutput(null);}}>{output.runs.map(r=><option key={r.id} value={r.id}>{r.name}</option>)}</select>}</div>{output&&<><p>{output.state==='EXITED'?'命令已结束':output.state==='RUNNING'?'命令运行中':output.state==='STARTING'?'命令启动中':'回执待核查'}{output.exit_code!==undefined?' · 退出码 '+output.exit_code:''}</p>{output.note&&<p>{output.note}</p>}<details open><summary>stdout</summary><pre className="input-text">{output.stdout||'（暂无输出）'}</pre></details><details open={Boolean(output.stderr)}><summary>stderr</summary><pre className="input-text">{output.stderr||'（暂无输出）'}</pre></details>{(output.stdout_more||output.stderr_more)&&<button className="button" disabled={busy} onClick={()=>void read(true)}>继续读取下一段</button>}</>}<label>在同一目录运行已保存命令<select disabled={busy} value={selected} onChange={e=>setSelected(e.target.value)}><option value="">选择查询、取消或其它命令</option>{presets.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label><button className="button" disabled={busy||!selected} onClick={()=>void requestJson('/api/job-templates/'+selected).then(setProposal).catch(e=>setError(errorMessage(e)))}>预览运行命令</button>{proposal&&<div role="dialog" aria-label="确认自定义命令"><strong>{proposal.name}</strong><pre className="input-text">{proposal.original_text}</pre><button className="button" disabled={busy} onClick={()=>setProposal(null)}>取消</button><button className="button primary" disabled={busy} onClick={()=>void execute()}>确认执行一次</button></div>}{error&&<p role="alert">{error}</p>}</section>;
}

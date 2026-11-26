import {useState} from 'react';
import {errorMessage,requestJson} from './apiClient';

const emptyHost={host:'',user:'',port:'22'};
type Host=typeof emptyHost;
function Address({value,onChange}:{value:Host;onChange:(value:Host)=>void}){
 return <div className="submit-grid">{(['host','user','port'] as const).map(key=><label key={key}>{{host:'服务器地址',user:'用户名',port:'端口'}[key]}<input required value={value[key]} placeholder={key==='host'?'IP 或主机名':undefined} onChange={e=>onChange({...value,[key]:e.target.value})}/></label>)}</div>;
}
export default function NewSshConnection({aliases,onStarted}:{aliases:string[];onStarted:(alias:string)=>void}){
 const [target,setTarget]=useState(emptyHost),[jump,setJump]=useState(emptyHost),[via,setVia]=useState(false);
 const [jumpAlias,setJumpAlias]=useState(''),[name,setName]=useState(''),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
 const suggested=target.host?'fp-'+target.host.replace(/[^A-Za-z0-9.-]/g,'-').slice(0,48):'';
 const alias=name.trim()||suggested;
 async function start(){
  setBusy(true);setMessage('');
  try{await requestJson('/api/sites/first-login',{alias,target,...(via?{jump:jumpAlias||jump}:{})});
   setMessage('请在打开的系统终端完成认证，然后回到这里验证连接。');onStarted(alias);
  }catch(e){setMessage(errorMessage(e));}finally{setBusy(false);}
 }
 return <div><Address value={target} onChange={setTarget}/>
 <label>连接名称<input placeholder={suggested||'根据地址生成'} value={name} onChange={e=>setName(e.target.value)}/></label>
 <label><input type="checkbox" checked={via} onChange={e=>setVia(e.target.checked)}/>需要经过跳板机</label>
 {via&&<fieldset><legend>跳板机</legend>{aliases.length>0&&<label>连接<select value={jumpAlias} onChange={e=>setJumpAlias(e.target.value)}><option value="">填写跳板机地址</option>{aliases.map(a=><option key={a}>{a}</option>)}</select></label>}{!jumpAlias&&<Address value={jump} onChange={setJump}/>}</fieldset>}
 <p>密码和验证码在系统终端输入。首次登录时核对主机指纹，登录成功后可选择登记公钥。</p>
 <button className="button" disabled={busy||!target.host||!target.user||via&&!jumpAlias&&(!jump.host||!jump.user)} onClick={()=>void start()}>打开首次登录助手</button>{message&&<p role="status">{message}</p>}</div>;
}

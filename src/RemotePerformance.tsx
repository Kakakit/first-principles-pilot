import {useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
type Action={action:string;seconds:number;remote_requests:number;at:number};
export default function RemotePerformance(){
 const [rows,setRows]=useState<Action[]>([]),[message,setMessage]=useState('');
 const load=async()=>{try{const value=await requestJson('/api/remote-performance');setRows(value.actions.slice(-20).reverse());}catch(e){setMessage(errorMessage(e));}};
 const clear=async()=>{try{const value=await requestJson('/api/remote-performance/clear-cache',{});setMessage(value.message);}catch(e){setMessage(errorMessage(e));}};
 return <section className="input-submit-card"><h3>远端缓存与交互耗时</h3><p>已完成来源保存在本机。需要核对服务器变化时，在来源或绘图页面点击刷新。</p><button className="button" onClick={()=>void clear()}>清除可重建缓存</button><button className="button" onClick={()=>void load()}>查看最近交互耗时</button>{message&&<p role="status">{message}</p>}{rows.length>0&&<div className="atoms-scroll"><table><thead><tr><th>操作</th><th>总耗时</th><th>远端请求</th></tr></thead><tbody>{rows.map((r,i)=><tr key={i}><td>{r.action}</td><td>{r.seconds.toFixed(3)} 秒</td><td>{r.remote_requests}</td></tr>)}</tbody></table></div>}</section>;
}

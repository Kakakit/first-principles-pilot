import {useState} from 'react';
import type {Analysis} from './extensionModel';
import {remoteOutputPath} from './extensionModel';

export function RemotePath({label,path}:{label:string;path:string}){
 const [message,setMessage]=useState('');
 const copy=async()=>{try{await navigator.clipboard.writeText(path);setMessage('已复制');}catch{setMessage('复制未成功，请选中路径复制');}};
 return <div className="extension-path-row"><span className="extension-path-label">{label}</span><code>{path}</code><button type="button" className="text-button" aria-label={'复制'+label} onClick={()=>void copy()}>复制路径</button><span className="extension-path-feedback" role="status">{message}</span></div>;
}

export default function ExtensionRemotePaths({record}:{record:Analysis}){
 if(!record.remote_dir&&record.location==='local')return null;
 const root=record.remote_dir?.trim();
 return <section className="extension-remote-storage" aria-label="远端结果保存位置"><strong>远端结果保存位置</strong><p>服务器：<b>{record.runtime?.alias||'未记录'}</b>{record.job_id&&` · job ID ${record.job_id}`}</p>{root?<><RemotePath label="分析目录" path={root}/><RemotePath label="结果目录" path={remoteOutputPath(root,'')!}/>{record.presentation?.status==='READY'&&record.presentation.location==='source'&&<RemotePath label="交互展示文件目录" path={root.replace(/\/+$/,'')+'/presentations/'+record.presentation.id+'/outputs'}/>}{record.outputs.some(item=>!item.available)&&<div className="extension-remote-only"><strong>以下结果未回传本机，请到服务器读取</strong>{record.outputs.filter(item=>!item.available).map(item=>{const path=remoteOutputPath(root,item.path);return path?<RemotePath key={item.path} label={item.label+' · '+(item.size_bytes/1024/1024).toFixed(1)+' MiB'} path={path}/>:<p key={item.path}>{item.label}：完整远端文件路径未记录</p>;})}</div>}</>:<p>此记录尚未保存远端目录，无法确定完整路径。</p>}</section>;
}

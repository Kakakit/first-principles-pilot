import {useEffect,useRef,useState} from 'react';
import {readJson,errorMessage} from './apiClient';
import type {Analysis} from './extensionModel';
const protocol='fpilot.extension-view.v1';
export default function ExtensionPresentation({record,onFailure,dataBase,onChanged}:{record:Analysis;onFailure:()=>void;dataBase?:string;onChanged?:()=>Promise<void>}){
 const frame=useRef<HTMLIFrameElement>(null),[html,setHtml]=useState(''),[height,setHeight]=useState(1000),[error,setError]=useState('');
 const view=record.presentation!;
 const base=`${dataBase||'/api/extension-host/analyses/'+record.id}/presentation/${view.id}`;
 const file=(name:string)=>base+'/files/'+name.split('/').map(encodeURIComponent).join('/');
 useEffect(()=>{let live=true;setHtml('');setError('');void fetch(file(view.manifest!.entry_html)).then(async response=>{if(!response.ok)throw Error('展示入口读取失败');return response.text();}).then(value=>{if(live)setHtml(value.replace(/<head>/i,`<head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'">`));}).catch(e=>{if(live){setError(errorMessage(e));onFailure();}});return()=>{live=false;};},[record.id,view.id,dataBase]);
 useEffect(()=>{
  const listener=async(event:MessageEvent)=>{
   if(event.source!==frame.current?.contentWindow||event.data?.protocol!==protocol)return;
   const message=event.data;
   if(message.kind==='resize'){if(Number.isFinite(message.height))setHeight(Math.min(5000,Math.max(500,message.height+8)));return;}
   if(message.kind==='failed'){setError(String(message.error||'扩展展示失败'));onFailure();return;}
   if(!['read-file','read-state','save-state','download','refresh-view'].includes(message.kind)||typeof message.id!=='string')return;
   const reply=(value:unknown,error?:string)=>frame.current?.contentWindow?.postMessage({protocol,kind:'response',id:message.id,value,error},'*');
   try{
    if(message.kind==='refresh-view'){
     if(dataBase||!onChanged)throw Error('该只读展示不能重新整理；请到原分析记录操作');
     reply(await readJson(await fetch('/api/extension-host/analyses/'+record.id+'/presentation',{method:'POST'})));
     await onChanged();
    }else if(message.kind==='download'){
     if(typeof message.name!=='string'||!message.name||message.name.length>120||/[\\/\x00-\x1f]/.test(message.name)||typeof message.content!=='string'||message.content.length>64*1024*1024||!['text/csv;charset=utf-8','application/json','text/plain','image/png'].includes(message.media_type)||!['text','base64'].includes(message.encoding))throw Error('下载内容或文件名无效');
     const data=message.encoding==='base64'?Uint8Array.from(atob(message.content),c=>c.charCodeAt(0)):message.content;
     const url=URL.createObjectURL(new Blob([data],{type:message.media_type})),anchor=document.createElement('a');anchor.href=url;anchor.download=message.name;document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),5000);reply({started:true});
    }else if(message.kind==='read-file'){
     const asset=view.manifest!.assets.find(a=>a.path===message.path);
     if(!asset||asset.media_type!=='application/json')throw Error('数据文件未在展示清单中声明');
     reply(await readJson(await fetch(file(asset.path))));
    }else if(message.kind==='read-state')reply(await readJson(await fetch(base+'/state')));
    else reply(await readJson(await fetch(base+'/state',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(message.value)})));
   }catch(e){reply(null,errorMessage(e));}
  };
  window.addEventListener('message',listener);return()=>window.removeEventListener('message',listener);
 },[record.id,view.id,dataBase,onChanged]);
 return <div className="extension-presentation"><p className="extension-notice">扩展内置交互展示 · 版本 {view.version} · 原运行配置与产物保留</p>{error&&<p className="handoff-warning" role="alert">{error}；可查看原始结果或重新生成展示。</p>}{!html&&!error&&<p role="status">正在载入扩展展示…</p>}{html&&<iframe ref={frame} title={record.manifest.name+' · 交互结果'} srcDoc={html} sandbox="allow-scripts allow-downloads" style={{width:'100%',height,border:0}}/>}</div>;
}

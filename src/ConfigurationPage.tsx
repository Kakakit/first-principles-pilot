import {useCallback,useId,useEffect,useLayoutEffect,useRef,useState} from 'react';
import InputPreviewDivider from './InputPreviewDivider';
import {useIssueField} from './useActionCheck';
import type {ReactNode} from 'react';

/** One scroll container keeps the section index and review controls in view. */
export default function ConfigurationPage({part,children,files=false}:{part?:string;files?:boolean;children:(footer:HTMLDivElement|null,preview:HTMLDivElement|null)=>ReactNode}){
 const previewId=useId();
 const [preview,setPreview]=useState<HTMLDivElement|null>(null);
 const [showFiles,setShowFiles]=useState(true);
 useIssueField(field=>{if(['parameter.incar','input.constraints','input.poscar','input.kpoints-edit'].includes(field))setShowFiles(true);});
 const body=useRef<HTMLDivElement>(null);
 const [footer,setFooter]=useState<HTMLDivElement|null>(null);
 const [sections,setSections]=useState<string[]>([]);
 const [active,setActive]=useState('');
 const jump=useCallback((label:string,smooth:boolean)=>{
  const panel=body.current;
  const section=Array.from(panel?.querySelectorAll<HTMLElement>('[data-config-section]')||[]).find(el=>el.dataset.configSection===label);
  if(panel&&section)panel.scrollTo({top:panel.scrollTop+section.getBoundingClientRect().top-panel.getBoundingClientRect().top-8,
   behavior:smooth&&!matchMedia('(prefers-reduced-motion: reduce)').matches?'smooth':'instant'});
 },[]);
 useLayoutEffect(()=>{
  const panel=body.current!;
  let anchors:HTMLElement[]=[];
  const update=()=>{
   const top=panel.getBoundingClientRect().top+12;
   const visible=anchors.filter(el=>el.getBoundingClientRect().top<=top);
   const last=panel.scrollTop>0&&panel.scrollTop+panel.clientHeight>=panel.scrollHeight-2;
   setActive((last?anchors.at(-1):visible.at(-1)||anchors[0])?.dataset.configSection||'');
  };
  const refresh=()=>{
   anchors=Array.from(panel.querySelectorAll<HTMLElement>('[data-config-section]'));
   const labels=anchors.map(el=>el.dataset.configSection!);
   setSections(old=>old.join('|')===labels.join('|')?old:labels);
   update();
  };
  refresh();
  const observer=new MutationObserver(refresh);
  observer.observe(panel,{childList:true,subtree:true});
  panel.addEventListener('scroll',update,{passive:true});
  return ()=>{observer.disconnect();panel.removeEventListener('scroll',update);};
 },[]);
 useEffect(()=>{
  const label=part==='files'?'k 点与赝势':part==='source'?'来源与重启':part==='physics'?'电子设置':'';
  if(label){const frame=requestAnimationFrame(()=>jump(label,false));return ()=>cancelAnimationFrame(frame);}
 },[part,jump]);
 const nav=useRef<HTMLElement>(null);
 useEffect(()=>{
  const bar=nav.current,button=bar?.querySelector<HTMLElement>('[aria-current]');
  if(!bar||!button)return;
  const rect=button.getBoundingClientRect(),bounds=bar.getBoundingClientRect();
  if(rect.left<bounds.left)bar.scrollLeft+=rect.left-bounds.left;
  if(rect.right>bounds.right)bar.scrollLeft+=rect.right-bounds.right;
 },[active]);
 return <div className="vasp-configuration">
  <nav ref={nav} className="configuration-parts" aria-label="计算配置区域">{files&&<button aria-expanded={showFiles} onClick={()=>setShowFiles(!showFiles)}>输入文件</button>}{sections.map(label=><button key={label} type="button" className={active===label?'active':''} aria-current={active===label?'location':undefined} onClick={()=>jump(label,true)}>{label}</button>)}</nav>
  <div className="configuration-split">
  <div ref={body} data-navigation-scroll className="configuration-section">{children(footer,preview)}</div>
  {files&&showFiles&&<InputPreviewDivider controls={previewId}/>}
  {files&&<div id={previewId} ref={setPreview} hidden={!showFiles} className="configuration-file-pane"/>}</div>
  <div ref={setFooter} className="configuration-review-bar"/>
 </div>;
}

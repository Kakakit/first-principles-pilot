import {useIssueField} from './useActionCheck';
import {useRef,useState} from 'react';
import {definitions,knownTags} from './parameters';
import type {ParameterLayer} from './parameters';
import type {Draft} from './InputPanel';
import type {StructureDocument} from './structure';

export type FileReport={
 poscar_review?:{before:number;after:number;text:string};partial?:boolean;incar_notes?:string[];files:Record<string,string>;errors:string[];sampling_mode?:string;sampling_note?:string;reference_geometry_sha256?:string;
 species?:string[];potcar_source?:string;potcar?:{records:{element:string;titel:string;enmax?:number}[]};
};
type Props={report:FileReport|null;busy:boolean;error:string;layer:ParameterLayer;structure?:StructureDocument;draft:Draft;
 onLayer:(layer:ParameterLayer)=>void;onDraft:(update:(draft:Draft)=>Draft)=>void;bands:boolean};

function constraintRows(structure:StructureDocument, poscar?:string){
 if(poscar){
  const lines=poscar.split(/\r?\n/);
  if(/^s/i.test(lines[7]?.trim()||''))return structure.atoms.map((_,i)=>lines[9+i].trim().split(/\s+/).slice(3,6).join('').toUpperCase());
  return structure.atoms.map(()=>'TTT');
 }
 return structure.atoms.map(a=>(a.selective||[true,true,true]).map(f=>f?'T':'F').join(''));
}
function Constraints({structure,report,draft,onDraft}:Pick<Props,'report'|'draft'|'onDraft'>&{structure:StructureDocument}){
 const [element,setElement]=useState('*'),[range,setRange]=useState(''),[axes,setAxes]=useState([false,false,false]),[error,setError]=useState('');
 const base=report?.reference_geometry_sha256;
 function apply(){
  if(!base)return;
  const rows=constraintRows(structure,draft.poscar_text);
  if(draft.selective_base===base&&draft.selective_flags?.length===rows.length*3)
   rows.forEach((_,i)=>rows[i]=draft.selective_flags!.slice(3*i,3*i+3));
  const selected=new Set<number>();
  for(const token of range.trim()?range.trim().split(/[,，\s]+/):[]){
   const m=token.match(/^(\d+)(?:-(\d+))?$/),a=Number(m?.[1]),b=Number(m?.[2]??a);
   if(!m||a<1||b>rows.length||a>b){setError('请输入有效的原子编号，例如 1-4, 7。');return;}
   for(let i=a;i<=b;i++)selected.add(i);
  }
  let count=0;
  for(const [i,atom] of structure.atoms.entries())if((!selected.size||selected.has(i+1))&&(element==='*'||atom.element===element)){
   rows[i]=axes.map(movable=>movable?'T':'F').join('');count++;
  }
  if(!count){setError('该元素与编号范围没有共同原子。');return;}
  setError('');onDraft(d=>({...d,selective_flags:rows.join(''),selective_base:base}));
 }
 return <details data-action-field="input.constraints"><summary>设置本节点的原子约束</summary>
 <label>元素<select value={element} onChange={e=>setElement(e.target.value)}><option value="*">全部元素</option>{[...new Set(structure.atoms.map(a=>a.element))].map(s=><option key={s}>{s}</option>)}</select></label>
 <label>原子编号<input value={range} onChange={e=>setRange(e.target.value)} placeholder="留空选全部；例如 1-4, 7"/></label>
 <fieldset><legend>允许移动的晶格方向</legend>{['a','b','c'].map((v,i)=><label key={v}><input type="checkbox" checked={axes[i]} onChange={e=>setAxes(axes.map((a,j)=>i===j?e.target.checked:a))}/>{v}</label>)}</fieldset>
 <button className="button" disabled={!base} onClick={apply}>应用到所选原子</button>{draft.selective_flags&&<button className="text-button" onClick={()=>onDraft(({selective_flags,selective_base,...rest})=>rest)}>恢复来源约束</button>}{error&&<p role="alert">{error}</p>}</details>;
}
export function meshFields(text:string,bands:boolean):Partial<Draft>{
 const lines=text.trim().split(/\r?\n/).map(l=>l.trim()).filter(Boolean);
 if(/^[1-9]\d*$/.test(lines[1]||'')&&/^[rck]/i.test(lines[2]||'')){
  const points=lines.slice(3);
  if(points.length!==Number(lines[1])||points.some(line=>{
   const fields=line.split('!')[0].trim().split(/\s+/);
   return fields.length!==4||fields.some(x=>!/^[-+]?(?:\d+\.?\d*|\.\d+)(?:[Ee][-+]?\d+)?$/.test(x)||!Number.isFinite(Number(x)));
  }))throw Error('显式点表需要与声明数量一致的行，每行三个坐标和一个权重。');
  return {weighted_kpoints:text,sampling:'explicit',kmesh_info:undefined};
 }
 if(bands){
  const count=Number(lines[1]);
  if(!Number.isInteger(count)||count<2||count>10000||!/^l/i.test(lines[2]||'')||!/^r/i.test(lines[3]||''))throw Error('需要 Line-mode、Reciprocal 和每段 2–10000 个点。');
  const path=lines.slice(4).map(line=>{
   const [coords,label]=line.split(/\s*!\s*/);
   const parts=coords.trim().split(/\s+/);
   if(parts.length!==3||parts.some(x=>!Number.isFinite(Number(x)))||label&&/\s/.test(label))throw Error('每个端点需三个倒空间坐标，可在 ! 后写一个标签。');
   return [...parts,...(label?[label]:[])].join(' ');
  });
  if(path.length<2||path.length>200||path.length%2)throw Error('能带路径需要成对端点，最多 200 个。');
  return {weighted_kpoints:undefined,line_points:String(count),path:path.join('\n')};
 }
 const grid=(lines[3]||'').split(/\s+/),shift=(lines[4]??'0 0 0').split(/\s+/);
 if(lines.length<4||lines.length>5||lines[1]!=='0'||!/^([gm])/i.test(lines[2])||grid.length!==3||grid.some(x=>!/^\d+$/.test(x)||Number(x)<1||Number(x)>1000)||shift.length!==3||shift.some(x=>!Number.isFinite(Number(x))))throw Error('需要 Gamma 或 Monkhorst-Pack 网格、三个正整数及可选的三分量位移。');
 return {weighted_kpoints:undefined,grid:grid.join(' '),center:/^g/i.test(lines[2])?'Gamma':'Monkhorst-Pack',mesh_shift:shift.join(' '),kmesh_info:undefined};
}
export default function InputFilePreview({report,busy,error,layer,onLayer,structure,draft,onDraft,bands}:Props){
 const [file,setFile]=useState('INCAR'),[query,setQuery]=useState(''),[message,setMessage]=useState('');
 useIssueField(field=>{if(field==="parameter.incar")setFile("INCAR");if(field==="input.constraints"||field==="input.poscar")setFile("POSCAR");if(field==="input.kpoints-edit")setFile("KPOINTS");});
 const editor=useRef<HTMLTextAreaElement>(null),upload=useRef<HTMLInputElement>(null);
 const ktext=draft.kpoints_edit??null;
 const setKtext=(text:string|null)=>onDraft(d=>({...d,kpoints_edit:text??undefined}));
 const full=layer.incarMode==='complete',files=report?.files||{};
 const text=full?layer.completeIncarText??'':files.INCAR??'';
 const write=(value:string,name?:string)=>{
  if(new TextEncoder().encode(value).length>262144||value.includes('\0')){setMessage('请输入 256 KiB 以内的普通文本。');return;}
  setMessage('');onLayer({...layer,incarMode:'complete',completeIncarText:value,completeIncarFilename:name??layer.completeIncarFilename});
 };
 const [searchOpen,setSearchOpen]=useState(false);
 const tagQuery=query.trim().toUpperCase();
 const documentTags=Array.from(text.matchAll(/(?:^|[;\n])\s*([A-Za-z][A-Za-z_0-9/]*)\s*=/g),m=>m[1].toUpperCase());
 const matches=tagQuery?[...new Set([...documentTags,...knownTags,...definitions.map(d=>d.tag)])].filter(tag=>tag.includes(tagQuery)||definitions.some(d=>d.tag===tag&&d.label.includes(query.trim()))).sort((a,b)=>Number(b.startsWith(tagQuery))-Number(a.startsWith(tagQuery))||a.localeCompare(b)):[...new Set([...documentTags,...definitions.map(d=>d.tag)])];
 const find=(selected=query)=>{const tag=selected.trim().toUpperCase();if(!/^[A-Z][A-Z_0-9/]*$/.test(tag))return;
  const match=new RegExp('(?:^|[;\\n])\\s*('+tag+')\\s*=','i').exec(text);
  if(!match){setMessage('当前文件中未找到 '+tag);return;}
  const pos=match.index+match[0].toUpperCase().lastIndexOf(tag);
  editor.current?.focus();editor.current?.setSelectionRange(pos,pos+tag.length);
  if(editor.current)editor.current.scrollTop=Math.max(0,(text.slice(0,pos).split('\n').length-3)*parseFloat(getComputedStyle(editor.current).lineHeight));setMessage('');
 };
 return <section className="vasp-file-preview" aria-label="输入文件预览">
 <nav aria-label="输入文件">{['INCAR','POSCAR','KPOINTS','POTCAR'].map(f=><button type="button" aria-pressed={file===f} key={f} onClick={()=>{setFile(f);setMessage('');}}>{f}</button>)}</nav>
 {file==='INCAR'?<><div className="file-tools"><input aria-label="搜索文件中的 tag" type="search" placeholder="搜索 tag 或名称片段" onFocus={()=>setSearchOpen(true)} value={query} onChange={e=>{setQuery(e.target.value);setSearchOpen(true);}} onKeyDown={e=>{if(e.key==='Escape')setSearchOpen(false);if(e.key==='Enter'&&!e.nativeEvent.isComposing){setSearchOpen(false);find();}}}/><button className="text-button" onClick={()=>find()}>定位</button><button className="text-button" onClick={()=>upload.current?.click()}>导入 INCAR</button><input hidden ref={upload} type="file" onChange={async e=>{const f=e.target.files?.[0];e.target.value='';if(!f)return;try{if(f.size>262144)throw Error('文件超过 256 KiB。');const raw=await f.text();if(!raw.trim())throw Error('文件为空。');write(raw,f.name);}catch(err){setMessage(String(err));}}}/></div>
 {searchOpen&&<div className="file-tag-matches" onBlur={e=>{if(e.relatedTarget&&!e.currentTarget.contains(e.relatedTarget))setSearchOpen(false);}} aria-label="匹配的 INCAR 参数">{matches.length===0?<small>没有匹配的已知 tag</small>:matches.map(tag=>{
  const present=documentTags.includes(tag);
  return <button type="button" className="text-button" key={tag} disabled={!full&&busy} onClick={()=>{
   setQuery(tag);setSearchOpen(false);
   if(present){find(tag);return;}
   const next=text+(text&&!text.endsWith('\n')?'\n':'')+tag+' = ';
   write(next);requestAnimationFrame(()=>{editor.current?.focus();editor.current?.setSelectionRange(next.length,next.length);});
  }}>{tag}<small>{present?'定位':'插入'}</small></button>;
 })}</div>}
 <textarea onFocus={()=>setSearchOpen(false)} data-action-field="parameter.incar" ref={editor} aria-label="INCAR 文件内容" spellCheck={false} readOnly={!full&&busy} value={text} placeholder="可导入或填写本节点的 INCAR 原文" onChange={e=>write(e.target.value)}/>
 {full&&report?.incar_notes?.map(note=><small key={note}>{note}</small>)}<small>{full?'节点原文 · 独立于共享参数':report?.partial?'参数草稿 · 结构和来源就绪后补全文件':'表单生成 · 编辑此处即采用节点原文'}</small>{full&&<button className="text-button" onClick={()=>onLayer({...layer,incarMode:'overlay'})}>切回表单生成</button>}{!full&&layer.completeIncarText!==undefined&&<button className="text-button" onClick={()=>onLayer({...layer,incarMode:'complete'})}>恢复上次文件原文</button>}</>:
 file==='POSCAR'?<>
 {files['POSCAR.reference']&&<p>{draft.poscar_text?'已采用节点结构。':'当前显示参考结构；采用节点原文后，本次计算使用所填坐标。'}</p>}
 <textarea aria-label="POSCAR 文件内容" data-action-field="input.poscar" spellCheck={false}
  value={draft.poscar_edit??files.POSCAR??files['POSCAR.reference']??draft.poscar_text??''}
  readOnly={!structure||!report?.reference_geometry_sha256}
  onChange={e=>{const value=e.target.value;if(new TextEncoder().encode(value).length>262144){setMessage('POSCAR 最大 256 KiB。');return;}
   onDraft(d=>({...d,poscar_edit:value,poscar_edit_base:d.poscar_edit_base??d.poscar_base??report?.reference_geometry_sha256}));}}/>
 {draft.poscar_edit!==undefined&&<div><small>原子身份按行保留，请保持原子顺序。</small>{report?.poscar_review?.text===draft.poscar_edit&&<p>晶胞体积 {report.poscar_review.before.toFixed(3)} → {report.poscar_review.after.toFixed(3)} Å³</p>}
 <button className="button" disabled={busy||report?.poscar_review?.text!==draft.poscar_edit} onClick={()=>onDraft(d=>({...d,poscar_text:d.poscar_edit,poscar_base:d.poscar_edit_base,poscar_edit:undefined,poscar_edit_base:undefined,selective_flags:undefined,selective_base:undefined}))}>采用本节点 POSCAR</button>
 <button className="text-button" onClick={()=>onDraft(d=>({...d,poscar_edit:undefined,poscar_edit_base:undefined}))}>撤销此次编辑</button></div>}
 {draft.poscar_text!==undefined&&<button className="text-button" onClick={()=>onDraft(d=>({...d,poscar_text:undefined,poscar_base:undefined,poscar_edit:undefined,poscar_edit_base:undefined,selective_base:undefined,selective_flags:undefined}))}>恢复来源结构</button>}
 {structure&&draft.poscar_edit===undefined&&(!draft.poscar_text||report?.poscar_review?.text===draft.poscar_text)&&<Constraints structure={structure} report={busy?null:report} draft={draft} onDraft={onDraft}/>}</>:
 file==='KPOINTS'?report?.sampling_mode==='spacing'?<p>{report?.sampling_note||'使用 INCAR 中的 KSPACING，本次不写 KPOINTS 文件。'}</p>:<><textarea data-action-field="input.kpoints-edit" aria-label="KPOINTS 文件内容" spellCheck={false} value={ktext??files.KPOINTS??''} onChange={e=>{if(new TextEncoder().encode(e.target.value).length<=262144)setKtext(e.target.value);else setMessage("KPOINTS 最大 256 KiB。");}} placeholder={bands?'输入倒空间 Line-mode 路径':'输入规则网格 KPOINTS'}/>{ktext!==null&&<div><button className="button" onClick={()=>{try{const fields=meshFields(ktext,bands);onDraft(d=>({...d,...fields,kpoints_edit:undefined}));setMessage('');}catch(err){setMessage((err as Error).message);}}}>应用到采样设置</button><button className="text-button" onClick={()=>{setKtext(null);setMessage('');}}>取消编辑</button></div>}</>:
 <>{report?.species&&<p>结构物种顺序　{report.species.join(' → ')}</p>}{report?.potcar?<><p>{report.potcar_source}</p><table><thead><tr><th>元素</th><th>TITEL</th><th>ENMAX / eV</th></tr></thead><tbody>{report.potcar.records.map((r,i)=><tr key={i}><td>{r.element}</td><td>{r.titel}</td><td>{r.enmax??'—'}</td></tr>)}</tbody></table></>:<p>尚未装配 POTCAR</p>}<button className="text-button" onClick={()=>document.querySelector('[data-action-field="input.potcar"]')?.scrollIntoView({block:'center'})}>选择赝势</button></>}
 {message&&<p role="status">{message}</p>}{error&&<p role="alert">{error}</p>}{busy&&<small role="status">正在更新文件…</small>}
 {!busy&&report?.errors.length&&(report.partial||!Object.keys(files).length||file==='POTCAR'||file==='KPOINTS'||file==='POSCAR')?report.errors.filter(e=>report.partial||!Object.keys(files).length||e.includes(file)||file==='POSCAR'&&e.includes('约束')).map((e,i)=><small key={i}>{e}</small>):null}
 </section>;
}

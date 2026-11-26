import InputFilePreview from './InputFilePreview';
import type {FileReport} from './InputFilePreview';
import type {Draft} from './InputPanel';
import {changeAssignment,simpleIncar} from './incarDocument';
import {createPortal} from 'react-dom';
import SharedParameterImpact from './SharedParameterImpact';
import DisclosureLabel from './DisclosureLabel';
import {useIssueField} from './useActionCheck';
import IncarEditor from './IncarEditor';
import {requestJson} from './apiClient';
import {calculationKind} from './scanNodes';
import type {ReactNode} from 'react';
import {usePanelTransition} from './usePanelTransition';
import {Fragment,useEffect,useId,useState} from 'react';
import type {Project} from './model';
import {checkParameters,ionicCriterion,definitions,effectiveParameters,emptyLayer,parameterTables,upstreamStructure,knownTags,managedTags,catalogRevision,targetVaspVersion} from './parameters';
import type {Definition,HubbardRow,ParameterLayer} from './parameters';
import InteractivePlot from './InteractivePlot';
import PyW90PreDosPanel from './PyW90PreDosPanel';
import {simpleProjectionCount} from './projectionCount';

function Criterion({value,onChange}:{value:string|null;onChange:(value:string|null)=>void}) {
 const [emptyMode,setEmptyMode]=useState('force');
 const mode=value===null?emptyMode:value==='0'?'steps':value.startsWith('-')?'force':'energy';
 const magnitude=value===null?'':value.replace(/^[+-]/,'');
 return <div className="criterion"><select aria-label="离子停止依据" value={mode} onChange={e=>{setEmptyMode(e.target.value);onChange(ionicCriterion(e.target.value,mode==='steps'?'':magnitude));}}><option value="force">力的模（负 EDIFFG）</option><option value="energy">能量变化（正 EDIFFG）</option><option value="steps">仅步数（EDIFFG=0）</option></select>{mode!=='steps'&&<label>阈值 / {mode==='force'?'eV/Å':'eV'}<input aria-label="离子停止阈值" inputMode="decimal" value={magnitude} onChange={e=>onChange(ionicCriterion(mode,e.target.value))}/></label>}{value!==null&&value!=="0"&&Number(value)===0&&<p role="alert">阈值必须大于 0；仅按步数停止请显式选择“仅步数”。</p>}</div>;
}
function Input({definition:d,value,onChange}:{definition:Definition;value:string|null;onChange:(value:string|null)=>void}) {
 const listId=useId();
 if(d.allow_positive_integer)return <><input list={listId} aria-label={d.tag} value={value??''} onChange={e=>onChange(e.target.value||null)} placeholder="选择模式或输入正整数阶数"/><datalist id={listId}>{d.choices?.map(([v,label])=><option key={v} value={v}>{label}</option>)}</datalist></>;
 if(d.type==='ediffg')return <Criterion value={value} onChange={onChange}/>;
 if(d.choices?.length===2&&d.choices.some(([v])=>v==='true')&&d.choices.some(([v])=>v==='false')){
  return <div role="group" aria-label={d.tag} tabIndex={-1} className="boolean-parameter">
   {d.choices.map(([v,label])=><button key={v} type="button" title={label} aria-pressed={value===v} onClick={()=>onChange(v)}>{v==='true'?'开启':'关闭'}</button>)}
   <button type="button" aria-pressed={value===null} onClick={()=>onChange(null)}>未设置</button>
   {value!==null&&value!=='true'&&value!=='false'&&<small>{value} · 手动文本</small>}
  </div>;
 }
 if(d.type==='enum')return <select aria-label={d.tag} value={value??''} onChange={e=>onChange(e.target.value||null)}><option value="">未设置</option>{value&&!d.choices!.some(([v])=>v===value)&&<option value={value}>{value} · 手动文本</option>}{d.choices!.map(([v,label])=><option key={v} value={v}>{label}</option>)}</select>;
 return <input aria-label={d.tag} inputMode="decimal" value={value??''} onChange={e=>onChange(e.target.value||null)} placeholder="未设置"/>;
}
export default function ParameterPanel({project,initialNodeId,onLayer,onTemplates,onProjections,onNumWann,embedded=false,maintenance=false,continuous=false,sourcePanel,previewHost,onFileDraft}:{previewHost?:HTMLDivElement|null;onFileDraft?:(id:string,update:(draft:Draft)=>Draft)=>void;embedded?:boolean;maintenance?:boolean;continuous?:boolean;sourcePanel?:ReactNode;project:Project;initialNodeId?:string|null;onLayer:(nodeId:string|null,layer:ParameterLayer)=>void;onTemplates:(templates:NonNullable<NonNullable<Project['parameters']>['templates']>)=>void;onProjections:(nodeId:string,value:string)=>void;onNumWann:(nodeId:string,value:string)=>void}) {
 const nodes=project.nodes.filter(n=>['relax','scf','bands','dos','vasp-wannier'].includes(calculationKind(n,project)||''));
 const [id,setId]=useState(nodes.some(n=>n.id===initialNodeId)?initialNodeId!:nodes[0]?.id||''),[scope,setScope]=useState<'node'|'shared'>('node'),[search,setSearch]=useState(''),[templateName,setTemplateName]=useState(''),[extraTag,setExtraTag]=useState('');
 const [detailTag,setDetailTag]=useState(''),[onlyOverrides,setOnlyOverrides]=useState(false);
 const [sharedBaseline,setSharedBaseline]=useState<ParameterLayer>(emptyLayer);
 const detailId=useId();
 const [group,setGroup]=useState(0);
 const [plotOpen,setPlotOpen]=useState(false);
 const groupBody=usePanelTransition<HTMLDivElement>(group);
 useIssueField(field=>{if(field.startsWith('parameter.'))setOnlyOverrides(false);if(continuous)return;if(field==='parameter.incar'){setSearch('');setGroup(6);}else if(field.startsWith('parameter.')){setScope('node');setSearch(field.slice(10));}});
 const [bulkMoment,setBulkMoment]=useState('0 0 0'),[bulkElement,setBulkElement]=useState('*');
 const node=nodes.find(n=>n.id===id)||nodes[0];
 const [caseReport,setCaseReport]=useState<FileReport & {parameters:Record<string,{value:string|null;source:string}>;errors:string[];warnings:string[];manual_duplicates?:{tag:string;form_value:string;text_value:string;form_layer:string;text_layer:string}[];parameter_source?:{parameter_summary:string;hubbard:Record<string,HubbardRow>;elements:string[];moments:string[]}}|null>(null);
 const [caseError,setCaseError]=useState('');
 const selectedSource=node?.data.sourceDraft as {attempt_id?:string}|undefined;
 const hasCase=Boolean(selectedSource?.attempt_id&&project.nodes.some(n=>n.data.kind==='scan'&&project.edges.some(e=>e.source===n.id&&e.target===node?.id)));
 const hasManual=Boolean(project.parameters?.shared.manualIncarText?.trim()||node?.data.parameters?.manualIncarText?.trim()||node?.data.parameters?.incarMode==='complete');
 const serverPreview=Boolean(onFileDraft)||hasCase||hasManual;
 const [fileBusy,setFileBusy]=useState(true);
 useEffect(()=>{const controller=new AbortController();let active=true;setFileBusy(serverPreview);if(!serverPreview)setCaseReport(null);setCaseError('');const timer=window.setTimeout(()=>{if(serverPreview&&node)void requestJson('/api/inputs/live-preview',{project,node_id:node.id},undefined,{signal:controller.signal}).then(r=>{if(active){setCaseReport(r);setFileBusy(false);}}).catch(e=>{if(active){setCaseReport(null);setFileBusy(false);setCaseError(String(e.message||e));}});},250);return()=>{active=false;controller.abort();window.clearTimeout(timer);};},[project,node?.id,serverPreview,hasManual]);
 if(!node)return <div className="structure-empty">请先添加计算节点，再配置参数。</div>;
 const layer=scope==='shared'?project.parameters?.shared||emptyLayer():node.data.parameters||emptyLayer();
 const formValues=effectiveParameters(project,node);
 const effective={...formValues,...(hasCase||hasManual?caseReport?.parameters:{})},structure=upstreamStructure(project,node),tables=parameterTables(project,node),issues=caseReport?[...caseReport.errors.map(message=>({level:'error' as const,message})),...caseReport.warnings.map(message=>({level:'warning' as const,message}))]:serverPreview?[]:checkParameters(project,node);
 if(caseReport?.parameter_source){const source=caseReport.parameter_source;tables.hubbard={...tables.hubbard,...source.hubbard,...node.data.parameters?.hubbard};if(structure&&source.moments.length&&structure.atoms.map(a=>a.element).join(',')===source.elements.join(','))tables.moments={...tables.moments,...Object.fromEntries(structure.atoms.map((a,i)=>[a.id,source.moments[i]])),...node.data.parameters?.moments};}
 const edit=(next:ParameterLayer)=>onLayer(scope==='shared'?null:node.id,next);
 const fileEditing=Boolean(onFileDraft)&&scope==='node'&&layer.incarMode==='complete';
 const rawValues=fileEditing?simpleIncar(layer.completeIncarText||''):null;
 if(fileEditing){
  for(const tag of Object.keys(effective)){
   let v=rawValues?rawValues[tag]??null:caseReport?.parameters?.[tag]?.value??null;
   if(rawValues&&tag==='XC_FUNCTIONAL')v=({PE:'PBE',PS:'PBEsol'} as Record<string,string>)[rawValues.GGA?.toUpperCase()]??null;
   if(v&&/^(\.TRUE\.|TRUE|T|\.FALSE\.|FALSE|F)$/i.test(v))v=/T/i.test(v)?'true':'false';
   effective[tag]={value:v,source:'完整手动 INCAR'};
  }
 }
 const set=(tag:string,value:string|null)=>{
  if(fileEditing){edit({...layer,completeIncarText:changeAssignment(layer.completeIncarText||'',tag,value)});return;}
  const sourceChoice=tag==='ICHARG'&&scope==='node'?{explicitCharge:value!==null}:{};
  const manual=layer.manualIncarText;
  const key=tag==='XC_FUNCTIONAL'?'GGA':tag;
  if(onFileDraft&&manual&&Object.hasOwn(simpleIncar(manual)||{},key)){
   edit({...layer,...sourceChoice,manualIncarText:changeAssignment(manual,tag,value),values:{...layer.values,[tag]:value}});return;
  }
  edit({...layer,...sourceChoice,values:{...layer.values,[tag]:value}});
 };
 const clear=(tag:string)=>{const values={...layer.values};delete values[tag];edit({...layer,...(tag==='ICHARG'?{explicitCharge:undefined}:{}),values,confirmed_custom_tags:layer.confirmed_custom_tags?.filter(item=>item!==tag)});};
 const applyMoments=()=>{if(!structure)return;const parts=bulkMoment.trim().split(/\s+/);const noncollinear=effective.LSORBIT?.value==='true'||effective.LNONCOLLINEAR?.value==='true';if(parts.length!==(noncollinear?3:1)||parts.some(part=>!Number.isFinite(Number(part))))return;const moments={...layer.moments};for(const atom of structure.atoms)if(bulkElement==='*'||atom.element===bulkElement)moments[atom.id]=parts.join(' ');edit({...layer,moments});};
 const hubbard=(symbol:string,field:keyof HubbardRow,value:string)=>{const row=layer.hubbard?.[symbol]||(scope==='node'?tables.hubbard[symbol]:undefined)||{l:'',u:'',j:''};edit({...layer,hubbard:{...layer.hubbard,[symbol]:{...row,[field]:value}}});};
 const projectionText=String((node.data.inputDraft as {projections?:string}|undefined)?.projections||'');
 const spinor=effective.LSORBIT?.value==='true'||effective.LNONCOLLINEAR?.value==='true';
 const recommendedWann=simpleProjectionCount(projectionText,structure,spinor);
 const chosenWann=String((node.data.inputDraft as {num_wann?:string}|undefined)?.num_wann||'');
 const matchingDefinitions=definitions.filter(d=>(!d.applies_to||d.applies_to.includes(calculationKind(node,project)))&&(d.tag+d.label).toLowerCase().includes(search.toLowerCase()));
 const filteredDefinitions=continuous?definitions.filter(d=>!d.applies_to||d.applies_to.includes(calculationKind(node,project)||'')):matchingDefinitions;
 const searchTags=[...matchingDefinitions.map(d=>d.tag),...['MAGMOM','LDAUL','LDAUU','LDAUJ'].filter(tag=>tag.includes(search.trim().toUpperCase())||(/磁矩/.test(search)&&tag==='MAGMOM')||(/U\/J/i.test(search)&&tag.startsWith('LDAU')))];
 const groups:[string,string[]][]=[
  ['电子自洽与占据',['XC_FUNCTIONAL','ENCUT','EDIFF','PREC','ALGO','ISMEAR','SIGMA','NELM','NBANDS','LREAL','LASPH','AMIX','BMIX','AMIX_MAG','BMIX_MAG']],
  ['离子移动与结构优化',['NSW','IBRION','ISIF','EDIFFG']],
  [continuous?'DFT+U':'DFT+U 与自旋',continuous?['LDAU','LDAUTYPE','LMAXMIX']:['ISPIN','LDAU','LDAUTYPE','LMAXMIX']],
  [continuous?'磁性与 SOC':'SOC 与非共线',[...(continuous?['ISPIN']:[]),'LSORBIT','LNONCOLLINEAR','SAXIS','GGA_COMPAT','LORBMOM']],
  ['电荷、重启与输出',['ISTART','ICHARG','LWAVE','LCHARG','ISYM','LORBIT','NEDOS','EMIN','EMAX']],
  ['并行',['NCORE','KPAR']]
 ];

 const overridden=(tag:string)=>Object.hasOwn(node.data.parameters?.values||{},tag);
 const overrideFilter=onlyOverrides&&scope==='node';
 const renderRows=(items:Definition[])=>{
  if(!items.length)return null;
  return <table className="physical-parameter-table">
   <thead><tr><th scope="col">参数</th><th scope="col">{scope==='shared'?'共享设置':'参数值'}</th><th scope="col">单位</th><th scope="col">来源</th></tr></thead>
   <tbody>{items.map(d=>{
    const resolved=effective[d.tag],fromText=resolved.source.includes('手动');
    const value=scope==='shared'?layer.values[d.tag]??null:fromText&&!onFileDraft?formValues[d.tag]?.value??null:resolved.value;
    const explicit=Object.hasOwn(layer.values,d.tag);
    const locked=!fileEditing&&(scope==='node'&&calculationKind(node,project)==='vasp-wannier'&&['NCORE','KPAR','ISYM'].includes(d.tag)||Boolean(node.data.sourceDraft&&d.tag==='ISTART'));
    const open=detailTag===d.tag;
    const localSource=scope==='node'&&resolved.source!=='共享设置'&&resolved.source!=='未设置'?resolved.source:'';
    const showFinal=(fromText||scope==='shared')&&resolved.value!==value;
    return <Fragment key={d.tag}><tr className={open?'parameter-selected':undefined}>
     <th scope="row"><button type="button" className="parameter-detail-trigger" aria-expanded={open} aria-controls={open?detailId:undefined} onClick={()=>setDetailTag(open?'':d.tag)}><code>{d.tag}</code><span>{d.label}</span><span className="parameter-chevron" aria-hidden="true"/></button></th>
     <td data-action-field={'parameter.'+d.tag}>{locked?<output aria-label={d.tag}>{resolved.value}</output>:fileEditing&&!rawValues?<span>{value??"未设置"} · 在 INCAR 原文编辑</span>:<Input definition={fileEditing&&d.tag==='XC_FUNCTIONAL'?{...d,choices:d.choices?.filter(([v])=>v!=='LDA')}:d} value={value} onChange={v=>set(d.tag,v)}/>}{showFinal&&<small>节点最终值 {resolved.value??'未设置'}</small>}</td>
     <td>{d.unit||'—'}</td><td>{localSource&&<span className={overridden(d.tag)?'override-label':undefined}>{localSource}</span>}{explicit&&!fileEditing&&<button className="text-button" onClick={()=>clear(d.tag)}>{scope==='shared'?'清除共享值':'恢复继承'}</button>}</td>
    </tr>{open&&<tr id={detailId} className="parameter-detail"><td colSpan={4}><strong>{d.tag} · {resolved.source}</strong><p>{d.help}</p>{overridden(d.tag)&&project.parameters?.shared.values[d.tag]!=null&&<p>项目共享值 {project.parameters.shared.values[d.tag]} {d.unit}</p>}<p>节点最终值 {resolved.value??'未设置'} {d.unit} · 目标 VASP {targetVaspVersion}</p><p>适用节点：{d.applies_to?.join('、')||'当前 VASP 节点，仍需按计算方法核对'}</p><a href={d.url} target="_blank" rel="noreferrer">VASP 官方说明</a></td></tr>}</Fragment>;
   })}</tbody>
  </table>;
 };
 const openIssue=(message:string)=>{
  const tag=[...knownTags].sort((a,b)=>b.length-a.length).find(t=>new RegExp('\\b'+t+'\\b').test(message));
  const index=tag?groups.findIndex(([,tags])=>tags.includes(tag)):-1;
  if(continuous&&tag){jumpToParameter(tag);return;}
  setOnlyOverrides(false);setSearch('');setGroup(tag?.startsWith('LDAU')||tag==='MAGMOM'||/U\/J|磁矩/.test(message)?7:index>=0?index:6);
  setTimeout(()=>{const field=tag?document.querySelector<HTMLElement>('[aria-label="'+tag+'"]'):null;(field||document.querySelector<HTMLElement>('.parameter-group input,.parameter-tables input,.parameter-tables select,.parameter-checks input'))?.focus();},0);
 };
 const magneticTable=()=>(<section data-action-field="parameter.MAGMOM"><h3>初始磁矩 · MAGMOM / μB</h3><p>共线每原子一个数，非共线或 SOC 每原子三个分量（如 0 0 4）；保留原子身份；{spinor?'本节点每个原子填写三个分量，例如 0 0 4。':''}当前 ISPIN = {effective.ISPIN.value??'未设置'}。这些值不是约束磁矩，也不是最终计算磁矩。</p>{structure?<><div className="magmom-bulk"><label>批量范围<select value={bulkElement} onChange={e=>setBulkElement(e.target.value)}><option value="*">全部原子</option>{[...new Set(structure.atoms.map(a=>a.element))].map(symbol=><option key={symbol} value={symbol}>{symbol} 原子</option>)}</select></label><label>磁矩分量<input aria-label="批量 MAGMOM" value={bulkMoment} onChange={e=>setBulkMoment(e.target.value)} placeholder={effective.LSORBIT?.value==='true'||effective.LNONCOLLINEAR?.value==='true'?'0 0 4':'4'}/></label><button className="button" disabled={(()=>{const parts=bulkMoment.trim().split(/\s+/);return parts.length!==((effective.LSORBIT?.value==='true'||effective.LNONCOLLINEAR?.value==='true')?3:1)||parts.some(part=>!Number.isFinite(Number(part)));})()} onClick={applyMoments}>应用到所选原子</button></div><div className="atoms-scroll"><table><thead><tr><th>原子</th><th>元素</th><th>初始磁矩</th><th>来源</th><th/></tr></thead><tbody>{structure.atoms.map((a,i)=>{const value=scope==='shared'?layer.moments?.[a.id]:tables.moments[a.id];return <tr key={a.id}><td title={a.id}>{i+1}</td><td>{a.element}</td><td><input aria-label={`原子 ${i+1} MAGMOM`} value={value??''} onChange={e=>edit({...layer,moments:{...layer.moments,[a.id]:e.target.value||null}})}/></td><td>{Object.hasOwn(node.data.parameters?.moments||{},a.id)?'节点覆盖':caseReport?.parameter_source?.moments.length?'继承 case 实际 MAGMOM':Object.hasOwn(project.parameters?.shared.moments||{},a.id)?'共享设置':'未设置'}</td><td><button className="text-button" onClick={()=>{const moments={...layer.moments};delete moments[a.id];edit({...layer,moments});}}>恢复继承</button></td></tr>;})}</tbody></table></div><details><summary>按当前结构顺序预览 MAGMOM</summary><pre className="input-text">{structure.atoms.map(atom=>tables.moments[atom.id]??'?').join('  ')}</pre></details></>:<p>请连接唯一、已导入的上游结构以建立原子映射。</p>}</section>);
 const hubbardTable=()=>(<section><h3>DFT+U 元素表 · LDAUL / LDAUU / LDAUJ</h3><p>当前 LDAU = {effective.LDAU.value??'未设置'}。{effective.LDAUTYPE.value==='3'?'LDAUU 和 LDAUJ 分别为作用于自旋向上、向下的微扰势，可取负值。':'U、J 单位 eV；Dudarev 使用 U−J。'}不同 U/J 的能量不能直接作为同一模型比较。</p>{structure?<div className="atoms-scroll"><table><thead><tr><th>元素</th><th>轨道</th><th>{effective.LDAUTYPE.value==='3'?'↑ 微扰势':'U'} / eV</th><th>{effective.LDAUTYPE.value==='3'?'↓ 微扰势':'J'} / eV</th><th>来源</th><th/></tr></thead><tbody>{[...new Set(structure.atoms.map(a=>a.element))].map(symbol=>{const r=(scope==='shared'?layer.hubbard?.[symbol]:tables.hubbard[symbol])||{l:'',u:'',j:''};return <tr key={symbol}><td>{symbol}</td><td data-action-field="parameter.LDAUL"><select aria-label={`${symbol} LDAUL`} value={r.l} onChange={e=>hubbard(symbol,'l',e.target.value)}><option value="">未设置</option>{[['-1','不加 U'],['0','s'],['1','p'],['2','d'],['3','f']].map(([v,t])=><option key={v} value={v}>{v} · {t}</option>)}</select></td><td data-action-field="parameter.LDAUU"><input aria-label={`${symbol} U`} disabled={r.l==='-1'} value={r.u} onChange={e=>hubbard(symbol,'u',e.target.value)}/></td><td data-action-field="parameter.LDAUJ"><input aria-label={`${symbol} J`} disabled={r.l==='-1'} value={r.j} onChange={e=>hubbard(symbol,'j',e.target.value)}/></td><td>{node.data.parameters?.hubbard?.[symbol]?'节点覆盖':caseReport?.parameter_source?.hubbard[symbol]?'继承 case 实际 U/J':project.parameters?.shared.hubbard?.[symbol]?'共享设置':'未设置'}</td><td><button className="text-button" onClick={()=>{const h={...layer.hubbard};delete h[symbol];edit({...layer,hubbard:h});}}>恢复继承</button></td></tr>;})}</tbody></table></div>:<p>需要唯一已导入结构。相同元素暂用一组 U/J，按物种区分的复杂模型留待扩展。</p>}</section>);
 const jumpToParameter=(tag:string)=>{
  setOnlyOverrides(false);
  requestAnimationFrame(()=>{
   const area=groupBody.current;
   const el=area?.querySelector<HTMLElement>('[data-action-field="parameter.'+CSS.escape(tag)+'"]');
   if(!el){if(['MAGMOM','LDAUL','LDAUU','LDAUJ'].includes(tag)){const detail=area?.querySelector<HTMLDetailsElement>(tag==='MAGMOM'?'[data-magmom-table]':'[data-hubbard-table]');if(detail){detail.open=true;detail.scrollIntoView({block:'center'});}}return;}
   for(let parent:HTMLElement|null=el;parent&&parent!==area;parent=parent.parentElement){
    if(parent instanceof HTMLDetailsElement)parent.open=true;
   }
   el.scrollIntoView({block:'center',behavior:'instant'});
   el.querySelector<HTMLElement>('input:not(:disabled),select,textarea,button,summary')?.focus({preventScroll:true});
  });
 };
 const presets=<div> <details className="configuration-presets workbench-disclosure"><DisclosureLabel title="阶段预设与个人模板"/><div className="parameter-presets"><label>此节点的阶段预设<select value={node.data.parameters?.preset||'none'} onChange={e=>onLayer(node.id,{...(node.data.parameters||emptyLayer()),preset:e.target.value as 'none'|'basic'})}><option value="none">不启用预设</option><option value="basic">{calculationKind(node,project)==='relax'?'固定晶胞优化：IBRION=2、ISIF=2':'静态阶段：NSW=0、IBRION=−1'}</option></select></label><p>预设不填写 ENCUT、磁矩、U/J，也不设置 ICHARG 或选择电荷文件。</p></div>
 <details className="personal-templates"><summary>个人参数模板</summary><p>保存当前编辑范围的明确值；应用会替换该范围，可撤销。磁矩按原子 ID 保存，不按位置重用。</p><label>模板名称<input maxLength={80} value={templateName} onChange={e=>setTemplateName(e.target.value)}/></label><button className="button" disabled={!templateName.trim()||(project.parameters?.templates?.length||0)>=20} onClick={()=>{onTemplates([...(project.parameters?.templates||[]),{name:templateName.trim(),layer:structuredClone(layer)}]);setTemplateName('');}}>保存为模板</button>{project.parameters?.templates?.map((t,i)=><span key={i}><button className="button" onClick={()=>edit(structuredClone(t.layer))}>应用：{t.name}</button><button className="text-button" onClick={()=>onTemplates((project.parameters?.templates||[]).filter((_,j)=>j!==i))}>删除</button></span>)}</details>
 </details></div>;
 if(maintenance)return presets;
 return <section className={`parameter-workspace ${embedded?'parameter-embedded':''}`}><div className="parameter-heading"><div><h2>{embedded?"物理参数与继承":"参数与继承 · "+project.name}</h2><details><summary>参数继承</summary><p>共享设置 → 所选 case → 阶段默认／预设 → 节点覆盖。“恢复继承”重新使用下层值。</p></details></div>{!embedded&&<label className="prominent-node-choice"><strong>当前参数节点：{node.data.label}</strong><select value={node.id} onChange={e=>setId(e.target.value)}>{nodes.map(n=><option key={n.id} value={n.id}>{n.data.label}</option>)}</select></label>}<label>编辑范围<select value={scope} onChange={e=>{if(e.target.value==='shared')setSharedBaseline(project.parameters?.shared??emptyLayer());setScope(e.target.value as typeof scope);}}><option value="node">仅此节点</option><option value="shared">项目共享设置</option></select></label></div>{scope==='shared'&&<SharedParameterImpact project={project} baseline={sharedBaseline}/>}
{node.data.parameters?.incarMode==='complete'&&<p className="handoff-warning" role="status">当前使用节点 INCAR 原文。可在文件预览中切回表单生成。</p>}<div className="parameter-tools"><label className="parameter-search"><input aria-label="搜索已支持参数" value={search} placeholder="搜索参数 · ENCUT、截断能…" onChange={e=>setSearch(e.target.value)} onKeyDown={e=>{if(continuous&&e.key==='Enter'&&search.trim()&&searchTags.length)jumpToParameter(searchTags[0]);}}/></label><button type="button" className="text-button" onClick={()=>{if(continuous)jumpToParameter('incar');else setGroup(6);setSearch('');}}>INCAR 文本／导入</button></div>
{continuous&&search.trim()&&<div className="parameter-search-matches">{searchTags.length?searchTags.map(tag=><button key={tag} className="text-button" onClick={()=>jumpToParameter(tag)}>{tag}</button>):<span>没有匹配的参数</span>}</div>}
{onFileDraft&&scope==='node'&&layer.manualIncarText&&<details><summary>已有补充 INCAR 文本</summary><textarea aria-label="已有补充 INCAR 文本" value={layer.manualIncarText} onChange={e=>edit({...layer,manualIncarText:e.target.value})}/></details>}{presets}{hasCase&&!caseError&&<p role="status">{caseReport?.parameter_source?'参数继承自 case · '+caseReport.parameter_source.parameter_summary:'正在读取所选 case 的实际参数；最终以输入审查为准。'}</p>}{!continuous&&<nav className="parameter-group-nav" aria-label="参数分组">{[...groups.map(([title])=>title),'低频与自定义','磁矩与 U/J',...(calculationKind(node,project)==='vasp-wannier'?['Wannier 投影']:[])].map((title,index)=><button key={title} aria-pressed={!search&&group===index} className={!search&&group===index?'active':''} onClick={()=>{setGroup(index);setSearch('');}}>{title}</button>)}</nav>}

 <div ref={groupBody}>
 {(group<6||search)&&<div className="parameter-view-options"><span>{scope==='node'?'默认继承项目设置':'编辑项目共享设置'}</span>{scope==='node'&&<label><input type="checkbox" checked={onlyOverrides} onChange={e=>setOnlyOverrides(e.target.checked)}/>仅看节点覆盖</label>}</div>}
 {continuous&&[0,3,2,1,4].map(index=>{
  const labels=['电子设置','离子与晶胞','DFT+U','磁性与 SOC','来源与重启'];
  const title=labels[index],tags=index===0?[...groups[0][1],...groups[5][1]]:groups[index][1];
  const items=filteredDefinitions.filter(d=>tags.includes(d.tag)&&(!overrideFilter||overridden(d.tag)));
  const common=index===0?items.filter(d=>tags.indexOf(d.tag)<7):items;
  const advanced=index===0?items.filter(d=>tags.indexOf(d.tag)>=7):[];
  return <section className="parameter-group" data-config-section={title} key={title}>
   <h3>{title}</h3>{index===4&&sourcePanel}{renderRows(common)}
   {advanced.length>0&&<details className="workbench-disclosure"><DisclosureLabel title="高级电子参数"/>{renderRows(advanced)}</details>}
   {index===2&&fileEditing&&rawValues&&['LDAUL','LDAUU','LDAUJ'].map(tag=><label key={tag} data-action-field={'parameter.'+tag}>{tag} · 按物种顺序<input value={rawValues[tag]??''} onChange={e=>set(tag,e.target.value||null)}/></label>)}{index===2&&fileEditing&&!rawValues&&<p>在 INCAR 原文编辑 LDAUL、LDAUU 和 LDAUJ。</p>}{index===2&&!fileEditing&&<details data-hubbard-table open={(scope==='shared'?layer.values.LDAU:effective.LDAU.value)==='true'}><DisclosureLabel title="元素轨道与 U/J"/>{hubbardTable()}</details>}
   {index===3&&fileEditing&&rawValues&&<label data-action-field="parameter.MAGMOM">MAGMOM · 按原子顺序<input value={rawValues.MAGMOM??''} onChange={e=>set('MAGMOM',e.target.value||null)}/></label>}{index===3&&fileEditing&&!rawValues&&<p>在 INCAR 原文编辑 MAGMOM。</p>}{index===3&&!fileEditing&&<details data-magmom-table open={spinor||effective.ISPIN.value==='2'}><DisclosureLabel title="初始磁矩 · MAGMOM"/>{magneticTable()}</details>}
  </section>;
 })}
 {!continuous&&groups.map(([title,tags],index)=>{const items=filteredDefinitions.filter(d=>tags.includes(d.tag));if(!items.length||(!search&&group!==index))return null;if(overrideFilter){const matches=items.filter(d=>overridden(d.tag));return <section className="parameter-group" key={title}>{search&&<h3>{title}</h3>}{matches.length?renderRows(matches):<p className="summary-muted">本组没有匹配的节点覆盖项。</p>}</section>;}const common=index===0?items.filter(d=>tags.indexOf(d.tag)<7):items,advanced=index===0?items.filter(d=>tags.indexOf(d.tag)>=7):[];return <section className="parameter-group" key={title} aria-label={title}>{search&&<h3>{title}</h3>}{index===4&&sourcePanel}{search?renderRows(items):<>{renderRows(common)}{advanced.length>0&&<details className="advanced-parameters workbench-disclosure"><DisclosureLabel title={`高级电子参数 · ${advanced.length} 项`}/>{renderRows(advanced)}</details>}</>}</section>;})}{!filteredDefinitions.length&&<p>该搜索词没有强类型表单，可在下方使用官方已知低频参数入口。可查阅 <a href="https://vasp.at/wiki/Category:INCAR_tag" target="_blank" rel="noreferrer">VASP 参数目录</a>；不会把未知参数标为验证通过。</p>}
 {calculationKind(node,project)==='vasp-wannier'&&(continuous||group===8||Boolean(search))&&<section data-config-section="Wannier 投影" className="parameter-group"><h3>VASP → Wannier 投影</h3><PyW90PreDosPanel key={node.id} project={project} nodeId={node.id} projectionText={projectionText} onProjections={value=>onProjections(node.id,value)} targetSpinors={spinor}/><p>LWANNIER90 与接口并行设置提供可编辑默认值。投影按完整原文传递，可写具体轨道、杂化、中心和方向；不限制轨道名称，不要求每个元素都有投影。</p><label className="field">Projections 完整文本<textarea aria-label="Wannier 投影原文" className="input-text" rows={8} value={projectionText} onChange={e=>onProjections(node.id,e.target.value)} placeholder={'例如 Si:s\nSi:p\n也可输入完整 Begin/End Projections 块'}/></label><p>推荐 NUM_WANN：<strong>{recommendedWann??'无法自动估算，请手动填写'}</strong>。仅对全部可明确计数的“元素:s/p/d/f”简单表示推荐；{spinor?'当前 SOC／非共线草稿按 spinor 乘 2。':'共线按每通道计数。'}复杂表达不使用部分轨道数估算总数。</p><label className="field">本次 NUM_WANN（必填，由您决定）<input type="number" min="1" step="1" aria-label="本次 NUM_WANN" value={chosenWann} onChange={e=>onNumWann(node.id,e.target.value)} placeholder="请填写正整数"/></label><button className="button" disabled={recommendedWann===null} onClick={()=>onNumWann(node.id,String(recommendedWann))}>采用简单轨道推荐值</button><p>推荐仅供参考，不要求与手填值相等；维度诊断仅提示；实际矩阵文件仍需可读取。</p></section>}
 {calculationKind(node,project)==='vasp-wannier'&&(continuous?<details onToggle={e=>setPlotOpen(e.currentTarget.open)}><DisclosureLabel title="能带与投影对照"/>{plotOpen&&<InteractivePlot project={project} compact/>}</details>:group===8&&<InteractivePlot project={project} compact/>)}
 {(!onFileDraft||scope==="shared")&&(continuous||group===6||Boolean(search))&&<section data-config-section="手动 INCAR" data-action-field="parameter.incar" className="parameter-checks"><h3>低频参数与手动 INCAR</h3><p>目录 {catalogRevision} · 目标 VASP {targetVaspVersion}。搜索用于查阅和插入模板；手动文本不做 tag 白名单、取值审核或逐项确认。</p><label>搜索 INCAR tag<input list="known-vasp-tags" value={extraTag} onChange={e=>setExtraTag(e.target.value.toUpperCase().trim())} placeholder="例如 ADDGRID、M_CONSTR"/></label><datalist id="known-vasp-tags">{[...knownTags].map(tag=><option key={tag} value={tag}/>)}</datalist>{extraTag&&<div className="catalog-matches">{[...knownTags].filter(tag=>tag.includes(extraTag)).slice(0,30).map(tag=><button className="text-button" key={tag} onClick={()=>setExtraTag(tag)}>{tag}</button>)}</div>}<button className="button" disabled={!/^[A-Z][A-Z0-9_/]*$/.test(extraTag)} onClick={()=>{edit({...layer,manualIncarText:(layer.manualIncarText||'').replace(/\n?$/,'\n')+extraTag+' = \n'});setExtraTag('');}}>插入文本模板</button><IncarEditor key={node.id+scope} layer={layer} shared={scope==='shared'} onChange={edit} duplicates={(caseReport?.manual_duplicates||[]).filter(row=>row.text_layer===(scope==='shared'?'共享设置':'节点覆盖'))} onClearForm={(tag,where)=>{const shared=where==='共享设置',source=shared?project.parameters?.shared:node.data.parameters;const values={...source?.values,[tag]:null};onLayer(shared?null:node.id,{...source,values});}}/>{hasManual&&!caseReport&&!caseError&&<p role="status">正在生成本机最终输入预览，不连接服务器…</p>}{caseReport?.parameters&&<details><summary>手动覆盖后的实际值</summary><dl>{Object.entries(caseReport.parameters).filter(([,v])=>v.source.includes('手动')).map(([tag,v])=><div key={tag}><dt>{tag} · {v.source}</dt><dd><pre>{v.value}</pre></dd></div>)}</dl></details>}{Object.entries(layer.values).filter(([tag])=>!definitions.some(d=>d.tag===tag)).map(([tag,value])=><div key={tag}><label>{tag} · 已有单项参数<input value={value??''} onChange={e=>set(tag,e.target.value||null)} placeholder="多行内容使用上方文本框"/></label><button className="text-button" onClick={()=>clear(tag)}>移除</button></div>)}</section>}
 {!continuous&&(group===7||/MAGMOM|LDAU|磁矩|U\/J/i.test(search))&&<div className="parameter-tables">{magneticTable()}{hubbardTable()}</div>}
 </div>
 {onFileDraft&&previewHost&&createPortal(<InputFilePreview key={node.id} layer={node.data.parameters||emptyLayer()} structure={structure} bands={calculationKind(node,project)==='bands'} draft={(node.data.inputDraft||{}) as Draft} onDraft={fn=>onFileDraft(node.id,fn)} report={caseReport} busy={fileBusy} error={caseError} onLayer={next=>onLayer(node.id,next)}/>,previewHost)}
 <section className="parameter-checks"><h3>当前节点配置检查</h3>{caseError&&<p role="alert">参数预览失败：{caseError}</p>}{issues.length?issues.map((i,n)=><p key={n} className={i.level==='error'?'danger':''}>{i.level==='error'?<button className="text-button" onClick={()=>openIssue(i.message)}>待修正：{i.message}</button>:'待确认：'+i.message}</p>):<p>{hasManual?"手动文本未做参数白名单与取值审核；请在最终输入中核对来源与接口一致性。":"已支持字段的类型和基础规则通过；尚未检查赝势、k 点、VASP 版本或输入文件兼容性。"}</p>}</section>
 </section>;
}

import {useEffect,useRef,useState} from 'react';
import {requestJson,errorMessage} from './apiClient';
import {currentPoscar,validateStructure,verifySource} from './structure';
import type {StructureDocument} from './structure';

type Proposal={structure:StructureDocument;summary:{volume_before:number;volume_after:number;volume_ratio:number;lengths_before:number[];lengths_after:number[];atoms_before:number;atoms_after:number;elements_before:string[];species_after:string[];counts_after:number[];atom_ids_preserved:boolean;scale:number[];coordinate_mode:string;extra_sections:string[];messages:string[]}};
export default function PoscarEditor({structure,onApply,onCancel}:{structure:StructureDocument;onApply:(s:StructureDocument)=>void;onCancel:()=>void}){
 const [text,setText]=useState(()=>currentPoscar(structure)),[rebuild,setRebuild]=useState(false),[proposal,setProposal]=useState<Proposal|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const alive=useRef(true);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 const changed=()=>{setProposal(null);setError('');};
 const verify=async()=>{
  setBusy(true);setError('');setProposal(null);
  try{
   if(new TextEncoder().encode(text).length>262144)throw new Error('POSCAR 文本不能超过 256 KiB。');
   const result:Proposal=await requestJson('/api/structures/poscar-edit',{parent:structure,text,rebuild_atom_ids:rebuild});
   validateStructure(result.structure);await verifySource(result.structure);
   if(alive.current)setProposal(result);
  }catch(e){if(alive.current)setError(errorMessage(e));}finally{if(alive.current)setBusy(false);}
 };
 const format=(n:number)=>n.toFixed(6),vec=(v:number[])=>v.map(format).join(' / ');
 const elementRows=(elements:string[])=>{const rows:{element:string;count:number}[]=[];for(const element of elements){const last=rows.at(-1);if(last?.element===element)last.count++;else rows.push({element,count:1});}return rows.map(r=>`${r.element} ${r.count}`).join(' / ');};
 return <section className="poscar-editor" aria-label="POSCAR 文本编辑器">
  <h3>编辑 POSCAR 文本</h3>
  <p>修改完整文本，先验证并预览，再应用到当前结构节点。原导入文件和历史冻结输入保留。</p>
  <details><summary>POSCAR 格式与 EOS 缩放说明</summary>
   <p>依次为：标题 → scale → 三行晶格向量 → 元素 → 数量 → 可选 Selective dynamics → Direct / Cartesian → 坐标及可选 T/F → 可识别附加段。</p>
   <p>一个正 scale 缩放长度；负数的绝对值是目标体积（Å³）；三个正数分别缩放笛卡尔 x/y/z 分量。Cartesian 坐标也按相应因子缩放，Direct 分数坐标保持。</p>
   <p>对同一组晶格向量，各向同性长度因子 s 对应体积因子 s³。例如 1.01 对应 1.030301。当前文本已包含此前的缩放，不会自动恢复原导入晶胞。</p>
   <p>默认按坐标行保留原子身份。若重排原子行，勾选“重新建立原子身份”并重新核对磁矩。</p>
  </details>
  <label className="poscar-text-label">当前 POSCAR<textarea aria-label="POSCAR 完整文本" spellCheck={false} disabled={busy} value={text} onChange={e=>{setText(e.target.value);changed();}}/></label>
  <label className="poscar-identity"><input type="checkbox" disabled={busy} checked={rebuild} onChange={e=>{setRebuild(e.target.checked);changed();}}/>重新建立原子身份（用于重排原子；元素或数量变化时自动重建）</label>
  {error&&<p role="alert" className="structure-error">{error} 当前结构未改变，编辑文本保留。</p>}
  {proposal&&<div className="poscar-preview" aria-label="POSCAR 变更预览">
   <h4>验证通过 · 尚未应用</h4>
   <div className="poscar-volume"><div><span>体积 / Å³</span><strong>{format(proposal.summary.volume_before)} → {format(proposal.summary.volume_after)}</strong></div><div><span>体积比 V/V₀</span><strong>{proposal.summary.volume_ratio.toFixed(9)}</strong></div></div>
   <p>a / b / c（Å）：{vec(proposal.summary.lengths_before)} → {vec(proposal.summary.lengths_after)}</p>
   <p>原子数：{proposal.summary.atoms_before} → {proposal.summary.atoms_after} · 元素顺序及数量：{elementRows(proposal.summary.elements_before)} → {proposal.summary.species_after.map((s,i)=>`${s} ${proposal.summary.counts_after[i]}`).join(' / ')}</p>
   <p>scale：{proposal.summary.scale.join(' ')} · {proposal.summary.coordinate_mode==='fractional'?'Direct':'Cartesian'} · {proposal.summary.atom_ids_preserved?'原子身份按行保留':'原子身份重新建立'}</p>
   {proposal.summary.messages.map((m,i)=><p key={i} role="status">{m}</p>)}
  </div>}
  <div className="crystal-actions"><span>只验证文件与几何；计算参数继续在计算配置页审核。</span><div>
   <button className="button" disabled={busy} onClick={onCancel}>放弃本次编辑</button>{' '}
   <button className="button" disabled={busy||!text.trim()} onClick={()=>void verify()}>{busy?'正在验证…':'验证并预览'}</button>{' '}
   <button className="button primary" disabled={busy||!proposal} onClick={()=>{if(proposal)onApply(proposal.structure);}}>应用到结构节点</button>
  </div></div>
 </section>;
}

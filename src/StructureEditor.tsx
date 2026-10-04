import {useEffect,useRef,useState} from 'react';
import type {StructureDocument} from './structure';
import {validateStructure,verifySource} from './structure';

export default function StructureEditor({structure,mode,onApply,onCancel}:{structure:StructureDocument;mode:'fractional'|'cartesian';onApply:(s:StructureDocument)=>void;onCancel:()=>void}) {
  const [lattice,setLattice]=useState(structure.lattice.map(row=>row.map(String)));
  const [atoms,setAtoms]=useState(structure.atoms.map(a=>({id:a.id,element:a.element,coordinates:a[mode].map(String),selective:a.selective?[...a.selective]:null})));
  const [error,setError]=useState(''),[busy,setBusy]=useState(false);const request=useRef(0);
  useEffect(()=>()=>{request.current++;},[]);
  const number=(s:string)=>{if(!s.trim()||!Number.isFinite(Number(s)))throw new Error('矩阵和坐标每项都需要有限数值。');return Number(s);};
  const apply=async()=>{
    setBusy(true);setError('');const id=++request.current;
    try{
      const response=await fetch('/api/structures/edit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({parent:structure,lattice:lattice.map(row=>row.map(number)),atoms:atoms.map(a=>({...a,coordinates:a.coordinates.map(number)})),coordinate_mode:mode})});
      const result=await response.json();if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'编辑未能验证。');
      validateStructure(result);await verifySource(result);if(id===request.current)onApply(result);
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}
  };
  return <section className="geometry-editor"><h3>编辑几何与约束</h3><p>当前使用{mode==='fractional'?'分数坐标：改变晶胞时，未修改的分数坐标保持不变。':'笛卡尔坐标（Å）：改变晶胞时，未修改的原子空间位置保持不变。'} 原子编号保持，原文件不改写。</p><fieldset disabled={busy}><legend>晶胞行向量 / Å</legend>{lattice.map((row,i)=><div className="lattice-row" key={i}><span>{['a','b','c'][i]}</span>{row.map((v,j)=><input key={j} aria-label={`晶胞 ${['a','b','c'][i]} ${['x','y','z'][j]}`} value={v} onChange={e=>setLattice(old=>old.map((r,k)=>k===i?r.map((x,l)=>l===j?e.target.value:x):r))}/>)}</div>)}</fieldset><fieldset disabled={busy}><legend>原子坐标与 Selective Dynamics</legend><p>T 表示允许沿对应晶格方向移动；F 表示固定。此约束方向遵循 VASP 分数基矢约定，与当前坐标显示类型无关。</p><div className="atoms-scroll"><table><thead><tr><th>编号</th><th>元素</th><th>坐标 1</th><th>坐标 2</th><th>坐标 3</th><th>约束</th></tr></thead><tbody>{atoms.map((a,i)=><tr key={a.id}><td title={a.id}>{i+1}</td><td>{a.element}</td>{a.coordinates.map((v,j)=><td key={j}><input aria-label={`原子 ${i+1} 坐标 ${j+1}`} value={v} onChange={e=>setAtoms(old=>old.map((x,k)=>k===i?{...x,coordinates:x.coordinates.map((n,l)=>l===j?e.target.value:n)}:x))}/></td>)}<td><select aria-label={`原子 ${i+1} 约束`} value={a.selective?a.selective.map(v=>v?'T':'F').join(''):'none'} onChange={e=>setAtoms(old=>old.map((x,k)=>k===i?{...x,selective:e.target.value==='none'?null:e.target.value.split('').map(v=>v==='T')}:x))}><option value="none">未指定</option>{['TTT','TTF','TFT','TFF','FTT','FTF','FFT','FFF'].map(v=><option key={v}>{v}</option>)}</select></td></tr>)}</tbody></table></div></fieldset>{error&&<p role="alert" className="structure-error">{error}</p>}<div className="crystal-actions"><span>应用后可通过工作台“撤销”恢复。只校验几何，不判断物理合理性。</span><button className="button" disabled={busy} onClick={onCancel}>放弃本次编辑</button><button className="button primary" disabled={busy} onClick={()=>void apply()}>{busy?'正在验证…':'验证并应用'}</button></div></section>;
}

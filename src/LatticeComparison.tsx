import {useEffect,useState} from 'react';
import {formatValue} from './resultWorkspace';
import {latticeMetrics,latticeStatus,latticeCsv,relativeChange} from './latticeMetrics';
import type {LatticeRow} from './latticeMetrics';

export default function LatticeComparison({rows,labels}:{rows:LatticeRow[];labels:Record<string,string>}){
 const [referenceId,setReferenceId]=useState(rows[0]?.attempt_id||'');
 useEffect(()=>{setReferenceId(old=>rows.some(row=>row.attempt_id===old)?old:rows[0]?.attempt_id||'');},[rows]);
 const reference=rows.find(row=>row.attempt_id===referenceId)||rows[0];
 const label=(row:LatticeRow)=>(labels[row.node_id]||'计算')+' · '+(row.job_id||row.attempt_id.slice(0,12));
 const percent=(value:number|null)=>value===null?'—':(value>0?'+':'')+formatValue(value);
 const download=()=>{
  const url=URL.createObjectURL(new Blob([latticeCsv(rows,reference?.attempt_id||'',label)],{type:'text/csv;charset=utf-8'}));
  const link=document.createElement('a');link.href=url;link.download='lattice-comparison.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 };
 return <section className="input-submit-card lattice-comparison">
  <div className="section-action-heading"><div><h3>晶格常数与体积</h3><p>取每个任务 OUTCAR 最后一组实际晶格。a、b、c 为晶格矢量长度，α、β、γ 为晶格夹角；不自动转换原胞或常规胞。</p></div><button className="button" onClick={download}>导出晶格对比 CSV</button></div>
  <div className="atoms-scroll"><table><thead><tr><th>结果</th><th>阶段 / 数值状态</th>{latticeMetrics.map(([key,name,unit])=><th key={key}>{name} / {unit}</th>)}</tr></thead><tbody>{rows.map(row=><tr key={row.attempt_id}><td>{label(row)}</td><td>{row.stage||'未记录'} · {latticeStatus(row)}</td>{latticeMetrics.map(([key])=><td key={key}>{formatValue(row.metrics?.[key]?.value)}</td>)}</tr>)}</tbody></table></div>
  <label>变化量参考结果<select aria-label="晶格变化量参考结果" value={reference?.attempt_id||''} onChange={e=>setReferenceId(e.target.value)}>{rows.map(row=><option key={row.attempt_id} value={row.attempt_id}>{label(row)}</option>)}</select></label>
  <p>相对变化 =（当前值 − 参考值）/ 参考值 × 100%。选择参考结果只更新本地表格。</p>
  <div className="atoms-scroll"><table><thead><tr><th>结果</th><th>Δa / %</th><th>Δb / %</th><th>Δc / %</th><th>ΔV / %</th></tr></thead><tbody>{rows.map(row=><tr key={row.attempt_id}><td>{label(row)}</td>{(['lattice_a','lattice_b','lattice_c','volume'] as const).map(key=><td key={key}>{percent(relativeChange(row.metrics?.[key]?.value,reference?.metrics?.[key]?.value))}</td>)}</tr>)}</tbody></table></div>
  <p className="muted">文献对比前请核对晶胞表示、a/b/c 轴序、化学组成与优化条件。非优化阶段的晶格不是重新优化后的平衡晶格；“优化已收敛”仅表示看到离子与电子收敛证据，不替代物理验证。</p>
  {rows.map(row=>row.metrics?.lattice_error?<p className="handoff-warning" key={row.attempt_id}>{label(row)}：{row.metrics.lattice_error}</p>:!row.metrics?.lattice_a?<p className="muted" key={row.attempt_id}>{label(row)}：尚无晶格指标，请读取并比较指标；缺失值不会补零。</p>:null)}
 </section>;
}

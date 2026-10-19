export type DosSelection={lb:number;threshold:number;spatial_orbitals:number;suggested_num_wann:number;spinors?:boolean;channel?:string;normalization_integral?:number;orbitals:{species:string;structure_id:number;orb_name:string;dos:number;integral?:number;selected:boolean}[]};

export default function PyW90ProjectionDiagnostic({selection,projections,numWann,ratioBasis='auto'}:{selection:DosSelection;projections:string[];numWann?:number;ratioBasis?:'auto'|'current_model'|'manual'}){
 return <><p>PDOS 自动建议：{selection.spatial_orbitals} 个空间轨道，<strong className="pyw90-key-number">{selection.suggested_num_wann}</strong> 个函数。此计数仅适用于本次建议的原子轨道集合，不能代替复杂投影的 NUM_WANN。</p>
 {numWann!==undefined&&<p>当前窗口检查使用 num_wann=<strong className="pyw90-key-number">{numWann}</strong>。{ratioBasis==='auto'?'此处比例使用自动建议轨道。':'本节只显示自动投影建议；候选比例使用本次明确显示的轨道集合。'}</p>}
 <p className="pyw90-projections">自动建议投影：{projections.join('；')||'未选中轨道'}</p>
 <details><summary>完整轨道积分与选择依据 · {selection.orbitals.length} 行</summary><p>在所选能量范围积分，按最大单轨道积分归一化{selection.normalization_integral!==undefined&&selection.normalization_integral!==null?`（分母 ${selection.normalization_integral.toFixed(6)}）`:''}；lb={selection.lb}，选取归一化积分 &gt; {selection.threshold} 的轨道。建议用于手动选择投影，不代表拟合质量。</p>
 <div className="pyw90-table-scroll"><table className="pyw90-table"><thead><tr><th>元素</th><th>站点索引（从 0 开始）</th><th>轨道</th><th>轨道 DOS 积分</th><th>归一化积分</th><th>是否选中</th></tr></thead><tbody>{selection.orbitals.map((o,i)=><tr key={i}><td>{o.species}</td><td>{o.structure_id}</td><td>{o.orb_name}</td><td>{typeof o.integral==='number'&&Number.isFinite(o.integral)?o.integral.toFixed(6):'旧记录未保留'}</td><td>{typeof o.dos==='number'&&Number.isFinite(o.dos)?o.dos.toFixed(6):'不可确定'}</td><td>{o.selected?'是':'否'}</td></tr>)}</tbody></table></div></details></>;
}

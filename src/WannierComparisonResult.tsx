import type {Project} from './model';
import {attemptLabel} from './humanLabels';
import AnalysisDatasetRegister from './AnalysisDatasetRegister';

type ErrorStats={mae_meV:number|null;rmse_meV:number|null;max_abs_diff_meV:number|null;paired_values:number};
type Group=ErrorStats & {wannier_first:number;wannier_last:number;dft_first:number;dft_last:number};
export type Comparison={id:string;title?:string;wannier_attempt_id:string;band_attempt_id:string;seed:string;fermi_eV:number;local_path:string;created_at:string;note:string;
 diagnostics?:ErrorStats & {status:string;revision:string;dft_band_first:number;dft_band_last:number;matched_bands:number;expected_bands:number;metric_window_relative_eV:number[];reference_values:number;reference:{id:string;selection:string};groups:Group[]};
 pyw90_diagnostic?:{status:string;reason?:string;quality_value?:number;quality_unit?:string;dft_band_first?:number;dft_band_last?:number;pyw90_version?:string;smoothing?:unknown;kernel?:string;metric_window_relative_eV?:number[]}};

const number=(value:number|null|undefined)=>value==null?'—':value.toFixed(3);
export default function WannierComparisonResult({record,project,bandLabel,busy,onDiagnose}:{record:Comparison;project:Project;bandLabel?:string;busy:boolean;onDiagnose:()=>void}){
 const metrics=record.diagnostics,native=record.pyw90_diagnostic;
 const status=({PENDING:'计算中',COMPLETED:'完成',NOT_REQUESTED:'未计算',UNAVAILABLE:'不可用',NOT_APPLICABLE:'不适用',FAILED:'未完成'} as Record<string,string>)[native?.status||'NOT_REQUESTED'];
 return <div className="result-dataset-card">
  <h4>{record.title||'DFT–Wannier 能带叠图'} · {record.seed}</h4>
  <p>DFT {bandLabel||attemptLabel({kind:'bands'},project)} · E_F {record.fermi_eV.toFixed(6)} eV</p>
  <img className="local-plot" src={`/api/wannier90/comparisons/${record.id}/files/comparison.png?project_id=${encodeURIComponent(project.id)}&v=${native?.status||'old'}`} alt="DFT 与 Wannier90 能带叠加图及诊断摘要"/>
  {metrics&&<section aria-label="固定目标带段误差">
   <p>固定 DFT 带 {metrics.dft_band_first}–{metrics.dft_band_last} · 参考覆盖 {metrics.matched_bands}/{metrics.expected_bands} 条 · 评价范围 {metrics.metric_window_relative_eV.join(' ～ ')} eV</p>
   <div className="pyw90-summary">{(['mae_meV','rmse_meV','max_abs_diff_meV'] as const).map((key,i)=><div className="pyw90-metric" key={key}><span>{['MAE','RMSE','最大偏差'][i]} / meV</span><strong className="pyw90-metric-value">{number(metrics[key])}</strong></div>)}</div>
   {metrics.status==='PARTIAL'&&<p className="handoff-warning">参考带不完整，仅保留局部统计；不与完整模型的总分比较。</p>}
   {metrics.status==='EMPTY'&&<p>评价范围内没有参考样本。</p>}
   <details><summary>分组误差 · {metrics.groups.length} 组</summary><div className="pyw90-table-scroll"><table className="pyw90-table"><thead><tr><th>Wannier 带</th><th>DFT 带</th><th>样本</th><th>MAE / meV</th><th>RMSE / meV</th><th>最大偏差 / meV</th></tr></thead><tbody>{metrics.groups.map(group=><tr key={group.wannier_first}><td>{group.wannier_first}–{group.wannier_last}</td><td>{group.dft_first}–{group.dft_last}</td><td>{group.paired_values}</td><td>{number(group.mae_meV)}</td><td>{number(group.rmse_meV)}</td><td>{number(group.max_abs_diff_meV)}</td></tr>)}</tbody></table></div></details>
  </section>}
  <p className="pyw90-status" role="status">PyW90 原生评分：{native?.quality_value!=null?`${number(native.quality_value)} ${native.quality_unit||'meV'}`:status}{native?.dft_band_first!=null?` · 自动匹配 DFT 带 ${native.dft_band_first}–${native.dft_band_last}`:''}</p>
  {native?.reason&&<p>{native.reason}</p>}
  <div className="saved-plot-links">{(metrics?['comparison.png','comparison.svg','curves.csv','diagnostics.json','per_band.csv','groups.csv']:['comparison.png','comparison.svg','curves.csv']).map(name=><a className="button" key={name} href={`/api/wannier90/comparisons/${record.id}/files/${name}?project_id=${encodeURIComponent(project.id)}`} download>{name}</a>)}{metrics&&!['COMPLETED','NOT_APPLICABLE'].includes(native?.status||'')&&<button className="button" disabled={busy} onClick={onDiagnose}>补充 PyW90 评分</button>}</div>
  <details><summary>评价方法与来源</summary><p>主指标固定带段和 DFT 采样点，逐路径段线性插值，不做平滑；偏出范围的 Wannier 误差仍保留。</p><p>原生评分独立匹配带段，使用 AbAk 和平滑加权，不与主指标混为同一评分。</p><pre>{JSON.stringify({reference:metrics?.reference,revision:metrics?.revision,native,created_at:record.created_at},null,2)}</pre><button className="button" onClick={()=>void navigator.clipboard.writeText(record.local_path)}>复制本机路径</button></details>
  <AnalysisDatasetRegister kind="comparison" sourceId={record.id} state="COMPLETED"/>
 </div>;
}

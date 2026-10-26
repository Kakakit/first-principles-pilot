import type {Project} from './model';
import {attemptLabel} from './humanLabels';
import AnalysisDatasetRegister from './AnalysisDatasetRegister';

type ErrorStats={mae_meV:number|null;rmse_meV:number|null;max_abs_diff_meV:number|null;paired_values:number};
type Group=ErrorStats & {wannier_first:number;wannier_last:number;dft_first:number;dft_last:number;matched_bands?:number;expected_bands?:number};
export type Comparison={id:string;title?:string;wannier_attempt_id:string;band_attempt_id:string;seed:string;fermi_eV:number;local_path:string;created_at:string;note:string;
 diagnostics?:ErrorStats & {status:string;revision:string;dft_band_first:number;dft_band_last:number;matched_bands:number;expected_bands:number;partial_metrics?:ErrorStats|null;metric_window_relative_eV:number[];reference_values:number;reference:{id:string;selection:string};groups:Group[]};
 pyw90_diagnostic?:{status:string;reason?:string;quality_value?:number;quality_unit?:string;dft_band_first?:number;dft_band_last?:number;band_matching?:string;pyw90_version?:string;smoothing?:unknown;kernel?:string;metric_window_relative_eV?:number[]}};

const number=(value:number|null|undefined)=>value==null?'—':value.toFixed(3);
export default function WannierComparisonResult({record,project,bandLabel,busy,onDiagnose}:{record:Comparison;project:Project;bandLabel?:string;busy:boolean;onDiagnose:()=>void}){
 const metrics=record.diagnostics,native=record.pyw90_diagnostic;
 const partial=metrics?.status==='PARTIAL',measured=partial?metrics.partial_metrics:metrics;
 const legacy=metrics?.revision==='fpilot.band-metrics.v1'&&metrics.reference.selection!=='user_selected';
 const status=({PENDING:'计算中',COMPLETED:'完成',NOT_REQUESTED:'未计算',UNAVAILABLE:'不可用',NOT_APPLICABLE:'不适用',FAILED:'未完成'} as Record<string,string>)[native?.status||'NOT_REQUESTED'];
 return <div className="result-dataset-card">
  <h4>{record.title||'DFT–Wannier 能带叠图'} · {record.seed}</h4>
  <p>DFT {bandLabel||attemptLabel({kind:'bands'},project)} · E_F {record.fermi_eV.toFixed(6)} eV</p>
  <img className="local-plot" src={`/api/wannier90/comparisons/${record.id}/files/comparison.png?project_id=${encodeURIComponent(project.id)}&v=${native?.status||'old'}`} alt="DFT 与 Wannier90 能带叠加图及诊断摘要"/>
  {metrics&&<section aria-label="固定目标带段误差">
   <p>目标 DFT 带 {metrics.dft_band_first}–{metrics.dft_band_last} · 参考覆盖 {metrics.matched_bands}/{metrics.expected_bands} 条 · 评价范围 {metrics.metric_window_relative_eV.join(' ～ ')} eV</p>
   {legacy&&<p className="handoff-warning">旧版自动匹配记录；重新生成叠图可使用修复后的匹配，原图保留。</p>}
   {partial&&<p className="handoff-warning">以下仅统计已覆盖的 DFT 带 {metrics.matched_bands?`${metrics.dft_band_first}–${metrics.dft_band_first+metrics.matched_bands-1}`:'（无）'}；缺少 {metrics.expected_bands-metrics.matched_bands} 条参考，不代表完整模型总分。</p>}
   <div className="pyw90-summary">{(['mae_meV','rmse_meV','max_abs_diff_meV'] as const).map((key,i)=><div className="pyw90-metric" key={key}><span>{partial?'局部 ':''}{['MAE','RMSE','最大偏差'][i]} / meV</span><strong className="pyw90-metric-value">{number(measured?.[key])}</strong></div>)}</div>
   {metrics.status==='EMPTY'&&<p>评价范围内没有参考样本。</p>}
   <details><summary>分组误差 · {metrics.groups.length} 组</summary><div className="pyw90-table-scroll"><table className="pyw90-table"><thead><tr><th>Wannier 带</th><th>DFT 带</th><th>参考覆盖</th><th>样本</th><th>MAE / meV</th><th>RMSE / meV</th><th>最大偏差 / meV</th></tr></thead><tbody>{metrics.groups.map(group=><tr key={group.wannier_first}><td>{group.wannier_first}–{group.wannier_last}</td><td>{group.dft_first}–{group.dft_last}</td><td>{group.matched_bands??'—'}/{group.expected_bands??group.wannier_last-group.wannier_first+1}</td><td>{group.paired_values}</td><td>{number(group.mae_meV)}</td><td>{number(group.rmse_meV)}</td><td>{number(group.max_abs_diff_meV)}</td></tr>)}</tbody></table></div></details>
  </section>}
  <p className="pyw90-status" role="status">PyW90 AbAk 评分：{legacy?'旧版匹配结果，请重新生成叠图':partial?'参考不完整，不计算总分':native?.status==='COMPLETED'&&native.quality_value!=null?`${number(native.quality_value)} ${native.quality_unit||'meV'}`:status}{!partial&&!legacy&&native?.dft_band_first!=null?` · ${native.band_matching==='caller_fixed_reference'?'固定':'自动匹配'} DFT 带 ${native.dft_band_first}–${native.dft_band_last}`:''}</p>
  {!partial&&native?.reason&&<p>{native.reason}</p>}
  <div className="saved-plot-links">{(metrics?['comparison.png','comparison.svg','curves.csv','diagnostics.json','per_band.csv','groups.csv']:['comparison.png','comparison.svg','curves.csv']).map(name=><a className="button" key={name} href={`/api/wannier90/comparisons/${record.id}/files/${name}?project_id=${encodeURIComponent(project.id)}`} download>{name}</a>)}{metrics&&!partial&&!legacy&&!['COMPLETED','NOT_APPLICABLE'].includes(native?.status||'')&&<button className="button" disabled={busy} onClick={onDiagnose}>补充 PyW90 评分</button>}</div>
  <details><summary>评价方法与来源</summary><p>主指标固定带段和 DFT 采样点，逐路径段线性插值，不做平滑；偏出范围的 Wannier 误差仍保留。</p><p>新版 PyW90 AbAk 沿用同一参考带段，并使用平滑加权；不与未平滑主指标混为同一评分。参考不完整时仅显示局部统计。</p><pre>{JSON.stringify({reference:metrics?.reference,revision:metrics?.revision,native,created_at:record.created_at},null,2)}</pre><button className="button" onClick={()=>void navigator.clipboard.writeText(record.local_path)}>复制本机路径</button></details>
  <AnalysisDatasetRegister kind="comparison" sourceId={record.id} state="COMPLETED"/>
 </div>;
}

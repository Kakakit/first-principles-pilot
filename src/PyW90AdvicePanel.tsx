import {attemptLabel} from './humanLabels';
import {useEffect,useRef,useState} from 'react';
import type {Project} from './model';
import {requestJson,errorMessage} from './apiClient';
import {candidateWindows,draftWindows,windowTags,type Advice,type Candidate,type Diagnostic,type Gap,type MeshReport,type WindowCheck,type Windows} from './pyw90Advice';
import './pyw90-advice.css';

type EnergyReference={fermi_eV:number;outcar_sha256:string};
type Props={project:Project;nodeId:string;parentId:string;alias:string;seed:string;sourceReady:boolean;energyReference:EnergyReference|null;parameters:Record<string,string>;win:string;dosChoices:{id:string;node_id:string;job_id?:string}[];onAdopt:(values:Record<string,number>)=>void};
const number=(value:number|null|undefined)=>value==null?'未启用':value.toFixed(6);
const channel=(value:string)=>({spinor:'SOC／非共线 spinor 单通道',down:'下自旋',up:'上自旋',single:'单通道'}[value]||value);
const lowerDescription:Record<string,string>={crossing:'穿过能带范围',gap:'位于全局带间隙',below_all:'低于全部已提供能带',above_all:'高于全部已提供能带',no_resolved_gap:'未检测到大于 0.004 eV 的间隙'};

function WindowSummary({windows,changed=[]}:{windows:WindowCheck['relative_windows_eV'];changed?:string[]}){
  const labels=['外窗下界','外窗上界','冻结窗下界','冻结窗上界'];
  return <div className="pyw90-window-summary" aria-label="窗口边界 E−E_F / eV">{windowTags.map((tag,i)=><div key={tag} className={changed.includes(tag)?'pyw90-window changed':'pyw90-window'}><span>{labels[i]} {changed.includes(tag)&&<strong>将改变</strong>}</span><strong className="pyw90-window-value">{number(windows[tag])}</strong><span>{tag} · E−E_F / eV</span></div>)}</div>;
}

function Summary({report}:{report:MeshReport}){
  const {basis,current}=report;
  return <><div className="pyw90-summary" aria-label="调窗重点摘要">
    <div className="pyw90-metric"><span>当前 num_wann</span><strong className="pyw90-metric-value">{basis.num_wann}</strong><span>{channel(basis.channel)}</span></div>
    <div className="pyw90-metric"><span>完整外窗最少态数</span><strong className="pyw90-metric-value">{current.outer.min}</strong><span>{current.outer.min>=basis.num_wann?'满足':'不满足'} ≥ {basis.num_wann} · k #{current.outer.min_kpoint}</span></div>
    <div className="pyw90-metric"><span>完整冻结窗最多态数</span><strong className="pyw90-metric-value">{current.frozen?.max??'未启用'}</strong><span>{current.frozen?`${current.frozen.max<=basis.num_wann?'满足':'不满足'} ≤ ${basis.num_wann} · k #${current.frozen.max_kpoint}`:'没有冻结态约束'}</span></div>
    <div className="pyw90-metric"><span>分析下界 / 当前外窗下界</span><strong className="pyw90-metric-classification">{report.analysis_lower.classification==='crossing'?'分析下界穿带':'分析下界未穿带'}</strong><span>当前外窗：{lowerDescription[report.outer_lower.classification]||report.outer_lower.classification}</span></div>
  </div><p className={`pyw90-constraint ${current.valid?'passed':'failed'}`} role="status">{current.valid?'窗口约束通过':'窗口约束未通过'} · 来自完整 .eig 采样网格；拟合质量需另做能带对比。</p><WindowSummary windows={current.relative_windows_eV}/></>;
}

function CountsView({value}:{value:WindowCheck}){
  return <div className="pyw90-counts"><p>完整外窗逐 k 点：最少 <strong>{value.outer.min}</strong> 个态（k #{value.outer.min_kpoint}），最多 {value.outer.max} 个态。</p>
    <p>{value.frozen?<>完整冻结窗逐 k 点：最多 <strong>{value.frozen.max}</strong> 个态（k #{value.frozen.max_kpoint}），最少 {value.frozen.min} 个态。</>:'未启用冻结窗；没有冻结态约束。'}</p>
    {value.errors.length?<ul className="structure-error">{value.errors.map(error=><li key={error}>{error}</li>)}</ul>:<p>窗口约束通过。拟合质量仍需能带对比。</p>}
    {!value.analysis_range_covered&&<p className="handoff-warning">外窗未覆盖整个分析范围。分析范围不自动等同于拟合目标，请核对要保留的态。</p>}
    {!!value.defaulted_tags.length&&<p>沿用程序默认或未启用：{value.defaulted_tags.join('、')}；下表显示实际检查值。</p>}
  </div>;
}

function GapView({gap,fermi}:{gap:Gap;fermi:number}){
  return <span className="pyw90-gap"><strong>带 {gap.lower_band}／{gap.upper_band}</strong>：<strong>{number(gap.i_max-fermi)} ～ {number(gap['i+1_min']-fermi)} eV</strong><span>宽 {number(gap.width_eV)} eV · 中点 {number(gap.midpoint_eV-fermi)} eV</span></span>;
}

function LowerView({label,value,fermi}:{label:string;value:Diagnostic;fermi:number}){
  return <div className="pyw90-lower"><h5>{label}：<strong>{number(value.value_eV-fermi)} eV</strong> · {lowerDescription[value.classification]||value.classification}</h5>
    {value.crossing_bands.length>0&&<p>穿过的带号与全网格范围：{value.crossing_bands.map(b=>`#${b.band} [${number(b.min_eV-fermi)}, ${number(b.max_eV-fermi)}] eV`).join('；')}。穿带本身不判定拟合失败。</p>}
    {value.containing_gap&&<p>所在间隙：<GapView gap={value.containing_gap} fermi={fermi}/></p>}
    {value.nearest_below&&<p>下方最近间隙：<GapView gap={value.nearest_below} fermi={fermi}/></p>}
    {value.nearest_above&&<p>上方最近间隙：<GapView gap={value.nearest_above} fermi={fermi}/></p>}
  </div>;
}

function Report({report,onGap}:{report:MeshReport;onGap?:(gap:Gap)=>void}){
  return <div className="pyw90-report"><Summary report={report}/><p>检查范围：{report.nkpts} 个 k 点 · {report.nbands} 条能带。以下能量为 E−E_F，单位 eV；带号与 k 点号从 1 开始。</p>
    <LowerView label="分析下界" value={report.analysis_lower} fermi={report.fermi_eV}/><LowerView label="当前外窗下界" value={report.outer_lower} fermi={report.fermi_eV}/>
    <p>分析范围逐 k 点态数：{report.analysis_counts.min} ～ {report.analysis_counts.max}。</p><CountsView value={report.current}/>
    <details><summary>检查方法、当前投影与绝对能量</summary><p>判断来自接口 .eig 的全部已提供 k 点；高对称路径图与完整布里渊区可能不同。</p><p>当前投影：{report.basis.projections.join('；')||'无显式 projections'}{report.basis.exclude_bands&&<> · exclude_bands：{report.basis.exclude_bands}</>}</p><div className="pyw90-table-scroll"><table className="pyw90-table"><thead><tr><th>当前检查窗口</th><th>E−E_F / eV</th><th>绝对能量 / eV</th></tr></thead><tbody>{windowTags.map(tag=><tr key={tag}><td>{tag}</td><td>{number(report.current.relative_windows_eV[tag])}</td><td>{number(report.current.absolute_windows_eV[tag])}</td></tr>)}</tbody></table></div></details>
    <details><summary>全部全局带间隙 · {report.gaps.length} 个（阈值 0.004 eV）</summary>{report.gaps.length?report.gaps.map(gap=><p key={gap.lower_band}><GapView gap={gap} fermi={report.fermi_eV}/> {onGap&&<button className="button" onClick={()=>onGap(gap)}>预览作为外窗下界</button>}</p>):<p>当前网格没有检测到满足阈值的相邻带间隙；仍可手动设置窗口并检查态数。</p>}</details>
  </div>;
}

export default function PyW90AdvicePanel(props:Props){
  const {project,nodeId,parentId,alias,seed,sourceReady,energyReference,parameters,win,dosChoices,onAdopt}=props;
  const [emin,setEmin]=useState('-4'),[emax,setEmax]=useState('4'),[dosId,setDosId]=useState('');
  const [advice,setAdvice]=useState<Partial<Record<'eig'|'pre',Advice>>>({}),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [manual,setManual]=useState<{report:MeshReport;fingerprint:string}|null>(null);
  const [preview,setPreview]=useState<{report:MeshReport;values:Windows;before:Windows;fingerprint:string}|null>(null);
  const mounted=useRef(true);
  const previewElement=useRef<HTMLElement|null>(null);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  const fingerprint=JSON.stringify([parameters,win,emin,emax,dosId,energyReference]);
  const currentFingerprint=useRef(fingerprint);currentFingerprint.current=fingerprint;
  useEffect(()=>{setPreview(null);},[fingerprint]);
  useEffect(()=>{if(preview)previewElement.current?.scrollIntoView({block:'nearest',behavior:'smooth'});},[preview]);
  const enabled=sourceReady&&!!energyReference&&!busy&&emin.trim()!==''&&emax.trim()!==''&&Number.isFinite(Number(emin))&&Number.isFinite(Number(emax))&&Number(emin)<Number(emax);
  const payload=(windows:Windows)=>({project,node_id:nodeId,parent_id:parentId,alias,seed,emin:Number(emin),emax:Number(emax),energy_reference:energyReference,relative_windows:windows});
  const run=async(mode:'eig'|'pre'|'check')=>{
    if(!energyReference)return;
    const started=fingerprint;setBusy(true);setError('');setPreview(null);
    try{
      const windows=draftWindows(parameters,win,energyReference.fermi_eV);
      if(mode==='check'){
        const report:MeshReport=await requestJson('/api/wannier90/pyw90-window-check',payload(windows));
        if(mounted.current&&started===currentFingerprint.current)setManual({report,fingerprint:started});
      }else{
        const result:Advice=await requestJson('/api/wannier90/pyw90-advice',{...payload(windows),mode,dos_attempt_id:mode==='pre'&&dosId?dosId:null});
        if(mounted.current&&started===currentFingerprint.current){setAdvice(old=>({...old,[mode]:result}));setManual({report:result.interface,fingerprint:started});}
      }
    }catch(caught){if(mounted.current)setError(errorMessage(caught));}finally{if(mounted.current)setBusy(false);}
  };
  const select=async(values:Windows,sourceHashes?:Record<string,string>)=>{
    if(!energyReference)return;
    const started=fingerprint;setBusy(true);setError('');setPreview(null);
    try{
      const before=draftWindows(parameters,win,energyReference.fermi_eV),selected=candidateWindows(values);
      const report:MeshReport=await requestJson('/api/wannier90/pyw90-window-check',{...payload({...before,...selected}),expected_source_hashes:sourceHashes});
      if(mounted.current&&started===currentFingerprint.current)setPreview({report,values:selected,before,fingerprint:started});
    }catch(caught){if(mounted.current)setError(errorMessage(caught));}finally{if(mounted.current)setBusy(false);}
  };
  const candidates=(item:Advice,group:'outer'|'frozen')=>{
    const columns=group==='outer'?['dis_win_min','dis_win_max','i_max','i+1_min','Nleast','Nmost']:item.mode==='pre'?['dis_froz_min','dis_froz_max','N','pdos','tdos','percent']:['dis_froz_min','dis_froz_max','N'];
    return <div><h5>{group==='outer'?'外窗候选':'冻结窗候选'} · {item.advice[group].length} 组</h5>
      <details className="pyw90-method"><summary>候选来源与计数含义</summary><p>{group==='outer'?'下界来自费米能级以下的全局间隙中点；按间隙上边缘筛选，不是最近位置或拟合最优排序。Nleast／Nmost 的区间为候选下界到 E_F，Nmost 为能带范围交叠计数。原生筛选要求 Nmost<num_wann，假设还要拟合费米能级以上的带。':'原生 N 按能带范围交叠计数，可能偏保守；完整窗口另用接口逐 k 点计数核对。'}{item.mode==='pre'&&group==='frozen'&&' pdos／tdos 使用下方自动选轨道模型；比例不是拟合误差，排序也不代表拟合质量。'}</p></details>
      {item.advice[group].length?<div className="pyw90-table-scroll"><table className="pyw90-table"><thead><tr>{columns.map(col=><th key={col}>{col==='percent'?'所选轨道贡献比例':['N','Nleast','Nmost'].includes(col)?`原生 ${col}`:col}<small>{windowTags.includes(col as typeof windowTags[number])?'E−E_F / eV':['i_max','i+1_min'].includes(col)?'间隙边缘 · E−E_F / eV':''}</small></th>)}<th>完整窗口逐 k 点</th><th>操作</th></tr></thead><tbody>{item.advice[group].map((row:Candidate,i)=><tr key={i}>{columns.map(col=><td key={col} className={windowTags.includes(col as typeof windowTags[number])?'pyw90-window-cell':['N','Nleast','Nmost'].includes(col)?'pyw90-native-count':undefined}>{typeof row[col]!=='number'?'—':col==='percent'?`${((row[col] as number)*100).toFixed(2)}%`:['N','Nleast','Nmost'].includes(col)?row[col] as number:number(row[col] as number)}</td>)}<td>{row.check.gap&&<><GapView gap={row.check.gap} fermi={item.fermi_eV}/><br/></>}<div className="pyw90-full-counts">完整 .eig：外窗最少 <strong>{row.check.outer.min}</strong>；冻结窗最多 <strong>{row.check.frozen?.max??'未启用'}</strong></div><span className={row.check.valid?'pyw90-valid':'structure-error'}>{row.check.valid?'窗口约束通过':'窗口约束未通过'}</span>{row.check.errors.map(e=><p className="structure-error" key={e}>{e}</p>)}</td><td><button className="button" disabled={!enabled} onClick={()=>void select(row,item.interface_source_hashes)}>预览这组建议</button></td></tr>)}</tbody></table></div>:<p>未生成候选。候选为空不等于当前冻结窗无效；可以检查当前草稿。</p>}
    </div>;
  };
  return <section className="input-submit-card pyw90-advice"><h3>PyW90 手动调窗辅助</h3><p>分析、选取候选、检查完整窗口，再填入参数草稿。实际拟合效果请结合 DFT／Wannier 能带对比。</p>
    <div className="parameter-grid"><label className="field">分析范围下界 / E−E_F (eV)<input type="number" value={emin} onChange={e=>setEmin(e.target.value)}/></label><label className="field">分析范围上界 / E−E_F (eV)<input type="number" value={emax} onChange={e=>setEmax(e.target.value)}/></label><label className="field">pre dos 来源<select value={dosId} onChange={e=>setDosId(e.target.value)}><option value="">当前 VASP-Wannier attempt</option>{dosChoices.map(item=><option value={item.id} key={item.id}>{attemptLabel(item,project)}</option>)}</select></label></div>
    <div className="pyw90-actions"><button className="button" disabled={!enabled} onClick={()=>void run('check')}>检查当前草稿窗口与下界</button><button className="button" disabled={!enabled} onClick={()=>void run('eig')}>远端 eig：外窗候选</button><button className="button" disabled={!enabled} onClick={()=>void run('pre')}>远端 pre dos：投影与冻结窗候选</button></div>
    {busy&&<p role="status">正在只读核对远端文件与采样网格…</p>}{error&&<p role="alert" className="structure-error">{error}</p>}
    {manual&&<><h4>下界与完整窗口诊断</h4>{manual.fingerprint!==fingerprint&&<p className="handoff-warning">草稿或分析设置已变化；以下为上次检查结果，请重新检查。</p>}<Report report={manual.report} onGap={enabled?gap=>void select({dis_win_min:gap.midpoint_eV-manual.report.fermi_eV},manual.report.source_hashes):undefined}/></>}
    {(['eig','pre'] as const).map(mode=>{const item=advice[mode];if(!item)return null;const selection=item.advice.dos_selection;return <div key={mode} className="pyw90-result"><h4>{mode==='eig'?'eig suggest':'pre dos'} · {item.seed}</h4><p className="pyw90-reference">E_F {number(item.fermi_eV)} eV · 分析范围 {item.relative_range_eV.join(' 至 ')} eV · PyW90 {item.pyw90_version}</p><p>{item.note}</p>
      {selection&&<><p>PDOS 自动建议：{selection.spatial_orbitals} 个空间轨道，<strong className="pyw90-key-number">{selection.suggested_num_wann}</strong> 个函数；当前窗口检查使用 num_wann=<strong className="pyw90-key-number">{item.interface.basis.num_wann}</strong>。</p>{selection.suggested_num_wann!==item.interface.basis.num_wann&&<p className="handoff-warning">PDOS 建议模型与当前函数数不同。比例评价的是自动选择的轨道；窗口态数检查使用当前接口模型。</p>}<p className="pyw90-projections">自动建议投影：{item.advice.projections?.join('；')||'未选中轨道'}</p><details><summary>完整轨道积分与选择依据 · {selection.orbitals.length} 行</summary><p>阈值 lb={selection.lb}，单轨道积分按最大值归一化，选取归一化积分 &gt; {selection.threshold}。空间轨道数与函数数按当前自旋模型换算。</p><div className="pyw90-table-scroll"><table className="pyw90-table"><thead><tr><th>元素</th><th>站点索引（从 0 开始）</th><th>轨道</th><th>归一化积分</th><th>是否选中</th></tr></thead><tbody>{selection.orbitals.map((o,i)=><tr key={i}><td>{o.species}</td><td>{o.structure_id}</td><td>{o.orb_name}</td><td>{number(o.dos)}</td><td>{o.selected?'是':'否'}</td></tr>)}</tbody></table></div></details></>}
      {item.advice.eigenval&&<p>DOS EIGENVAL：{item.advice.eigenval.spinors?'SOC／非共线单通道':channel(item.advice.eigenval.channel)} · {item.advice.eigenval.nkpts} 个 k 点 · {item.advice.eigenval.nbands} 条带。候选最终按接口 .eig 核对。</p>}
      {item.advice.eigenval?.spinors&&<p>SOC／非共线 PDOS 使用总电荷密度；三个磁化分量不作为上下自旋能带。每个空间轨道对应两个 spinor 投影，不假设能带二重简并。</p>}
      {item.advice.minimum_outer_max!==undefined&&<p>保持分析下界时，PyW90 给出的外窗上界至少为 <strong className="pyw90-key-number">{number(item.advice.minimum_outer_max)} eV</strong>；下界穿带时此算法可能偏保守。 <button className="button" disabled={!enabled} onClick={()=>void select({dis_win_min:item.relative_range_eV[0],dis_win_max:item.advice.minimum_outer_max},item.interface_source_hashes)}>预览此固定下界外窗</button></p>}
      {candidates(item,'outer')}{candidates(item,'frozen')}
      <details><summary>原始命令、完整输出、警告与来源身份</summary><p>使用进程内适配器，不修改服务器安装包或研究文件。命令中的 -e 为绝对能量。</p>{item.runs.map((run,i)=><div key={i}><pre className="input-text">{run.arguments.join(' ')}</pre><pre className="input-text">{run.stdout}</pre><pre className="input-text">{run.stderr||'stderr：无输出'}</pre></div>)}<pre className="input-text">{JSON.stringify({source_attempt_id:item.source_attempt_id,interface:item.interface_source_hashes,analysis:item.source_hashes},null,2)}</pre></details>
    </div>;})}
    {preview&&preview.fingerprint===fingerprint&&<section ref={previewElement} className="pyw90-preview" aria-label="采用窗口建议前预览"><h4>采用前预览 · num_wann={preview.report.basis.num_wann}</h4><WindowSummary windows={preview.report.current.relative_windows_eV} changed={Object.keys(preview.values)}/><p>将改变 {Object.keys(preview.values).join('、')}；其余边界沿用当前草稿。确认后再点击“应用参数到 .win”。</p><div className="pyw90-table-scroll"><table className="pyw90-table"><thead><tr><th>参数</th><th>当前草稿 E−E_F / eV</th><th>采用后 E−E_F / eV</th></tr></thead><tbody>{windowTags.map(tag=><tr key={tag}><td>{tag}</td><td>{preview.before[tag]===undefined?'沿用默认 / 未启用':number(preview.before[tag])}</td><td className={Object.hasOwn(preview.values,tag)?'pyw90-window-cell changed':'pyw90-window-cell'}>{number(preview.report.current.relative_windows_eV[tag])}</td></tr>)}</tbody></table></div><details><summary>采用后绝对能量 / eV</summary><dl>{windowTags.map(tag=><div key={tag}><dt>{tag}</dt><dd>{number(preview.report.current.absolute_windows_eV[tag])}</dd></div>)}</dl></details><CountsView value={preview.report.current}/><button className="button primary" disabled={busy||!preview.report.current.valid} onClick={()=>{onAdopt(preview.values as Record<string,number>);setPreview(null);}}>确认填入参数草稿</button><button className="button" onClick={()=>setPreview(null)}>取消</button></section>}
  </section>;
}

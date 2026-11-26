import {useState} from 'react';
import {useSummaryCollection} from './useSummaryCollection';
import {formatValue,metricDefinitions,type ResultSummary} from './resultWorkspace';

export default function CompletedMetrics({projectId,attemptId}:{projectId:string;attemptId:string}){
 const [summary,setSummary]=useState<ResultSummary|null>(null);
 const collection=useSummaryCollection(projectId,attemptId,true,setSummary,true);
 if(!summary?.metrics)return <p className="summary-muted" role="status">{collection.message} {collection.failed&&<button className="text-button" onClick={()=>collection.refresh()}>重试采集</button>}</p>;
 return <><div className="workflow-metrics">{metricDefinitions.filter(([key])=>key!=='energy_per_atom').map(([key,label,unit])=>{
   const metric=summary.metrics?.[key];
   return metric&&typeof metric==='object'&&Number.isFinite(metric.value)?<div key={key}><span>{label}</span><strong>{formatValue(metric.value)}</strong><small>{metric.unit||unit}</small></div>:null;
 })}</div>{!summary.metrics.magnetization&&<small className="summary-muted">OUTCAR 未记录晶胞磁矩</small>}</>;
}

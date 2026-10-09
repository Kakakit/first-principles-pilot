import type {FrozenInput,Scalar} from './resultWorkspace';

export const latticeMetrics=[
 ['lattice_a','a','Å'],['lattice_b','b','Å'],['lattice_c','c','Å'],
 ['lattice_alpha','α','°'],['lattice_beta','β','°'],['lattice_gamma','γ','°'],['volume','晶胞体积','Å³']
] as const;
export type LatticeMetric=(typeof latticeMetrics)[number][0];
export type LatticeMetrics=Partial<Record<LatticeMetric,Scalar>>&{lattice_error?:string;ionic_converged?:boolean|null};
export type LatticeRow={attempt_id:string;node_id:string;job_id?:string;stage?:string;program_done:boolean;ediff_reached?:boolean;metrics?:LatticeMetrics|null;input?:FrozenInput};
export const isLatticeMetric=(key:string):key is LatticeMetric=>latticeMetrics.some(([name])=>name===key);
export function relativeChange(value:unknown,reference:unknown):number|null{
 return typeof value==='number'&&Number.isFinite(value)&&typeof reference==='number'&&Number.isFinite(reference)&&reference>0?(value-reference)/reference*100:null;
}
export function latticeVerified(row:LatticeRow):boolean{
 return row.program_done&&row.ediff_reached===true&&(row.stage!=='relax'||row.metrics?.ionic_converged===true);
}
export function latticeStatus(row:LatticeRow):string{
 if(!row.metrics)return '尚未读取';
 if(row.metrics.lattice_error)return '晶格未提取';
 if(!row.program_done)return '程序结束未确认';
 if(row.ediff_reached!==true)return '电子收敛未确认';
 return row.stage==='relax'?(row.metrics.ionic_converged===true?'优化已收敛':'离子收敛未确认'):'非结构优化阶段';
}
const changeKeys=['lattice_a','lattice_b','lattice_c','volume'] as const;
export function latticeCsv(rows:LatticeRow[],referenceId:string,label:(row:LatticeRow)=>string):string{
 const reference=rows.find(row=>row.attempt_id===referenceId);
 const header=['结果','attempt_id','阶段','数值状态',...latticeMetrics.map(([,name,unit])=>name+' / '+unit),'参考结果',...changeKeys.map(key=>'Δ'+(key==='volume'?'V':key.slice(-1))+' / %'),'实际 INCAR 参数','来源主机','来源文件','来源 SHA-256','晶格读取说明'];
 const body=rows.map(row=>{
  const source=row.metrics?.lattice_a?.source;
  return [label(row),row.attempt_id,row.stage||'',latticeStatus(row),...latticeMetrics.map(([key])=>row.metrics?.[key]?.value??''),reference?label(reference):'',
   ...changeKeys.map(key=>relativeChange(row.metrics?.[key]?.value,reference?.metrics?.[key]?.value)??''),
   JSON.stringify(row.input?.parameters||{}),source?.host||'',source?.file||'',source?.sha256||'',row.metrics?.lattice_error||source?.method||'尚未读取'];
 });
 const cell=(value:string|number)=>'"'+(typeof value==='string'&&/^[=+\-@]/.test(value)?"'"+value:String(value)).replace(/"/g,'""')+'"';
 return '\uFEFF'+[header,...body].map(row=>row.map(cell).join(',')).join('\r\n')+'\r\n';
}

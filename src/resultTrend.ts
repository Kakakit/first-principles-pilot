import {isLatticeMetric} from './latticeMetrics';
type Value = {value:number;unit:string;source?:{sha256?:string}}|null|undefined;
export type TrendRow = {stage?:string;model_sha256?:string;cell_composition_sha256?:string;potcar_sha256?:string;program_done:boolean;ediff_reached?:boolean;metrics?:unknown};

// A line implies comparability. Missing values, unverified runs, and model changes break it.
export function trendSegments(rows:TrendRow[],metric:string):Array<Array<{index:number;value:number}>> {
  const segments:Array<Array<{index:number;value:number}>>=[];
  let current:Array<{index:number;value:number}>=[];
  let previous:TrendRow|null=null;
  let previousUnit='';
  const lattice=isLatticeMetric(metric);
  for(const [index,row] of rows.entries()) {
    const item=(row.metrics as Record<string,Value>|null|undefined)?.[metric];
    const ionic=(row.metrics as {ionic_converged?:boolean}|null|undefined)?.ionic_converged;
    const usable=Boolean(item&&Number.isFinite(item.value)&&item.unit&&item.source?.sha256&&row.program_done&&row.ediff_reached===true&&(!lattice||row.stage!=='relax'||ionic===true));
    const model=lattice?row.cell_composition_sha256:row.model_sha256,previousModel=lattice?previous?.cell_composition_sha256:previous?.model_sha256;
    const comparable=usable&&previous&&row.stage===previous.stage&&Boolean(row.stage)&&model===previousModel&&Boolean(model)&&row.potcar_sha256===previous.potcar_sha256&&Boolean(row.potcar_sha256)&&item!.unit===previousUnit;
    if(!comparable){if(current.length>1)segments.push(current);current=[];}
    if(usable)current.push({index,value:item!.value});
    previous=usable?row:null;previousUnit=usable?item!.unit:'';
  }
  if(current.length>1)segments.push(current);
  return segments;
}

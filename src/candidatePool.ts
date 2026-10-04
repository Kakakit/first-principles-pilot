import type {ServerPreview,ServerProbe,ProbeNode} from './executionUi';
export type Candidate={node:string;cores:number;reason:string};
export function poolNodes(probe:ServerProbe|null,alias?:string):ProbeNode[]{
 return (probe?.preflight.hosts.flatMap(h=>h.nodes)||[]).filter(n=>!(alias==='jutai-1'&&n.NODE==='node06'));
}
export function fullNodeCandidates(nodes:ProbeNode[],selected:string[]):Candidate[]{
 return selected.map(name=>{
  const n=nodes.find(n=>n.NODE===name),cores=Number(n?.CPUS_TOTAL||0);
  if(!n||!Number.isInteger(cores)||cores<1)throw new Error(name+' 的总核数未知，请重新检测');
  return {node:name,cores,reason:''};
 });
}
export function capacityWait(value:ServerPreview){
 return ['BUSY','FULLY_IDLE','PARTIAL'].includes(value.node_status||'')&&Boolean(value.blockers.length)&&value.blockers.every(item=>['selected node CPUS_FREE must be greater than zero','selected node STATUS must be FULLY_IDLE or PARTIAL','user override cannot exceed CPUS_FREE'].some(prefix=>item.startsWith(prefix)));
}

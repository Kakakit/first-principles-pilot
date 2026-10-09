import {capacityWait} from './candidatePool';
import type {ServerPreview} from './executionUi';

export function chainResourceIssues(steps:{node_id:string;label:string}[],nodes:string[],previews:Record<string,Record<string,ServerPreview>>){
 return steps.flatMap(step=>nodes.flatMap(node=>{
  const value=previews[step.node_id]?.[node];
  if(value&&(value.resource_ready||capacityWait(value))&&value.script&&value.profile)return [];
  const reasons=value?.blockers?.length?value.blockers:['未返回完整的资源与脚本预览，请重新检测节点'];
  return [{nodeId:step.node_id,label:step.label,node,reasons}];
 }));
}

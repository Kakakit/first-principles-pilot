import type {Project} from './model';

export type FrozenInputChoice = {id:string;node_id?:string;execution_scope?:string;source_contract?:{mode:string};status:string;stage?:string};
export const isReviewableInput = (item:FrozenInputChoice) =>
  (!item.execution_scope||item.execution_scope===item.node_id)&&item.source_contract?.mode!=='planned'&&(
  ['ready-for-execution-review','pending-restart'].includes(item.status) ||
  (item.status==='pending-dependencies' && ['relax','scf','dos','bands','vasp-wannier'].includes(item.stage||'')));

// An explicit handoff always wins. Multiple historical versions require a choice.
// Never substitute a different node or version if the requested input is unavailable.
export function inputForReview(inputs:FrozenInputChoice[],nodeId?:string|null,requestedId?:string|null):string {
  if(!nodeId)return '';
  const candidates=inputs.filter(item=>item.node_id===nodeId&&isReviewableInput(item));
  if(requestedId)return candidates.some(item=>item.id===requestedId)?requestedId:'';
  return candidates.length===1?candidates[0].id:'';
}

export function configurationNodes(project:Project) {
  return project.nodes.filter(node=>['relax','scf','bands','dos','vasp-wannier','wannier90','scan','program'].includes(node.data.kind));
}

export function chainNodes(project:Project,selectedIds?:string[]) {
  return project.nodes.filter(node=>['relax','scf','bands','dos','vasp-wannier'].includes(node.data.kind)&&
    (selectedIds===undefined||selectedIds.includes(node.id)));
}

// The preview resolves actual source files. Before it exists, the graph can identify
// direct upstream calculations for which historical results may be needed.
export function chainUpstreamCandidates(project:Project,executionIds:string[]){
 const selected=new Set(executionIds),eligible=new Set(chainNodes(project).map(n=>n.id));
 return new Set(project.edges.filter(e=>selected.has(e.target)&&!selected.has(e.source)&&eligible.has(e.source)).map(e=>e.source));
}

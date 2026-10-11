import {makeStep,type Project} from './model';
import {upstreamStructure} from './parameters';
export function independentBranch(project:Project,nodeId:string):Project {
 const start=project.nodes.find(n=>n.id===nodeId);
 if(!start)throw new Error('请先选择要复制的节点');
 const ids=new Set([nodeId]);
 if(start.data.kind==='scan'){const base=project.edges.find(e=>e.target===nodeId)?.source;if(base)ids.add(base);}
 let changed=true;while(changed){changed=false;for(const edge of project.edges)if(ids.has(edge.source)&&!ids.has(edge.target)){ids.add(edge.target);changed=true;}}
 const mapping=new Map<string,string>([...ids].map(id=>[id,crypto.randomUUID()]));
 const root=makeStep('structure',{x:40,y:120},1);
 const structure=upstreamStructure(project,start);
 if(structure)root.data.structure=structuredClone(structure);
 const nodes=project.nodes.filter(n=>ids.has(n.id)&&n.data.kind!=='structure').map((n,index)=>{
  const draft={...(n.data.inputDraft as Record<string,unknown>||{})};
  for(const key of Object.keys(draft))if(/attempt|source|hash|bundle|authorization|remote|probe/i.test(key))delete draft[key];
  const data={...structuredClone(n.data),inputDraft:draft,sourceDraft:{mode:'new',purpose:'charge',files:'charge'},stageSequence:index+2};
  for(const key of Object.keys(data))if(/authorization|binding|attempt|result|prepared|chainState|execution_scope|remote|wannierDrafts|activeWannierDraft|windowAdoption/i.test(key))delete (data as Record<string,unknown>)[key];
  return {...n,id:mapping.get(n.id)!,selected:false,data};
 });
 const kept=new Set(nodes.map(n=>n.id));
 const edges=project.edges.filter(e=>mapping.has(e.source)&&mapping.has(e.target)).map(e=>({...e,id:crypto.randomUUID(),source:mapping.get(e.source)!,target:mapping.get(e.target)!})).filter(e=>kept.has(e.source)&&kept.has(e.target));
 for(const node of nodes)if(!edges.some(e=>e.target===node.id))edges.push({id:crypto.randomUUID(),source:root.id,target:node.id});
 for(const node of nodes){
  let parent=nodes.find(n=>n.id===edges.find(e=>e.target===node.id)?.source);
  if(node.data.kind==='scan'&&parent)parent=nodes.find(n=>n.id===edges.find(e=>e.target===parent!.id)?.source);
  node.data.sourceDraft={mode:parent?'upstream':'new',purpose:'charge',files:'charge'};
 }
 const id=crypto.randomUUID();
 return {id,name:project.name+' · 独立分支',remoteSlug:'project-'+id.slice(0,8),nodes:[root,...nodes],edges,parameters:structuredClone(project.parameters),sharedPotcar:structuredClone(project.sharedPotcar),updatedAt:new Date().toISOString()};
}

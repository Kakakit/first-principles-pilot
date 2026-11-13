import type {Project,Step} from './model';

export type ContinuationCreated={project_id:string;node:Step;edge:Project['edges'][number];batch:{id:string;state:string;case_count:number}};

export function attachContinuation(project:Project,result:ContinuationCreated):Project{
 if(result.project_id!==project.id)throw new Error('项目已切换，请在来源项目打开此后续批次');
 const existing=project.nodes.find(node=>node.id===result.node.id);
 if(existing){
  if(existing.data.continuationBatchId!==result.batch.id)throw new Error('后续节点身份冲突，请重新读取项目');
  return project;
 }
 if(!project.nodes.some(node=>node.id===result.edge.source))throw new Error('来源节点已移除，请先恢复来源节点');
 if(project.nodes.length>=500||project.edges.length>=2000)throw new Error('项目节点或连线已达上限');
 return {...project,nodes:[...project.nodes,result.node],edges:[...project.edges,result.edge]};
}

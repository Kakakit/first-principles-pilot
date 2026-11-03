export type TaskNavigation={projectId:string;page:string;nodeId:string|null;attemptId:string;runScope:'node'|'chain'};
export type TaskDestination={projectId?:string;page:string;nodeId?:string|null;attemptId?:string;runScope?:'node'|'chain'};

/** Keep the selected record only when the destination still names the same task. */
export function taskDestination(current:TaskNavigation,target:TaskDestination):TaskNavigation{
 const nodeId=target.nodeId===undefined?current.nodeId:target.nodeId;
 const projectId=target.projectId??current.projectId;
 return {projectId,page:target.page==='overview'?'flow':target.page,nodeId,
  attemptId:target.attemptId??(projectId===current.projectId&&nodeId===current.nodeId?current.attemptId:''),
  runScope:target.runScope??current.runScope};
}

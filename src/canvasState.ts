import type {Node,NodeChange} from '@xyflow/react';

export type CanvasMeasurements={projectId:string;dimensions:Map<string,{width:number;height:number}>};

// React Flow needs measured dimensions back on every controlled-node update.
// Keep them separate from the saved research graph and its undo snapshots.
export function updateCanvasMeasurements<T extends Node>(previous:CanvasMeasurements,projectId:string,nodes:T[],changes:NodeChange<T>[]):CanvasMeasurements {
  const ids=new Set(nodes.map(node=>node.id));
  const dimensions=new Map(previous.projectId===projectId?[...previous.dimensions].filter(([id])=>ids.has(id)):[]);
  let changed=previous.projectId!==projectId||dimensions.size!==previous.dimensions.size;
  for(const change of changes){
    if(change.type!=='dimensions'||!change.dimensions||!ids.has(change.id))continue;
    const {width,height}=change.dimensions;
    if(!Number.isFinite(width)||!Number.isFinite(height)||width<=0||height<=0)continue;
    const old=dimensions.get(change.id);
    if(old?.width!==width||old?.height!==height){dimensions.set(change.id,{width,height});changed=true;}
  }
  return changed?{projectId,dimensions}:previous;
}

export function measuredCanvasNodes<T extends Node>(nodes:T[],projectId:string,measurements:CanvasMeasurements):T[] {
  return nodes.map(node=>{
    const measured=measurements.projectId===projectId?measurements.dimensions.get(node.id):undefined;
    return measured?{...node,measured}:node;
  });
}

import type {Workspace} from './model';
export type UndoEntry={workspace:Workspace;bytes:number};
// Workspace edits are immutable. Share original structures rather than copying them 30 times.
export function rememberWorkspace(history:UndoEntry[],workspace:Workspace):UndoEntry[]{
 const entry={workspace,bytes:new TextEncoder().encode(JSON.stringify(workspace)).length};
 const result=[...history.slice(-29),entry];let size=result.reduce((total,item)=>total+item.bytes,0);
 while(size>16*1024*1024&&result.length){size-=result.shift()!.bytes;}
 return result;
}

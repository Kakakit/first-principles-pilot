import {cleanWorkspace} from './model';
import type {Workspace} from './model';

export const AUTOSAVE_IDLE_MS=30_000;
export const AUTOSAVE_MAX_WAIT_MS=120_000;
export const autosaveDelay=(dirtySince:number,now:number)=>Math.max(0,Math.min(AUTOSAVE_IDLE_MS,AUTOSAVE_MAX_WAIT_MS-(now-dirtySince)));

export class WorkspaceRequestError extends Error {
  constructor(message:string, public retryable=false, public status=0){super(message);}
}

export async function workspaceRequest(init?:RequestInit, timeoutMs=15_000):Promise<any>{
  const controller=new AbortController();
  const abort=()=>controller.abort();
  if(init?.signal?.aborted)abort();
  else init?.signal?.addEventListener('abort',abort,{once:true});
  const timer=setTimeout(abort,timeoutMs);
  try{
    const response=await fetch('/api/workspace',{...init,signal:controller.signal});
    let body;
    try{body=await response.json();}
    catch{throw new WorkspaceRequestError('本机工作区响应暂不可读，编辑内容保留。',response.ok||response.status>=500,response.status);}
    if(!response.ok)throw new WorkspaceRequestError(typeof body.detail==='string'?body.detail:'本机工作区请求失败。',response.status>=500,response.status);
    return body;
  }catch(error){
    if(error instanceof WorkspaceRequestError)throw error;
    throw new WorkspaceRequestError('本机连接中断或等待超时，编辑内容保留，正在等待恢复。',true);
  }finally{clearTimeout(timer);init?.signal?.removeEventListener('abort',abort);}
}

export function workspaceSerializer(){
  const cache=new WeakMap<Workspace,string>();
  return (workspace:Workspace)=>{
    let body=cache.get(workspace);
    if(body===undefined){body=JSON.stringify(cleanWorkspace(workspace));cache.set(workspace,body);}
    return body;
  };
}

function equalJSON(a:any,b:any):boolean{
  if(a===b)return true;
  if(!a||!b||typeof a!=='object'||typeof b!=='object'||Array.isArray(a)!==Array.isArray(b))return false;
  const keys=Object.keys(a);
  return keys.length===Object.keys(b).length&&keys.every(key=>Object.hasOwn(b,key)&&equalJSON(a[key],b[key]));
}

/** Resolve an uncertain PUT by reading the committed version before sending again. */
export class WorkspacePersistence {
  saved=''; revision=0; pendingBody:string|null=null;
  constructor(private request:typeof workspaceRequest=workspaceRequest){}
  initialize(body:string,revision:number){this.saved=body;this.revision=revision;this.pendingBody=null;}
  private async reconcile(){
    const disk=await this.request();
    const workspace=disk.workspace?cleanWorkspace(disk.workspace):null;
    const matches=(body:string|null)=>body?equalJSON(workspace,JSON.parse(body)):workspace===null;
    if(!matches(this.saved)&&!matches(this.pendingBody))
      throw new WorkspaceRequestError('磁盘版本已被其他窗口修改。当前编辑保留，请导出或核对后重新载入。',false,409);
    if(matches(this.pendingBody))this.saved=this.pendingBody||'';
    this.revision=disk.revision;this.pendingBody=null;
  }
  async save(body:string){
    if(this.pendingBody!==null)await this.reconcile();
    if(body===this.saved)return;
    this.pendingBody=body;
    try{
      const result=await this.request({method:'PUT',headers:{'Content-Type':'application/json'},body:'{"workspace":'+body+',"expected_revision":'+this.revision+',"reason":"autosave"}'});
      this.saved=body;this.revision=result.revision;this.pendingBody=null;
    }catch(error){
      if(error instanceof WorkspaceRequestError&&error.status===409){
        await this.reconcile();
        if(this.saved===body)return;
        throw error;
      }
      if(error instanceof WorkspaceRequestError&&!error.retryable)this.pendingBody=null;
      throw error;
    }
  }
}

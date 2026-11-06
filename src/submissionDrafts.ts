/** Bounded UI cache. Frozen contracts, confirmations and authorization stay on disk. */
export class SubmissionDraftCache{
 private entries=new Map<string,{value:unknown;bytes:number}>();
 private bytes=0;
 constructor(private maxEntries=256,private maxBytes=8*1024*1024){}
 get<T>(key:string,initial:T):T{
  const item=this.entries.get(key);if(!item)return initial;
  this.entries.delete(key);this.entries.set(key,item);return item.value as T;
 }
 set(key:string,value:unknown){
  this.remove(key);
  const bytes=(JSON.stringify(value)||'').length*2;
  if(bytes>this.maxBytes)return;
  this.entries.set(key,{value,bytes});this.bytes+=bytes;
  while(this.entries.size>this.maxEntries||this.bytes>this.maxBytes)this.remove(this.entries.keys().next().value!);
 }
 delete(key:string){this.remove(key);}
 hasDraft(prefix=''){
  for(const [key,{value}] of this.entries){
   if(key.startsWith(prefix)&&value!==null&&value!==undefined&&
     (typeof value!=='object'||Object.keys(value).length>0))return true;
  }
  return false;
 }
 private remove(key:string){const old=this.entries.get(key);if(old)this.bytes-=old.bytes;this.entries.delete(key);}
 prune(projects:{id:string;nodes:{id:string}[]}[]){
  const live=new Map(projects.map(p=>[p.id,new Set(p.nodes.map(n=>n.id))]));
  for(const key of this.entries.keys()){
   const [kind,projectId,nodeId]=key.split(':');const nodes=live.get(projectId);
   if(!nodes||!['chain','properties'].includes(kind)&&!nodes.has(nodeId))this.remove(key);
  }
 }
 get size(){return this.entries.size;}
}
export const submissionDrafts=new SubmissionDraftCache();
export const settingsDrafts=new SubmissionDraftCache(100,512*1024);

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

/** Only editable selections may survive a tab reload. Validate stored JSON on read. */
export class RunPreferences {
 private values=new Map<string,unknown>();
 constructor(private workspace:string,private storage?:Storage){
  try{
   const raw=storage?.getItem(this.storageKey);
   if(!raw||raw.length>256*1024)return;
   const rows:unknown=JSON.parse(raw);
   if(Array.isArray(rows))for(const row of rows.slice(-256)){
    if(Array.isArray(row)&&this.accepts(row[0],row[1]))this.values.set(row[0],row[1]);
   }
  }catch{/* Browser storage can be unavailable. */}
 }
 private get storageKey(){return 'fpilot.run-preferences.v1:'+this.workspace;}
 private accepts(key:unknown,value:unknown){
  if(typeof key!=='string'||key.length>500)return false;
  if(/^(chain:[^:]+:selection|(chain:[^:]+|scan:[^:]+:[^:]+):pool)$/.test(key))
   return Array.isArray(value)&&value.length<=256&&value.every(v=>typeof v==='string'&&v.length<=200);
  if(/^scan:[^:]+:[^:]+:maxParallel$/.test(key))return Number.isInteger(value)&&Number(value)>0;
  return /^(single:[^:]+:[^:]+:(bundle|template|node|cores|reason|alias|program|mode)|chain:[^:]+:alias|scan:[^:]+:[^:]+:alias)$/.test(key)&&typeof value==='string'&&value.length<=2000;
 }
 get<T>(key:string,fallback:T):T{return this.values.has(key)?this.values.get(key) as T:fallback;}
 set(key:string,value:unknown){
  if(!this.accepts(key,value)||this.values.get(key)===value)return;
  this.values.delete(key);this.values.set(key,value);
  this.save();
 }
 prune(projects:{id:string;nodes:{id:string}[]}[]){
  const live=new Map(projects.map(p=>[p.id,new Set(p.nodes.map(n=>n.id))]));
  let changed=false;
  for(const key of this.values.keys()){
   const [kind,id,node]=key.split(':');
   if(!live.has(id)||(kind!=='chain'&&!live.get(id)?.has(node))){this.values.delete(key);changed=true;}
  }
  if(changed)this.save();
 }
 private save(){
  let raw=JSON.stringify([...this.values]);
  while(this.values.size>256||raw.length>256*1024){this.values.delete(this.values.keys().next().value!);raw=JSON.stringify([...this.values]);}
  try{this.storage?.setItem(this.storageKey,raw);}catch{/* Keep in-memory editing available. */}
 }
}

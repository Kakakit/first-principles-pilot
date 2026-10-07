export type ExtensionSource={kind:'structure';poscar:string;name?:string;original_sha256?:string;files?:Record<string,string>}|{kind:'attempt';attempt_id:string;files?:Record<string,string>};
export type ExtensionManifest={id:string;name:string;version:string;description:string;installed:boolean;enabled:boolean;error?:string;contexts:string[];stages:string[];sources:{min:number;max:number};inputs:Array<{role:string;title:string;names:string[];max_bytes:number;required?:boolean}>;execution:{locations:string[];timeout_seconds:number;preferred_location?:string};dependencies:string[];doctor?:{ready:boolean;missing:string[];reason:string;python?:string};options_schema:Record<string,any>;ui_schema:Record<string,any>;blockers?:string[];runtime?:PythonRuntime;runtime_doctor?:EnvironmentDoctor};
export type ResolvedSource={id:string;name:string;stage:string;kind:string;files:Array<{name:string;size_bytes:number}>;alias?:string;state:string;sha256?:string;input_summary?:{seed?:string;parent_attempt_id?:string;parent_name?:string;elements?:string[];spinors?:boolean|string;num_wann?:number;energy_reference?:{fermi_eV?:number;attempt_id?:string}}};
export type Presentation={id:string;status:'GENERATING'|'READY'|'FAILED';version:string;error?:string;manifest?:{entry_html:string;assets:Array<{path:string;media_type:string}>}};
export type Analysis={id:string;state:string;presentation?:Presentation;presentation_supported?:boolean;last_checked_at?:string;refresh_message?:string;refresh_busy?:boolean;created_at:string;extension_id:string;manifest:ExtensionManifest;location:string;job_id?:string;remote_dir?:string;runtime?:PythonRuntime;runtime_doctor?:EnvironmentDoctor;error?:string;check?:{status:string;reason:string};sources:Array<{id:string;name:string;sha256?:string;attempt_id?:string}>;scientific_validation:string;result?:{summary:string;warnings:string[];tables:Array<{title:string;columns:Array<{key:string;label:string;unit:string}>;rows:Record<string,unknown>[]}>;curves:Array<{title:string;x_label:string;x_unit:string;y_label:string;y_unit:string;series:Array<{label:string;points:number[][]}>}>};outputs:Array<{path:string;label:string;media_type:string;size_bytes:number;available:boolean}>};
export function defaults(schema:Record<string,any>):any {
 if('default' in schema)return structuredClone(schema.default);
 if(schema.type==='object')return Object.fromEntries(Object.entries(schema.properties||{}).flatMap(([key,value])=>{const v=defaults(value as Record<string,any>);return v===undefined?[]:[[key,v]];}));
 return undefined;
}
export const runningAnalysis=(state:string)=>['QUEUED','RUNNING','DISPATCHING','SUBMITTED','WAIT_RESOURCE'].includes(state);
export const analysisLabel=(state:string)=>({QUEUED:'等待本机执行',RUNNING:'分析中',DISPATCHING:'准备远端分析',SUBMITTED:'已提交，等待结束证据',WAIT_RESOURCE:'等待资源',UNKNOWN:'状态待核对',COMPLETED:'分析程序完成',NOT_APPLICABLE:'输入不适用',FAILED:'分析失败'}[state]||state);
export const manageExtensions=()=>window.dispatchEvent(new Event('fpilot:manage-extensions'));
export type EnvironmentDoctor={ready:boolean;python?:string;python_version?:string;version?:string;versions?:Record<string,string>;missing?:string[];reason?:string};
export type PythonRuntime={python:string;inherited?:boolean;alias?:string;scope?:string};
export type EnvironmentData={local:PythonRuntime;runtimes:Record<string,PythonRuntime>;overrides:Record<string,{local?:string;remote?:Record<string,string>}>};
export function executionCategories(manifest:Pick<ExtensionManifest,'execution'>):string[]{
 return [...new Set((manifest.execution?.locations||[]).map(mode=>mode==='local'?'local':'remote'))];
}
export function runtimeChoice(id:string,location:string,alias:string,data:EnvironmentData):PythonRuntime|undefined {
 const base=location==='local'?data.local:data.runtimes[alias];if(!base)return undefined;
 const override=location==='local'?data.overrides[id]?.local:data.overrides[id]?.remote?.[alias];
 return {...base,python:override||base.python,inherited:!override};
}

export function preferredLocation(manifest:Pick<ExtensionManifest,'execution'>):string {
 const {locations=[],preferred_location}=manifest.execution||{};
 return preferred_location&&locations.includes(preferred_location)?preferred_location:locations.includes('local')?'local':locations[0];
}
export function environmentVersion(doctor:EnvironmentDoctor):string {
 return [doctor.python_version&&`Python ${doctor.python_version}`,doctor.version&&`版本 ${doctor.version}`,...Object.entries(doctor.versions||{}).map(([name,version])=>`${name} ${version}`)].filter(Boolean).join(' · ');
}
export function sourceRuntime(sourceAlias:string|undefined,runtimes:Record<string,unknown>):string {
 return sourceAlias?(sourceAlias in runtimes?sourceAlias:''):Object.keys(runtimes)[0]||'';
}

export function sourceOptionsSchema(manifest:Pick<ExtensionManifest,'options_schema'|'ui_schema'>,sources:ResolvedSource[]):Record<string,any> {
 const schema=structuredClone(manifest.options_schema);
 const elements=[...new Set(sources.flatMap(source=>source.input_summary?.elements||[]))];
 for(const [key,ui] of Object.entries(manifest.ui_schema)){
  if((ui as Record<string,any>)?.['ui:options']?.source_enum==='elements'&&schema.properties?.[key]?.type==='array'&&elements.length){
   schema.properties[key].items={...schema.properties[key].items,enum:elements};
  }
 }
 return schema;
}

export function readableSource(value:string|undefined,fallback='运行来源'):string {
 return value&&!/^[a-f0-9]{12,64}$/i.test(value)?value:fallback;
}

export function formAvailability(busy:boolean,blocked:boolean,running:boolean){
 return {editingDisabled:busy,actionsDisabled:busy||blocked,submitDisabled:busy||blocked||running};
}

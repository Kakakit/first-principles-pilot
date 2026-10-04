export type ExtensionSource={kind:'structure';poscar:string;name?:string;original_sha256?:string;files?:Record<string,string>}|{kind:'attempt';attempt_id:string;files?:Record<string,string>};
export type ExtensionManifest={id:string;name:string;version:string;description:string;installed:boolean;enabled:boolean;error?:string;contexts:string[];stages:string[];sources:{min:number;max:number};inputs:Array<{role:string;title:string;names:string[];max_bytes:number}>;execution:{locations:string[];timeout_seconds:number};dependencies:string[];doctor?:{ready:boolean;missing:string[];reason:string;python?:string};options_schema:Record<string,any>;ui_schema:Record<string,any>;blockers?:string[]};
export type ResolvedSource={id:string;name:string;stage:string;kind:string;files:Array<{name:string;size_bytes:number}>;alias?:string;state:string;sha256?:string};
export type Analysis={id:string;state:string;created_at:string;extension_id:string;manifest:ExtensionManifest;location:string;job_id?:string;remote_dir?:string;error?:string;check?:{status:string;reason:string};sources:Array<{id:string;name:string;sha256?:string;attempt_id?:string}>;scientific_validation:string;result?:{summary:string;warnings:string[];tables:Array<{title:string;columns:Array<{key:string;label:string;unit:string}>;rows:Record<string,unknown>[]}>;curves:Array<{title:string;x_label:string;x_unit:string;y_label:string;y_unit:string;series:Array<{label:string;points:number[][]}>}>};outputs:Array<{path:string;label:string;media_type:string;size_bytes:number;available:boolean}>};
export function defaults(schema:Record<string,any>):any {
 if('default' in schema)return structuredClone(schema.default);
 if(schema.type==='object')return Object.fromEntries(Object.entries(schema.properties||{}).flatMap(([key,value])=>{const v=defaults(value as Record<string,any>);return v===undefined?[]:[[key,v]];}));
 return undefined;
}
export const runningAnalysis=(state:string)=>['QUEUED','RUNNING','DISPATCHING','SUBMITTED','WAIT_RESOURCE'].includes(state);
export const analysisLabel=(state:string)=>({QUEUED:'等待本机执行',RUNNING:'分析中',DISPATCHING:'准备远端分析',SUBMITTED:'已提交，等待结束证据',WAIT_RESOURCE:'等待资源',UNKNOWN:'状态待核对',COMPLETED:'分析程序完成',NOT_APPLICABLE:'输入不适用',FAILED:'分析失败'}[state]||state);
export const manageExtensions=()=>window.dispatchEvent(new Event('fpilot:manage-extensions'));

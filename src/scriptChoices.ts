import type {ServerConfig} from './executionUi';
export type ResourceRequest={mode:'gpu';allocation:'whole_node';mpi_ranks:number|null;gpus:number|null;devices?:string[];gpu_type?:string};
export type ScriptRecord={id:string;name:string;alias:string;scheduler:string;program:string;source:string;original_text?:string;resources?:Record<string,string|number>;resource_request?:ResourceRequest;compatible_programs?:string[];availability?:string;parent_id?:string;program_conflict?:string;verification_status?:'candidate'|'historical'|'user_imported'};
export const programLabels:Record<string,string>={vasp_std:'VASP 常规',vasp_ncl:'VASP SOC／非共线',gpu_vasp:'GPU VASP',wannier90:'Wannier90',other:'其他程序'};
export const schedulerName=(value:string)=>(value||'pbs').toLowerCase().replace('pbs/torque','pbs').replace('torque','pbs');
export function scriptFits(script:ScriptRecord,alias:string,scheduler:string,program:string){
 return Boolean(script.alias===alias&&schedulerName(script.scheduler)===schedulerName(scheduler)&&(schedulerName(scheduler)==='custom'||script.program===program||program==='vasp_std'&&script.program==='gpu_vasp'||!script.program_conflict&&script.compatible_programs?.includes(program)));
}
export function variantLabel(script:ScriptRecord,program:string){
 const original=script.program==='gpu_vasp'?'vasp_std':script.program;
 return original!==program&&script.compatible_programs?.includes(program)?`${original} → ${program}（同目录）`:'';
}
export function fillScriptSelections(config:ServerConfig,programs:string[],scripts:ScriptRecord[]):ServerConfig {
 if(config.runtime_source!=='script')return config;
 let choices:Record<string,string>={};try{choices=JSON.parse(config.script_templates||'{}');}catch{/* A new selection replaces an invalid draft map. */}
 if(config.template_id)choices[config.program||programs[0]]=config.template_id;
 const preferred=Object.values(choices);let changed=false;
 for(const program of programs){
  if(choices[program])continue;
  const eligible=scripts.filter(s=>!s.program_conflict&&scriptFits(s,config.alias,config.scheduler,program));
  const shared=eligible.find(s=>preferred.includes(s.id)),record=shared||(eligible.length===1?eligible[0]:undefined);
  if(record){choices[program]=record.id;changed=true;}
 }
 return changed?{...config,template_id:choices[programs[0]]||'',program:programs[0],vasp_executable:programs[0],script_templates:JSON.stringify(choices)}:config;
}
export function resourceFor(config:ServerConfig,program:string,fallback?:ResourceRequest):ResourceRequest|undefined {
 try {const saved=JSON.parse(config.resource_request||'{}');const override=saved.by_program?saved.by_program[program]:saved;const value={...fallback,...override};return value.mode==='gpu'?value:undefined;}catch{return fallback;}
}
export function withResource(config:ServerConfig,program:string,request:ResourceRequest|undefined):ServerConfig {
 let choices:Record<string,ResourceRequest>={};try{choices=JSON.parse(config.resource_request||'{}').by_program||{};}catch{/* A new draft replaces unreadable UI state. */}
 const next={...choices};if(request)next[program]=request;else delete next[program];
 const result={...config};if(Object.keys(next).length)result.resource_request=JSON.stringify({by_program:next});else delete result.resource_request;return result;
}
export function importedChoice(config:ServerConfig,program:string,script:ScriptRecord):ServerConfig {
 const next=withResource({...config,runtime_source:'script',program,vasp_executable:program,template_id:script.id},program,script.resource_request);
 return next;
}

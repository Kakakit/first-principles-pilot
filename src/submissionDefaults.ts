import type {RegisteredProfile,ServerConfig} from './executionUi';
export type WorkbenchSettings={version:number;runtimes:Record<string,ServerConfig>;defaults:Record<string,string|null>;history_limit:number};
export function runtimeFor(config:ServerConfig,profiles:RegisteredProfile[],settings:WorkbenchSettings,alias:string,program='vasp_std'):ServerConfig {
 const profile=profiles.find(item=>item.alias===alias);
 const registered=profile?{...config,alias,scheduler:profile.scheduler.toLowerCase(),queue:profile.queue,remote_workspace_root:profile.remote_workspace_root||'$HOME/230269265lht',mpi_executable:profile.mpi_executable,vasp_executable:program==='vasp_ncl'?profile.ncl_executable||'':profile.vasp_executable,environment_script:profile.environment_script,module:profile.module,launcher:profile.launcher,runtime_environment:profile.runtime_environment,pmi_library:profile.pmi_library,pmi_type:profile.pmi_type}:config;
 const next:ServerConfig={...registered,...settings.runtimes[alias+':'+program],program};
 delete next.template_id;delete next.script_templates;
 delete next.resource_request;
 if(settings.runtimes[alias+':'+program]?.resource_request)next.resource_request=settings.runtimes[alias+':'+program].resource_request;
 // An explicitly saved registered ncl runtime takes precedence over a std fallback.
 const template=settings.defaults[alias+':'+program]||(program==='vasp_ncl'&&!settings.runtimes[alias+':'+program]?settings.defaults[alias+':vasp_std']:undefined);
 if(template)return {...next,runtime_source:'script',vasp_executable:program,template_id:template};
 if(profile?.runtime_source==='script'||settings.runtimes[alias+':'+program]?.runtime_source==='script')return {...next,runtime_source:'script',vasp_executable:program};
 return {...next,runtime_source:'registered'};
}
export const emptySettings:WorkbenchSettings={version:1,runtimes:{},defaults:{},history_limit:5};
export type SettingsContext={section:string;alias?:string;program?:string;focus?:'scripts';returnToRun?:boolean};
export function openSettings(section='server',context:Omit<SettingsContext,'section'>={}){window.dispatchEvent(new CustomEvent('fpilot:settings',{detail:{section,...context}}));}

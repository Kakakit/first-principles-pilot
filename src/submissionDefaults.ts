import type {RegisteredProfile,ServerConfig} from './executionUi';
export type WorkbenchSettings={version:number;runtimes:Record<string,ServerConfig>;defaults:Record<string,string|null>;history_limit:number};
export function runtimeFor(config:ServerConfig,profiles:RegisteredProfile[],settings:WorkbenchSettings,alias:string,program='vasp_std'):ServerConfig {
 const profile=profiles.find(item=>item.alias===alias);
 const registered=profile?{...config,alias,scheduler:profile.scheduler.toLowerCase(),queue:profile.queue,mpi_executable:profile.mpi_executable,vasp_executable:program==='vasp_ncl'?profile.ncl_executable||'':profile.vasp_executable,environment_script:profile.environment_script,module:profile.module,launcher:profile.launcher,runtime_environment:profile.runtime_environment,pmi_library:profile.pmi_library,pmi_type:profile.pmi_type}:config;
 return {...registered,...settings.runtimes[alias+':'+program]};
}
export const emptySettings:WorkbenchSettings={version:1,runtimes:{},defaults:{},history_limit:5};
export function openSettings(section='server'){window.dispatchEvent(new CustomEvent('fpilot:settings',{detail:section}));}

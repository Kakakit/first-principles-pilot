import type {Kind,Project,StepData} from './model';

export const STAGE_SEQUENCES:Record<Kind,number>={structure:1,relax:2,scf:3,bands:4,dos:5,'vasp-wannier':6,wannier90:7,scan:8};
export const VASP_KINDS=['relax','scf','bands','dos','vasp-wannier'] as const;
export function nextStageSequence(project:Project,kind:Kind){
  return project.stagePolicy==='kind-fixed-v1'?STAGE_SEQUENCES[kind]:Math.max(0,...project.nodes.map(n=>n.data.stageSequence||0))+1;
}
export function staticDefaults(kind:Kind):Record<string,string>{
  return ['scf','bands','dos','vasp-wannier'].includes(kind)?{NSW:'0',IBRION:'-1',...(kind==='vasp-wannier'?{NCORE:'1'}:{})}:{};
}
export function clearExecutionBindings(original:StepData):StepData {
  const data=structuredClone(original);
  for(const key of Object.keys(data))if(/authorization|binding|attempt|result|prepared|chainState|execution_scope|remote|wannierDrafts|activeWannierDraft|windowAdoption|sourceDraft|audit|confirmation/i.test(key))delete data[key];
  if(data.parameters)delete data.parameters.confirmed_custom_tags;
  if(data.inputDraft){
    const draft={...(data.inputDraft as Record<string,string>)};
    for(const key of ['path_structure_hash','path_source_attempt_id','path_source_file','kmesh_info'])delete draft[key];
    data.inputDraft=draft;
  }
  return data;
}

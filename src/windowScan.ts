export const windowKeys=['dis_win_min','dis_win_max','dis_froz_min','dis_froz_max'] as const;
export type WindowKey=typeof windowKeys[number];
export type Phase='outer'|'frozen'|'recheck';
export const windowOrder:[Phase,WindowKey][]=[['outer','dis_win_max'],['outer','dis_win_min'],['frozen','dis_froz_max'],['frozen','dis_froz_min'],['recheck','dis_win_max'],['recheck','dis_win_min']];
export const windowLabel:Record<WindowKey,string>={dis_win_min:'外窗下沿',dis_win_max:'外窗上沿',dis_froz_min:'冻窗下沿',dis_froz_max:'冻窗上沿'};
export const phaseLabel:Record<Phase,string>={outer:'外窗测试',frozen:'冻窗测试',recheck:'外窗局部复查'};
export type WindowDraft={frozen_id:string;target_min:string;target_max:string;phase:Phase;boundary:WindowKey;ranges:Partial<Record<WindowKey,{lower:string;upper:string;step:string}>>;reason:string;previous_batch_id?:string;selected_case_id?:string;refine?:boolean;input_mode?:'range'|'list';values_text?:string};
export type WindowAdoption={node_id:string;source_frozen_id:string;parent_id:string;alias:string;seed:string;win:string;parameters:Record<string,string>;relative_windows:Record<string,number>;energy_reference:Record<string,unknown>;band_path:unknown;attempt_id:string;input_reason:string};
export const emptyWindowDraft=():WindowDraft=>({frozen_id:'',target_min:'',target_max:'',phase:'outer',boundary:'dis_win_max',ranges:{},reason:''});
export function windowRequest(draft:WindowDraft){const range=draft.ranges[draft.boundary];return {phase:draft.phase,boundary:draft.boundary,target:[Number(draft.target_min),Number(draft.target_max)],lower:Number(range?.lower),upper:Number(range?.upper),step:Number(range?.step),previous_batch_id:draft.previous_batch_id,selected_case_id:draft.selected_case_id,refine:!!draft.refine};}
export function windowDraftReady(draft:WindowDraft){const range=draft.ranges[draft.boundary];return !!draft.frozen_id&&!!range&&[range.lower,range.upper,range.step,draft.target_min,draft.target_max].every(x=>x.trim()&&Number.isFinite(Number(x)))&&Number(range.lower)<Number(range.upper)&&Number(range.step)>0&&Number(draft.target_min)<Number(draft.target_max);}
export const evidenceLabel=(value?:string)=>({confirmed:'已证实',not_satisfied:'未满足',criteria_disabled:'未启用判据',not_run:'未执行'}[value||'']||'未读取');

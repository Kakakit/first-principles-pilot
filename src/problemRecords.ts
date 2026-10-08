export type ProblemNotes={user_note:string;suspected_cause:string;confirmed_cause:string;confirmation_evidence:string};
export type ProblemRecord={id:string;source_kind:string;source_id:string;project_id:string;node_id?:string;project_name:string;node_name:string;program:string;category:string;state:string;server:string;occurred_at:string;error_summary:string;source_deleted:boolean;has_notes:boolean;notes:ProblemNotes;incar_tags:string[];excerpts:{file:string;location:string;text:string}[];evidence:{facts:unknown;logs:unknown;input:{incar?:string;kpoints?:string;parameter_origin?:string;warning?:string}}};
export const noteLabels={user_note:'用户备注',suspected_cause:'疑似原因',confirmed_cause:'已确认原因',confirmation_evidence:'确认依据（日志位置、复现或对照结果）'};
export function filterProblems(rows:ProblemRecord[],query:string,project:string,program:string,notes:string){
 const text=query.trim().toLowerCase();return rows.filter(r=>(!project||r.project_id===project)&&(!program||r.program===program)&&(notes==='all'||r.has_notes===(notes==='yes'))&&(!text||[r.project_name,r.node_name,r.program,r.server,r.error_summary,...Object.values(r.notes),...r.incar_tags].join(' ').toLowerCase().includes(text)));
}
export const openProblem=(id:string)=>window.dispatchEvent(new CustomEvent('fpilot:problem-record',{detail:{id}}));

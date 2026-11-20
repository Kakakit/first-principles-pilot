import type {TaskDestination,TaskNavigation} from './taskNavigation';
export type ActionIssue={code:string;message:string;level?:'error'|'warning';action?:'edit'|'inspect'|'retry';label?:string;target?:TaskDestination&{field?:string}};
export type IssueCheck=()=>Promise<ActionIssue[]>|ActionIssue[];
const checks=new Map<string,IssueCheck>();
let field='';
export const actionCheckKey=(action:string,projectId:string,nodeId:string)=>JSON.stringify([action,projectId,nodeId]);
export function registerActionCheck(key:string,check:IssueCheck){checks.set(key,check);return ()=>{if(checks.get(key)===check)checks.delete(key);};}
export function getActionCheck(key:string){return checks.get(key);}
export function showActionIssues(issues:ActionIssue[],checkKey?:string){
 const blocking=issues.filter(issue=>issue.level!=='warning');
 if(blocking.length)window.dispatchEvent(new CustomEvent('fpilot:action-issues',{detail:{issues:blocking,checkKey}}));
}
export function readActionIssues(value:unknown):ActionIssue[]{
 if(!Array.isArray(value))return [];
 return value.filter((item):item is ActionIssue=>!!item&&typeof item==='object'&&typeof item.code==='string'&&typeof item.message==='string');
}
export function issueFromError(error:unknown):ActionIssue[]{
 const issues=readActionIssues((error as {issues?:unknown})?.issues);
 return issues.length?issues:[{code:'request.failed',message:error instanceof Error?error.message:String(error),action:'retry',label:'返回后重试'}];
}
export function issueRoute(issue:ActionIssue,origin:TaskNavigation):TaskDestination{
 const {field,...target}=issue.target||{page:origin.page};
 return {...origin,...target,focus:field?'issue:'+field:undefined};
}
export function revealIssueField(value:string){field=value;window.dispatchEvent(new CustomEvent('fpilot:issue-field',{detail:value}));}
export function currentIssueField(){return field;}
export function runtimeIssueField(id:string,location:string,alias:string){return 'extension.runtime:'+encodeURIComponent(JSON.stringify([id,location,alias]));}
export function readRuntimeIssueField(field:string):{id:string;location:string;alias:string}|null{
 if(!field.startsWith('extension.runtime:'))return null;
 try{const [id,location,alias]=JSON.parse(decodeURIComponent(field.slice(18)));return typeof id==='string'&&['local','ssh'].includes(location)&&typeof alias==='string'?{id,location,alias}:null;}catch{return null;}
}

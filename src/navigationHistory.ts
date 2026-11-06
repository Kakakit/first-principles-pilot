import {taskDestination,type TaskDestination,type TaskNavigation} from './taskNavigation';

export const projectPages=['flow','structure','config','run','results','properties'] as const;
export const settingsSections=[
 ['appearance','通用与外观'],['server','服务器与运行脚本'],['data','数据与恢复'],
 ['config','POTCAR 库'],['operations','任务与监控'],['extensions','扩展与环境'],
] as const;
export function settingsSection(value?:string){
 if(value==='files')return 'config';
 if(value==='flow'||value==='structure')return 'data';
 return settingsSections.some(([id])=>id===value)?value!:'appearance';
}
const subpages:Record<string,readonly string[]>={config:['physics','source','files'],results:['summary','files','post','evidence','compare','handoff']};
const defaultRoute:TaskNavigation={projectId:'',page:'flow',nodeId:null,attemptId:'',runScope:'node'};
const projectPage=(value:string)=>projectPages.some(page=>page===value);
const programNames=['vasp_std','vasp_ncl','wannier90'];

export function normalizeLocation(route:TaskDestination):TaskNavigation{
 const page=route.page==='overview'?'flow':route.page;
 const settings=page==='settings';
 if(!projectPage(page)&&!['settings','datasets','problems'].includes(page))return {...defaultRoute,projectId:route.projectId||''};
 const node=projectPage(page)&&page!=='properties'?route.nodeId||null:null;
 const result:TaskNavigation={projectId:page==='problems'||settings?'':route.projectId||'',page,nodeId:node,
  attemptId:node&&page!=='properties'?route.attemptId||'':'',runScope:(page==='run'||page==='config')&&route.runScope==='chain'?'chain':'node'};
 if(subpages[page])result.subpage=subpages[page].includes(route.subpage||'')?route.subpage:subpages[page][0];
 if(settings){
  result.section=settingsSection(route.section);
  if(result.section==='server'){
   if(route.alias)result.alias=route.alias;
   if(programNames.includes(route.program||''))result.program=route.program;
  }
 }
 if(route.focus&&['settings','datasets','problems'].includes(page))result.focus=route.focus;
 if(route.bundleId&&node&&page==='run')result.bundleId=route.bundleId;
 return result;
}

export function nextLocation(current:TaskNavigation,target:TaskDestination){
 const destination=taskDestination(current,target);
 return normalizeLocation({...target,...destination,
  nodeId:target.nodeId===undefined&&destination.projectId!==current.projectId?null:destination.nodeId});
}

export function locationHash(route:TaskNavigation){
 const path=projectPage(route.page)?'/projects/'+encodeURIComponent(route.projectId)+'/'+route.page:
  '/'+route.page+(route.page==='settings'?'/'+route.section:'');
 const query=new URLSearchParams();
 for(const [key,value] of [['node',route.nodeId],['attempt',route.attemptId],['view',route.subpage],
  ['scope',route.runScope==='chain'?'chain':''],['alias',route.alias],['program',route.program],['focus',route.focus],
  ['bundle',route.bundleId],['project',route.page==='datasets'?route.projectId:'']] as const){
  if(value)query.set(key,value);
 }
 return '#'+path+(query.size?'?'+query:'');
}

export function locationFromHash(hash:string):TaskNavigation|null{
 try{
  if(!hash.startsWith('#/'))return null;
  const [path,search]=hash.slice(2).split('?');
  const [kind,identity,page,extra]=path.split('/').map(decodeURIComponent);
  if(extra!==undefined||kind==='projects'&&(!page||!projectPage(page))||
    kind!=='projects'&&!['settings','datasets','problems'].includes(kind))return null;
  const query=new URLSearchParams(search);
  const get=(key:string)=>query.get(key)||undefined;
  return normalizeLocation({page:kind==='projects'?page:kind,projectId:kind==='projects'?identity:get('project'),
   section:kind==='settings'?identity:undefined,nodeId:get('node'),attemptId:get('attempt'),subpage:get('view'),
   alias:get('alias'),program:get('program'),focus:get('focus'),bundleId:get('bundle'),
   runScope:get('scope')==='chain'?'chain':'node'});
 }catch{return null;}
}

export type Visit={fpilot:1;session:string;id:string;position:number;route:TaskNavigation;origin?:string};
export type VisitView={scroll?:number;scrollContainer?:'outer'|'content';viewport?:{x:number;y:number;zoom:number};
 filter?:string;category?:string;details?:Record<string,boolean>;nestedScroll?:Record<string,number>};
export function visitFromState(value:unknown):Visit|null{
 const state=value as Visit|null;
 if(state?.fpilot!==1||typeof state.session!=='string'||typeof state.id!=='string'||
    !Number.isSafeInteger(state.position)||state.position<0||typeof state.route?.page!=='string')return null;
 return {...state,route:normalizeLocation(state.route)};
}

export const maxVisits=100;
export function visitTrail(trail:Visit[],entry:Visit,action:'push'|'replace'|'pop'){
 const known=trail.filter(item=>item.session===entry.session&&item.position!==entry.position&&
  (action!=='push'||item.position<entry.position));
 return [...known,entry].sort((a,b)=>a.position-b.position)
  .filter(item=>Math.abs(item.position-entry.position)<maxVisits).slice(-maxVisits);
}

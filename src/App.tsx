import type {TaskDestination} from './taskNavigation';
import {useWorkbenchNavigation,useVisitScroll} from './useWorkbenchNavigation';
import {projectPages,settingsSections,type Visit} from './navigationHistory';
import NavigationBar from './NavigationBar';
import {submissionDrafts} from './submissionDrafts';
import {calculationKind} from './scanNodes';
import {sourceSummaryGraph,nodeInheritance} from './nodeInheritance';
import type {SourcePlanSummary} from './nodeInheritance';
import {addStructureSnapshot} from './structureSnapshot';
import type {StructureSnapshot} from './structureSnapshot';
import {rememberWorkspace} from './workspaceHistory';
import type {UndoEntry} from './workspaceHistory';
import ProjectProperties from './ProjectProperties';
import './navigation-ui.css';
import {FirstRunNotice} from './LocalSetup';
import './recycle.css';
import './problems.css';
import {UnifiedRecycleBin,openRecycleBin} from './RecordCleanup';
import BranchSettings from './BranchSettings';
import {independentBranch} from './independentBranch';
import NodeTypeSwitch from './NodeTypeSwitch';
import {copyDraftNode} from './nodeTypes';
import {nextStageSequence} from './stagePolicy';
import './submission.css';

import {configurationNodes} from './workflowUi';
import {scanBatchProgress} from './scanCaseView';
import {statusLabel} from './statusLabels';
import {useDiskWorkspace,MAX_BACKUP_BYTES,checkedWorkspace} from './useDiskWorkspace';
import DiskControls,{WorkspaceRecoveryDialog} from './DiskControls';
import ProjectSync from './ProjectSync';
import {verifySource} from './structure';

import RecipeControls from './RecipeControls';



import {lazy,Suspense,useEffect,useRef,useState} from 'react';
import ProjectOverview from './ProjectOverview';
import WorkflowSummary,{WorkflowTaskStrip} from './WorkflowSummary';
import {NodeIcon} from './nodeAppearance';
import './workbench.css';
import LifecycleDialog,{previewCleanup} from './LifecycleDialog';
import type {CleanupPlan} from './LifecycleDialog';
import {withoutProject} from './projectManagement';
import {requestJson,errorMessage} from './apiClient';
import type {SettingsContext} from './submissionDefaults';
import AppearancePanel from './AppearancePanel';
import {taskProject,taskTemplates} from './resultWorkspace';
import type {ResultAttempt} from './resultWorkspace';
import RemoteProgress from './RemoteProgress';
import WorkbenchErrorBoundary from './WorkbenchErrorBoundary';
declare const __FPILOT_CONTROL_URL__:string;
declare const __FPILOT_VERSION__:string;
const CalculationWorkspace=lazy(()=>import('./CalculationWorkspace'));
const SettingsWorkbench=lazy(()=>import('./SettingsWorkbench'));
const StructurePanel=lazy(()=>import('./StructurePanel'));

const ProblemNotebook=lazy(()=>import('./ProblemNotebook'));
const ResultDatasets=lazy(()=>import('./ResultDatasets'));
const NodeResults=lazy(()=>import('./NodeResults'));
const ChainPanel=lazy(()=>import('./ChainPanel'));
const FlowCanvas=lazy(()=>import('./FlowCanvas'));

import type {Connection,Viewport} from '@xyflow/react';
import { Palette, Activity, ArrowDownToLine, ArrowUpFromLine, Atom, Box, Check, CheckCheck, ChevronDown, ChevronRight, CircleHelp, Copy, Database, FileText, FlaskConical, FolderOpen, GitBranch, Layers, Link2, Menu, PanelRightClose, PanelRightOpen, Play, Plus, RotateCcw, Save, Search, Server, Settings2, Trash2, Undo2, X } from 'lucide-react';
import { STORAGE_KEY, catalog, kinds, makeStep, initialWorkspace, newProject, cleanWorkspace, parseWorkspace, inspectFlow, mergeWorkspace, canConnect, slugify, uid } from './model';
import type { Workspace, Step, Kind, Issue, Project } from './model';

function boot(): {workspace:Workspace; error:string; raw:string} {
  try { const raw=localStorage.getItem(STORAGE_KEY); return {workspace:raw?parseWorkspace(raw):initialWorkspace(),error:'',raw:''}; }
  catch(e) { let raw=''; try {raw=localStorage.getItem(STORAGE_KEY)||'';} catch {} return {workspace:initialWorkspace(),error:`本地存档未能读取：${(e as Error).message}。原存档未覆盖。`,raw}; }
}
function download(name:string, text:string) {const a=document.createElement('a');const url=URL.createObjectURL(new Blob([text],{type:'application/json;charset=utf-8'}));a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}

export default function App() {
  const [initial]=useState(boot), [workspace,setWorkspace]=useState(initial.workspace);
  const [storageError,setStorageError]=useState(initial.error);
  const disk=useDiskWorkspace(workspace,setWorkspace,storageError);const saveState=disk.status;
  const [recoveryOpen,setRecoveryOpen]=useState(false);
  const [syncReadOnly,setSyncReadOnly]=useState(false);
  useEffect(()=>{let active=true;const update=()=>{requestJson(`/api/projects/${workspace.activeId}/sync`).then(s=>{if(active)setSyncReadOnly(Boolean(s.draft_read_only??(s.local&&s.local.mode!=='ACTIVE')));}).catch(()=>{});};setSyncReadOnly(false);update();window.addEventListener('fpilot:project-sync-changed',update);return()=>{active=false;window.removeEventListener('fpilot:project-sync-changed',update);};},[workspace.activeId]);
  const nav=useWorkbenchNavigation({projectId:initial.workspace.activeId,page:'flow',nodeId:null,attemptId:'',runScope:'node'});
  const navigation=nav.entry.route;
  const {page:tab,nodeId:selected,attemptId:resultAttemptId,runScope}=navigation;
  const navigate=(target:TaskDestination,replace=false)=>{
    if(target.page!=='flow'&&target.page!=='overview')setFocusNode(undefined);
    nav.navigate({...target,projectId:target.projectId??(nav.current().projectId||stateRef.current.activeId)},replace);
  };
  const updateLocation=(patch:Partial<TaskDestination>)=>nav.navigate({...nav.current(),...patch},true);
  const setSelected=(value:string|null)=>{const route=nav.current();updateLocation({nodeId:value,...(route.nodeId===value?{}:{attemptId:''})});};
  const setTab=(page:string)=>{if(page===nav.current().page)return;navigate({page});};
  useEffect(()=>{if(disk.ready)submissionDrafts.prune(workspace.projects);},[disk.ready,workspace.projects]);
  const [canvasSelectedIds,setCanvasSelectedIds]=useState<string[]>([]);
  useEffect(()=>{
    const dataset=(event:Event)=>navigate({page:'datasets',focus:(event as CustomEvent).detail.id});
    const extensions=()=>navigate({page:'settings',section:'extensions'});
    const settings=(event:Event)=>{const detail=(event as CustomEvent).detail;
      const context:SettingsContext=typeof detail==='string'?{section:detail}:detail||{section:'appearance'};
      if(context.section==='structure'){navigate({page:'structure'});return;}
      if(context.section==='flow'){navigate({page:'properties',nodeId:null});return;}
      navigate({page:'settings',...context});
    };
    const problem=(event:Event)=>navigate({page:'problems',focus:(event as CustomEvent).detail.id});
    const listeners=[['fpilot:dataset',dataset],['fpilot:manage-extensions',extensions],['fpilot:settings',settings],['fpilot:problem-record',problem]] as const;
    for(const [name,listener] of listeners)window.addEventListener(name,listener);
    return()=>{for(const [name,listener] of listeners)window.removeEventListener(name,listener);};
  },[]);
  const [edgeId,setEdgeId]=useState<string|null>(null);
  const canvasViews=useRef(new Map<string,Viewport>());
  const nodeSelections=useRef(new Map<string,string|null>());
  const [focusNode,setFocusNode]=useState<{id:string;sequence:number}>();
  const [panelWidth,setPanelWidth]=useState(384);
  const [snapshotOpen,setSnapshotOpen]=useState(false);
  const [nodeEditOpen,setNodeEditOpen]=useState(false);
  const [nodeAdvancedOpen,setNodeAdvancedOpen]=useState(false);
  const panelDrag=useRef<{x:number;width:number}|null>(null);
  const [sideOpen,setSideOpen]=useState(()=>innerWidth>900), [navOpen,setNavOpen]=useState(false);
  const [addOpen,setAddOpen]=useState(false), [modal,setModal]=useState<'project'|'connect'|'help'|'appearance'|'checks'|null>(null), [newName,setNewName]=useState('');
  const [defaultWorkspaceRoot,setDefaultWorkspaceRoot]=useState('$HOME/fpilot');
  useEffect(()=>{const load=()=>void requestJson('/api/workbench-settings').then(v=>setDefaultWorkspaceRoot(v.workspace_root||'$HOME/fpilot')).catch(()=>{});load();window.addEventListener('fpilot:settings-changed',load);return()=>window.removeEventListener('fpilot:settings-changed',load);},[]);
  const [newRemoteSlug,setNewRemoteSlug]=useState(''),[newRemoteRoot,setNewRemoteRoot]=useState('$HOME/fpilot');
  const newRemoteValid=/^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/.test(newRemoteSlug);
  const newRemoteTaken=workspace.projects.some(p=>p.remoteSlug===newRemoteSlug);
  const newRemoteRootValid=/^(\/|\$HOME(?:\/|$))/.test(newRemoteRoot)&&!/[\x00-\x1f\x7f-\uffff]/.test(newRemoteRoot);
  const [query,setQuery]=useState(''), [message,setMessage]=useState(''), [issues,setIssues]=useState<Issue[]>([]);
  useEffect(()=>{const changed=(event:Event)=>{const {kind}=(event as CustomEvent).detail;if(kind==='attempts')setAttemptRetry(value=>value+1);};window.addEventListener('fpilot:history-changed',changed);return()=>window.removeEventListener('fpilot:history-changed',changed);},[]);
  const [source,setSource]=useState(''), [target,setTarget]=useState('');
  const [history,setHistory]=useState<UndoEntry[]>([]), importRef=useRef<HTMLInputElement>(null);
  const [attemptRows,setAttemptRows]=useState<ResultAttempt[]>([]);
  const [attemptStates,setAttemptStates]=useState<Record<string,string>>({});
  const [chainStates,setChainStates]=useState<Record<string,string>>({});
  const [scanHints,setScanHints]=useState<Record<string,string>>({});
  const [scanDetails,setScanDetails]=useState<Record<string,Array<{index:number;state:string;parameters:Array<{key:string;value:string}>}>>>({});
  const [attemptProjectId,setAttemptProjectId]=useState('');
  const [attemptStatus,setAttemptStatus]=useState<'loading'|'ready'|'error'>('loading');
  const [attemptRetry,setAttemptRetry]=useState(0);
  const [deletingProject,setDeletingProject]=useState<Project|null>(null);
  const [deletingNode,setDeletingNode]=useState<{projectId:string;nodeId:string;label:string;plan:CleanupPlan}|null>(null);
  const deleteProbe=useRef(false);
  const stateRef=useRef(workspace); stateRef.current=workspace;
  const project=workspace.projects.find(p=>p.id===(navigation.projectId||workspace.activeId))||workspace.projects.find(p=>p.id===workspace.activeId)||workspace.projects[0];
  const initialized=useRef(false);
  const pageIsProject=projectPages.some(page=>page===tab);
  const missingProject=Boolean(navigation.projectId&&!workspace.projects.some(p=>p.id===navigation.projectId));
  const missingNode=Boolean(selected&&pageIsProject&&!project.nodes.some(node=>node.id===selected));
  const canvasColumn=useRef<HTMLElement>(null);
  useVisitScroll(canvasColumn,nav.entry.id,nav.view,nav.remember);
  useEffect(()=>{
    if(!disk.ready)return;
    if(!initialized.current){
      initialized.current=true;
      if(!nav.explicit&&nav.entry.position===0&&tab==='flow'&&navigation.projectId!==workspace.activeId){navigate({page:'flow',projectId:workspace.activeId,nodeId:null},true);return;}
    }
    if(missingProject){setMessage('原项目已移入回收站或不存在，已打开当前项目流程。');navigate({page:'flow',projectId:project.id,nodeId:null},true);return;}
    if(missingNode){setMessage('原节点已不存在，已返回该项目的流程页。');navigate({page:'flow',projectId:project.id,nodeId:null},true);return;}
    if(navigation.projectId&&workspace.activeId!==project.id)setWorkspace(w=>({...w,activeId:project.id}));
  },[disk.ready,nav.entry.id,navigation.projectId,missingProject,missingNode,workspace.activeId,project.id]);
  useEffect(()=>{setCanvasSelectedIds(selected&&project.nodes.some(node=>node.id===selected)?[selected]:[]);},[project.id,nav.traversal]);
  useEffect(()=>{setModal(null);setAddOpen(false);setDeletingProject(null);setDeletingNode(null);setRecoveryOpen(false);setEdgeId(null);setFocusNode(undefined);},[nav.traversal]);
  const graph=sourceSummaryGraph(project),graphKey=JSON.stringify(graph);
  const [sourcePlans,setSourcePlans]=useState<{key:string;nodes:Record<string,SourcePlanSummary>}>({key:'',nodes:{}});
  useEffect(()=>{if(tab!=='flow')return;const controller=new AbortController();void requestJson('/api/projects/source-plan-summary',{project:graph},undefined,{signal:controller.signal}).then(value=>{if(!controller.signal.aborted)setSourcePlans({key:graphKey,nodes:value.nodes});}).catch(()=>{if(!controller.signal.aborted)setSourcePlans({key:graphKey,nodes:{}});});return()=>controller.abort();},[tab,graphKey]);
  const projectAttemptStates=attemptProjectId===project.id?attemptStates:{};
  const canvasNodes:Step[]=project.nodes.map(n=>({...n,selected:canvasSelectedIds.includes(n.id),data:{...n.data,
    openStructure:n.data.kind==='structure'?()=>openWorkspace('structure',n.id):undefined,
    inheritance:calculationKind(n,project)==='wannier90'?undefined:nodeInheritance(sourcePlans.key===graphKey?sourcePlans.nodes[n.id]:undefined),
    resultState:projectAttemptStates[n.id],resultHint:attemptProjectId===project.id?scanHints[n.id]:undefined,chainState:chainStates[n.id],
    openChain:chainStates[n.id]?()=>openRun('chain',n.id):undefined,
    attemptStatus:attemptProjectId===project.id?attemptStatus:'loading',
    toggleExpand:n.data.kind==='scan'?()=>{setSelected(n.id);setCanvasSelectedIds([n.id]);setSideOpen(true);}:undefined,
    openResults:projectAttemptStates[n.id]?()=>{setSelected(n.id);if(n.data.kind==='scan')openRun('node',n.id);else openWorkspace('results',n.id,'');}:undefined,
  }}));
  const node=project.nodes.find(n=>n.id===selected);
  useEffect(()=>{nodeSelections.current.set(project.id,project.nodes.some(n=>n.id===selected)?selected:null);setNodeEditOpen(false);setNodeAdvancedOpen(false);setSnapshotOpen(false);},[project.id,selected]);
  const configurable=configurationNodes(project);
  const activeCalculation=configurable.find(item=>item.id===selected)||configurable[0];
  const openConfiguration=()=>navigate({page:'config',nodeId:activeCalculation?.id||null});
  const openNodeConfiguration=()=>openWorkspace('config',activeCalculation?.id);
  const openRun=(scope:'node'|'chain',targetNodeId?:string,bundleId?:string)=>navigate({page:'run',nodeId:targetNodeId||activeCalculation?.id||null,runScope:scope,bundleId});
  const setRunScope=(scope:'node'|'chain')=>navigate({...navigation,runScope:scope});
  const updateAttempt=(nodeId:string,id:string,reason:'user'|'default'|'replace')=>{
    if(!nav.isCurrent(nav.entry.id))return;
    if(reason==='default'&&navigation.attemptId&&navigation.attemptId!==id)return;
    navigate({...navigation,nodeId,attemptId:id},reason!=='user');
  };
  const describeVisit=({route}:Visit)=>{
    const target=workspace.projects.find(item=>item.id===route.projectId);
    const item=target?.nodes.find(node=>node.id===route.nodeId);
    const label:Record<string,string>={flow:'项目与流程',structure:'结构管理',config:'计算配置',run:'运行与监控',results:'结果与后处理',properties:'项目属性',datasets:'数据集',problems:'问题记录',settings:'全局设置'};
    const detail:Record<string,string>={physics:'物理参数',source:'计算来源',files:route.page==='config'?'输入审查':'文件与日志',post:'绘图与分析',evidence:'执行证据',summary:'结果摘要',compare:'结果比较',handoff:'历史交接'};
    return [target?.name||(route.projectId?'原项目':undefined),item?.data.label,label[route.page],
      route.page==='settings'?settingsSections.find(([id])=>id===route.section)?.[1]:detail[route.subpage||''],
      route.alias,route.program,route.attemptId&&['run','results'].includes(route.page)?'记录 '+route.attemptId.slice(0,8):''].filter(Boolean).join(' / ');
  };
  const snapshot=()=>setHistory(h=>rememberWorkspace(h,stateRef.current));
  const changeProject=(fn:(p:Project)=>Project, remember=true)=>{if(syncReadOnly){setMessage('项目已交接或待核验，请先下载并接管后编辑');return;}if(remember)snapshot();setWorkspace(w=>({...w,projects:w.projects.map(p=>p.id===project.id?{...fn(p),updatedAt:new Date().toISOString()}:p)}));setIssues([]);};
  const toast=(m:string)=>setMessage(m);
  const undo=()=>{if(!history.length)return;const previous=history[history.length-1].workspace;setWorkspace(previous);setHistory(h=>h.slice(0,-1));navigate({projectId:previous.activeId,page:'flow',nodeId:null},true);setCanvasSelectedIds([]);setEdgeId(null);setIssues([]);toast('已撤销上一步');};
  useEffect(()=>{if(!message)return;const t=setTimeout(()=>setMessage(''),4200);return()=>clearTimeout(t);},[message]);
  useEffect(()=>{const controller=new AbortController();setAttemptStatus('loading');
    void requestJson(`/api/projects/${encodeURIComponent(project.id)}/task-summary`,undefined,undefined,{signal:controller.signal})
      .then(({attempts:rows,scans:scanData,chains:chainData})=>{if(controller.signal.aborted)return;if(!Array.isArray(rows)||!Array.isArray(scanData.batches))throw new Error('结果状态格式无效');rows.sort((a:ResultAttempt,b:ResultAttempt)=>String(b.created_at||'').localeCompare(String(a.created_at||'')));const map:Record<string,string>={};const details:typeof scanDetails={},hints:Record<string,string>={};for(const row of rows as Array<{node_id?:string;execution_scope?:string;scan_batch_id?:string;state?:string}>)if(!row.scan_batch_id&&row.node_id&&(!row.execution_scope||row.execution_scope===row.node_id)&&!map[row.node_id])map[row.node_id]=row.state||'已记录';for(const batch of scanData.batches as Array<{scan_node_id:string;state:string;waiting_reason?:string;last_worker_error?:string;cases:Array<{index:number;state:string;parameters:Array<{key:string;value:string}>}>}>)if(!map[batch.scan_node_id]){const progress=scanBatchProgress(batch);map[batch.scan_node_id]=progress.label;hints[batch.scan_node_id]=progress.hint;details[batch.scan_node_id]=batch.cases;}const chainMap:Record<string,string>={};for(const run of (chainData.runs||[]) as Array<{state:string;steps:Array<{node_id:string;state:string}>}>)for(const step of run.steps)if(!chainMap[step.node_id])chainMap[step.node_id]=`${run.state} · ${step.state}`;setChainStates(chainMap);setScanDetails(details);setScanHints(hints);setAttemptRows(rows);setAttemptStates(map);setAttemptProjectId(project.id);setAttemptStatus('ready');})
      .catch(()=>{if(!controller.signal.aborted)setAttemptStatus('error');});
    return()=>controller.abort();},[tab,project.id,attemptRetry]);
  useEffect(()=>{if(!modal)return;const previous=document.activeElement as HTMLElement;const dialog=document.querySelector<HTMLElement>('.modal');if(!dialog)return;const focusable=()=>Array.from(dialog?.querySelectorAll<HTMLElement>('button,input,select,textarea,summary,a[href],[tabindex="0"]')||[]).filter(element=>element.getClientRects().length>0&&!element.matches(':disabled'));if(!dialog?.contains(document.activeElement))focusable()[0]?.focus();const trap=(event:KeyboardEvent)=>{if(event.key!=='Tab')return;const items=focusable(), first=items[0], last=items[items.length-1];if(!dialog.contains(document.activeElement)){event.preventDefault();(event.shiftKey?last:first)?.focus();}else if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}};document.addEventListener('keydown',trap);return()=>{document.removeEventListener('keydown',trap);previous?.focus();};},[modal]);
  useEffect(()=>{const key=(e:KeyboardEvent)=>{if(e.key==='Escape'){setModal(null);setAddOpen(false);}if((e.metaKey||e.ctrlKey)&&e.key==='z' && !(e.target as HTMLElement)?.closest('input,textarea,select,[contenteditable]')){e.preventDefault();undo();}};window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key);});
  // Read-only WebMCP tool shares the visible document. No execution capabilities in phase 1.
  useEffect(()=>{const context=(document as Document & {modelContext?:{registerTool:(tool:unknown,options:unknown)=>unknown}}).modelContext;if(!context?.registerTool)return;const life=new AbortController();try{Promise.resolve(context.registerTool({name:'read_workflow_draft',description:'读取当前本地流程草稿。不会提交任务或修改数据。',inputSchema:{type:'object',properties:{},additionalProperties:false},annotations:{readOnlyHint:true},execute:()=>{const w=stateRef.current;return cleanWorkspace(w).projects.find(p=>p.id===w.activeId);}}, {signal:life.signal})).catch(()=>{});}catch{}return()=>life.abort();},[]);
  const add=(kind:Kind)=>{if(project.nodes.length>=500){toast('节点数量已达 500 上限');return;}const sequence=nextStageSequence(project,kind);const n=makeStep(kind,{x:100+project.nodes.length%4*270,y:120+Math.floor(project.nodes.length/4)*220},sequence);for(const key of ['wannierDrafts','activeWannierDraft','windowAdoption'])delete (n.data as Record<string,unknown>)[key];changeProject(p=>({...p,nodes:[...p.nodes,n]}));setSelected(n.id);setCanvasSelectedIds([n.id]);setEdgeId(null);setAddOpen(false);setSideOpen(true);setTab('flow');toast(`已添加${catalog[kind].label}，请连接上游节点`);};
  const connect=(c:Connection|{source:string;target:string})=>{if(project.edges.length>=2000){toast('连线数量已达 2000 上限');return;}if(!c.source||!c.target||!canConnect(project.nodes,project.edges,c.source,c.target)){toast('无法连接：请核对迭代内的计算类型、实际来源类型、重复连线或循环依赖；迭代配置页也可选择上游');return;}changeProject(p=>({...p,edges:[...p.edges,{id:uid(),source:c.source!,target:c.target!}]}));setModal(null);toast('已连接节点');};
  const copyNode=()=>{if(!node||project.nodes.length>=500)return;const n=copyDraftNode(project,node);changeProject(p=>({...p,nodes:[...p.nodes,n]}));setSelected(n.id);setCanvasSelectedIds([n.id]);toast('已复制节点；来源与连接关系需要重新设置');};
  const removeNodeDraft=(projectId:string,nodeId:string,recycled:boolean)=>{
    if(!recycled)snapshot();
    else setHistory([]);
    setWorkspace(w=>({...w,projects:w.projects.map(p=>p.id!==projectId?p:{...p,
      nodes:p.nodes.filter(n=>n.id!==nodeId),edges:p.edges.filter(e=>e.source!==nodeId&&e.target!==nodeId),updatedAt:new Date().toISOString()})}));
    if(nav.current().projectId===projectId&&nav.current().nodeId===nodeId){setSelected(null);setCanvasSelectedIds([]);setEdgeId(null);}
    if(recycled)setAttemptRetry(n=>n+1);
    toast(recycled?'节点和关联记录已移入回收站':'已删除，可点击撤销恢复');
  };
  const remove=async()=>{
    if(!disk.ready||disk.error||syncReadOnly||deleteProbe.current)return;
    if(edgeId){changeProject(p=>({...p,edges:p.edges.filter(e=>e.id!==edgeId)}));setEdgeId(null);toast('已删除连线，可点击撤销恢复');return;}
    if(!node)return;
    const projectId=project.id,nodeId=node.id;
    deleteProbe.current=true;
    try{
      const plan=await previewCleanup({kind:'nodes',key:nodeId,project_id:projectId});
      if(nav.current().projectId!==projectId||nav.current().nodeId!==nodeId)return;
      if(plan.records.length)setDeletingNode({projectId,nodeId,label:node.data.label,plan});
      else removeNodeDraft(projectId,nodeId,false);
    }catch(e){toast(errorMessage(e));}finally{deleteProbe.current=false;}
  };
  const updateData=(key:string,value:unknown)=>changeProject(p=>({...p,nodes:p.nodes.map(n=>n.id===selected?{...n,data:{...n.data,[key]:value}}:n)}),false);
  const projectRecycled=(target:Project)=>{
    setHistory([]);setWorkspace(w=>withoutProject(w,target.id));setIssues([]);
    window.dispatchEvent(new CustomEvent('fpilot:history-changed',{detail:{kind:'projects',id:target.id,action:'recycle'}}));
    toast('项目与相关记录已移入回收站，可恢复');
  };
  const restoreNode=async(row:{key:string;project_id?:string;node?:Step;edges?:Project['edges']})=>{
    const target=stateRef.current.projects.find(p=>p.id===row.project_id);
    if(!target||!row.node)throw new Error('请先恢复所属项目');
    if(target.nodes.some(n=>n.id===row.node?.id))throw new Error('项目中已有同一节点，请先核对当前草稿');
    const {node:restored,edges}=await requestJson('/api/recycle/nodes/'+encodeURIComponent(row.key),undefined,'DELETE');
    setWorkspace(w=>({...w,projects:w.projects.map(p=>{
      if(p.id!==target.id)return p;
      const nodes=[...p.nodes,restored as Step],ids=new Set(nodes.map(n=>n.id));
      return {...p,nodes,edges:[...p.edges,...(edges as Project['edges']).filter(e=>ids.has(e.source)&&ids.has(e.target)&&!p.edges.some(old=>old.id===e.id))]};
    })}));
    setAttemptRetry(n=>n+1);toast('节点已恢复，可在项目流程中查看');
  };
  const restoreProject=async(id:string)=>{if(stateRef.current.projects.some(p=>p.id===id))throw new Error('同 ID 项目已存在，请先处理重复项目');const data=await requestJson(`/api/recycle/projects/${id}`,undefined,'DELETE');snapshot();setWorkspace(w=>({...w,activeId:id,projects:[...w.projects,data.project]}));navigate({page:'flow',projectId:id,nodeId:null});setAttemptRetry(n=>n+1);toast('项目已恢复，自动计划保持暂停');};
  const selectProject=(id:string)=>{setFocusNode(undefined);navigate({page:'flow',projectId:id,nodeId:nodeSelections.current.get(id)||null,attemptId:''});setEdgeId(null);setIssues([]);setNavOpen(false);};
  const openWorkspace=(view:string,nodeId?:string,attemptId?:string,projectId=project.id)=>{if(nodeId)setCanvasSelectedIds([nodeId]);navigate({projectId,page:view,nodeId:nodeId||(projectId===project.id?selected:null),attemptId,runScope:['config','run'].includes(view)?'node':runScope});};
  const addTaskStructure=(value:StructureSnapshot)=>{const current=stateRef.current.projects.find(p=>p.id===stateRef.current.activeId);if(!current||current.id!==value.origin.project_id)throw new Error('项目已切换，请在原项目重新添加结构');if(syncReadOnly||!disk.ready)throw new Error('当前项目不可编辑');const result=addStructureSnapshot(current,value);if(!result.reused)changeProject(()=>result.project);setSelected(result.nodeId);setCanvasSelectedIds([result.nodeId]);setFocusNode({id:result.nodeId,sequence:Date.now()});setSideOpen(true);setTab('flow');toast(result.reused?'已定位此来源的结构节点':'已添加独立结构副本，来源任务与文件保持不变');};
  const createTask=(id:string)=>{if(workspace.projects.length>=100){toast('项目数量已达上限');return;}snapshot();const p=taskProject(id);p.remoteWorkspaceRoot=defaultWorkspaceRoot;setWorkspace(w=>({...w,activeId:p.id,projects:[...w.projects,p]}));navigate({projectId:p.id,page:'structure',nodeId:p.nodes[0].id});toast('已建立独立流程草稿；请导入结构并填写参数，尚未冻结或提交');};
  const check=()=>{setIssues(inspectFlow(project));setModal('checks');};
  const importFile=async(file?:File)=>{if(!file)return;try{if(file.size>MAX_BACKUP_BYTES)throw new Error('备份文件不能超过 32 MiB');const imported=parseWorkspace(await file.text());await Promise.all(imported.projects.flatMap(p=>p.nodes.flatMap(n=>n.data.structure?[verifySource(n.data.structure)]:[])));const merged=mergeWorkspace(stateRef.current,imported,defaultWorkspaceRoot);snapshot();setWorkspace(merged);navigate({page:'flow',projectId:merged.activeId,nodeId:null});setEdgeId(null);setIssues([]);toast('已导入为新项目，原项目完整保留');}catch(e){toast(`导入失败：${(e as Error).message}`);}finally{if(importRef.current)importRef.current.value='';}};
  const importRecipe=async(recipeProject:Project)=>{const imported=await checkedWorkspace({activeId:recipeProject.id,projects:[recipeProject]});const merged=mergeWorkspace(stateRef.current,imported,defaultWorkspaceRoot);snapshot();setWorkspace(merged);navigate({page:'flow',projectId:merged.activeId,nodeId:null});setEdgeId(null);setIssues([]);toast('配方已物化为新草稿；未提交任务');};
  return <div className="app-shell green-workbench" style={{'--workflow-panel-width':`${panelWidth}px`} as React.CSSProperties}>
    <aside inert={!disk.ready} className={`sidebar ${navOpen?'mobile-open':''}`}>
      <div className="brand"><div className="brand-mark">f<span>p</span></div><div><strong>First Principles</strong><small>个人计算工作台</small></div></div>
      <div className="workspace-label">WORKSPACE <span>本地</span></div>
      <button className="nav-current" onClick={()=>{setTab('overview');setNavOpen(false);}}><FlaskConical size={19}/>计算项目 <span>{workspace.projects.length}</span></button>
      <div className="section-heading">我的项目<button className="icon-btn" aria-label="新建项目" onClick={()=>{setNewName('');setNewRemoteSlug('');setNewRemoteRoot(defaultWorkspaceRoot);setModal('project');}}><Plus size={17}/></button></div>
      <label className="search"><Search size={15}/><input aria-label="搜索项目" placeholder="搜索项目" value={query} onChange={e=>setQuery(e.target.value)}/></label>
      <div className="project-list">{workspace.projects.filter(p=>p.name.toLowerCase().includes(query.toLowerCase())).map(p=><div key={p.id} className="project-list-entry"><div className="project-list-row"><button className={`project-item ${p.id===project.id?'active':''}`} onClick={()=>selectProject(p.id)}><ChevronDown size={14}/><FolderOpen size={17}/><span>{p.name}</span></button><details className="project-menu"><summary className="icon-btn" aria-label={'项目操作 · '+p.name}>⋯</summary><div>
            <button className="text-button" onClick={e=>{e.currentTarget.closest('details')?.removeAttribute('open');navigate({page:'properties',projectId:p.id,nodeId:null});setNavOpen(false);}}>项目属性</button>
            <button className="text-button" onClick={()=>download(p.remoteSlug+'-project.json',JSON.stringify(cleanWorkspace({activeId:p.id,projects:[p]})))}>导出当前项目</button>
            <RecipeControls project={p} disabled={!disk.ready} onImport={importRecipe} mode="export"/>
            <button className="text-button" onClick={()=>setDeletingProject(p)}>删除项目</button>
          </div></details></div></div>)}{!workspace.projects.some(p=>p.name.toLowerCase().includes(query.toLowerCase()))&&<p className="empty-search">没有匹配的项目</p>}</div>
      <button className="help-button" aria-current={tab==='datasets'?'page':undefined} onClick={()=>{navigate({page:'datasets',focus:''});setNavOpen(false);}}><Database size={17}/>数据集</button>
      <button className="help-button" aria-current={tab==='problems'?'page':undefined} onClick={()=>{navigate({page:'problems',focus:''});setNavOpen(false);}}><FileText size={17}/>问题记录</button>
      <button className="help-button" onClick={openRecycleBin}><Trash2 size={17}/>回收站</button><div className="sidebar-bottom"><div className="local-card"><Server size={18}/><div><strong>本机工作区</strong></div></div><button className="help-button" onClick={()=>{navigate({page:'settings',section:'extensions'});}}><Settings2 size={17}/>扩展管理</button><button className="help-button" onClick={()=>{navigate({page:'settings',section:'appearance'});}}><Palette size={17}/>外观设置</button><button className="help-button" onClick={()=>setModal('help')}><CircleHelp size={17}/>使用说明</button></div>
    </aside>
    {navOpen&&<button className="nav-backdrop" aria-label="关闭项目导航" onClick={()=>setNavOpen(false)}/>}
    <main className={`main ${tab==='results'?'results-active':''}`}>
      <header className="topbar"><div className="breadcrumbs"><button className="icon-btn mobile-menu" aria-label="打开项目导航" onClick={()=>setNavOpen(true)}><Menu size={20}/></button><NavigationBar nav={{...nav,recent:nav.recent.filter(({route})=>!route.projectId||workspace.projects.some(item=>item.id===route.projectId&&(!route.nodeId||item.nodes.some(node=>node.id===route.nodeId))))}} describe={describeVisit} onOpen={route=>navigate(route)}/></div><div className="top-right">{pageIsProject&&<button className="project-structure-shortcut" aria-pressed={tab==='structure'} onClick={()=>setTab('structure')}><Box size={16}/>结构管理</button>}{__FPILOT_CONTROL_URL__&&<a className="appearance-shortcut" href={__FPILOT_CONTROL_URL__} target="_blank" rel="noopener">工作台控制</a>}<button className="appearance-shortcut" onClick={()=>navigate({page:'settings',section:'appearance'})}><Settings2 size={17}/>全局设置</button></div></header>
      <FirstRunNotice/>
      {!disk.ready&&(disk.error?<div className="storage-error" role="alert">工作区尚未连接：{disk.error} 已保存的项目保留，当前显示不可编辑。<button className="button" onClick={disk.reload}>重新连接工作区</button></div>:<RemoteProgress label="正在连接本机工作区"/>)}
      {storageError&&<div className="storage-error" role="alert">{storageError}<button onClick={()=>download('fpilot-unreadable-backup.json',initial.raw)}>导出原存档</button><button onClick={()=>{try{localStorage.setItem(`${STORAGE_KEY}.recovery.${Date.now()}`,initial.raw);setStorageError('');}catch{toast('备份失败，请先导出原存档');}}}>备份并启用新存档</button></div>}
{pageIsProject&&<>
      <div className="workspace-tabs"><div role="tablist" aria-label="项目工作区">{[{id:'flow',label:'项目与流程',Icon:GitBranch},{id:'config',label:'计算配置',Icon:Settings2},{id:'run',label:'运行与监控',Icon:Play},{id:'results',label:'结果与后处理',Icon:Layers}].map(({id,label,Icon})=><button key={id} role="tab" aria-selected={tab===id} className={tab===id?'tab active':'tab'} onClick={()=>{if(id==='config')openConfiguration();else if(id==='run')openRun(runScope);else setTab(id);}}><Icon size={17}/>{label}{id==='flow'&&<span>{project.nodes.length}</span>}</button>)}</div><span className="local-indicator"><Save size={14}/>本机文件工作区</span></div>
      </>}
      <div inert={!disk.ready} className={`editor-layout ${tab==='flow'?'workflow-layout':''}`}>
        <section ref={canvasColumn} className={`canvas-column ${tab==='settings'?'settings-column':''}`}>
          {tab==='flow'&&<><div className="canvas-toolbar"><div className="toolbar-left"><div className="add-wrap"><button className="button primary" onClick={()=>setAddOpen(!addOpen)} aria-expanded={addOpen}><Plus size={17}/>添加节点<ChevronDown size={14}/></button>{addOpen&&<div className="node-menu">{kinds.map(k=>{return <button key={k} onClick={()=>add(k)}><span style={{color:catalog[k].color}}><NodeIcon kind={k} size={20}/></span><span><strong>{catalog[k].label}</strong><small>{catalog[k].description}</small></span></button>;})}</div>}</div><button className="icon-btn" aria-label="连接节点" title="通过列表连接节点" onClick={()=>{setSource(project.nodes[0]?.id||'');setTarget(project.nodes.find(n=>n.data.kind!=='structure')?.id||'');setModal('connect');}}><Link2 size={18}/></button><span className="separator"/><button className="icon-btn" aria-label="复制选中节点" title="复制选中节点" disabled={!node||project.nodes.length>=500} onClick={copyNode}><Copy size={17}/></button><button className="icon-btn" aria-label="删除选中内容" title="删除选中节点或连线" disabled={!node&&!edgeId} onClick={remove}><Trash2 size={17}/></button><button className="icon-btn" aria-label="撤销" title="撤销（Ctrl / ⌘ Z）" disabled={!history.length} onClick={undo}><Undo2 size={18}/></button></div><div className="toolbar-right"><details className="workflow-templates"><summary className="button">应用流程模板</summary><div>{taskTemplates.map(template=><button className="text-button" key={template.id} title={template.description} onClick={e=>{e.currentTarget.closest('details')?.removeAttribute('open');createTask(template.id);}}>{template.name}</button>)}<RecipeControls project={project} disabled={!disk.ready} onImport={importRecipe} mode="import"/></div></details><select aria-label="定位流程节点"  value={selected||''} onChange={e=>{setSelected(e.target.value||null);setCanvasSelectedIds(e.target.value?[e.target.value]:[]);setSideOpen(true);if(e.target.value)setFocusNode({id:e.target.value,sequence:Date.now()});}}><option value="">查找节点</option>{project.nodes.map(n=><option key={n.id} value={n.id}>{n.data.label}</option>)}</select><button className="button" onClick={check}><CheckCheck size={16}/>检查流程</button><button className="icon-btn" aria-label={sideOpen?'收起摘要面板':'展开摘要面板'} onClick={()=>setSideOpen(!sideOpen)}>{sideOpen?<PanelRightClose size={19}/>:<PanelRightOpen size={19}/>}</button></div></div><WorkflowTaskStrip rows={attemptProjectId===project.id?attemptRows:[]} loading={attemptStatus==='loading'}/></>}

          <WorkbenchErrorBoundary key={`${project.id}:${tab}:${nav.traversal}`} onReturn={()=>setTab('overview')}>
          <Suspense fallback={<RemoteProgress label="正在载入工作区"/>}>{(!disk.ready||missingProject||missingNode)?<RemoteProgress label="正在定位页面"/>:tab==='flow'?<div className="flow-surface"><div className="canvas-caption"><span className="canvas-pill">流程草稿</span><span>Ctrl/⌘ 点选 · Shift 框选 · 拖动选中组</span></div><FlowCanvas initialViewport={nav.view?.viewport||canvasViews.current.get(project.id)} onViewportChange={viewport=>{if(nav.isCurrent(nav.entry.id)){canvasViews.current.set(project.id,viewport);nav.remember({viewport});}}} focusNode={focusNode} project={project} nodes={canvasNodes} edgeId={edgeId} onSelectionChange={setCanvasSelectedIds} onChange={nodes=>setWorkspace(w=>({...w,projects:w.projects.map(p=>p.id===w.activeId?{...p,nodes}:p)}))} onDragStart={snapshot} onNodeOpen={id=>{const target=project.nodes.find(n=>n.id===id);if(target)openWorkspace(target.data.kind==='structure'?'structure':'config',id);}} onNodeClick={id=>{setSelected(id);setEdgeId(null);setSideOpen(true);}} onEdgeClick={id=>{setEdgeId(id);setSelected(null);setCanvasSelectedIds([]);}} onPaneClick={()=>{setSelected(null);setCanvasSelectedIds([]);setEdgeId(null);setAddOpen(false);}} onConnect={connect}/>{attemptStatus==='error'&&<div className="canvas-note" role="alert">执行记录暂不可读；<button className="text-button" onClick={()=>setAttemptRetry(n=>n+1)}>重试读取状态</button></div>}</div>
          :(tab==='config'||tab==='run')?<div className="workflow-page">
            <div className="workflow-context">
              <label>{tab==="run"&&runScope==="chain"?"画布定位节点（不改变执行范围）":"当前计算节点"}<select aria-label="当前计算节点" value={activeCalculation?.id||''} onChange={e=>navigate({...navigation,nodeId:e.target.value,attemptId:"",subpage:undefined,bundleId:undefined})}>{configurable.map(item=><option key={item.id} value={item.id}>{item.data.label}{item.data.label!==catalog[item.data.kind].label?" · "+catalog[item.data.kind].label:""}</option>)}</select></label>
              <div className="workflow-source"><span>{tab==="run"&&runScope==="chain"?"执行范围":"直接上游"}</span><strong>{tab==="run"&&runScope==="chain"?"以下方冻结计算链与复用来源为准":project.edges.filter(edge=>edge.target===activeCalculation?.id).map(edge=>project.nodes.find(item=>item.id===edge.source)?.data.label).filter(Boolean).join('、')||'尚未连接'}</strong></div>
              {(tab==='run'||tab==='config')&&<label>计算范围<select aria-label="运行范围" value={runScope} onChange={e=>setRunScope(e.target.value as typeof runScope)}><option value="node">当前节点{activeCalculation?.data.kind==='scan'?'的参数迭代批次':''}</option><option value="chain">计算链：选择多个 VASP 节点</option></select></label>}
              {tab==='config'&&<button className="button" onClick={()=>openRun('node')}>运行审阅与历史</button>}
            </div>
            {runScope==='chain'?<ChainPanel key={project.id} project={project} initialNodeId={selected} view={tab==='config'?'config':'run'} onReview={()=>openRun('chain')} onConfigure={openConfiguration}/>:activeCalculation?<CalculationWorkspace onProject={next=>changeProject(()=>next)} key={project.id+activeCalculation.id} project={project} nodeId={activeCalculation.id} view={tab==='config'?'config':'run'}
              bundleId={navigation.bundleId} configPart={navigation.subpage as 'physics'|'source'|'files'|undefined}
              onConfigPart={subpage=>navigate({...navigation,subpage})}
              onReview={bundleId=>openRun('node',activeCalculation.id,bundleId)}
              onConfigure={openConfiguration} initialAttemptId={resultAttemptId} onAttemptChange={(id,reason)=>updateAttempt(activeCalculation.id,id,reason)} onResults={(nodeId,attemptId)=>openWorkspace('results',nodeId,attemptId)}
              onSource={(nodeId,value)=>changeProject(p=>({...p,nodes:p.nodes.map(n=>n.id===nodeId?{...n,data:{...n.data,sourceDraft:value}}:n)}),false)}
              onDraft={(nodeId,update)=>changeProject(p=>({...p,nodes:p.nodes.map(n=>n.id===nodeId?{...n,data:{...n.data,inputDraft:update((n.data.inputDraft||{}) as Record<string,string>)}}:n)}),false)}
              onSharedPotcar={(id,provenance_id)=>changeProject(p=>({...p,sharedPotcar:id?{id,provenance_id}:undefined}),false)}
              onLayer={(nodeId,layer)=>changeProject(p=>nodeId?{...p,nodes:p.nodes.map(n=>n.id===nodeId?{...n,data:{...n.data,parameters:layer}}:n)}:{...p,parameters:{...p.parameters,version:1,shared:layer}},false)}
              onTemplates={templates=>changeProject(p=>({...p,parameters:{version:1,shared:p.parameters?.shared||{values:{}},templates}}))}
              onNumWann={(nodeId,value)=>changeProject(p=>({...p,nodes:p.nodes.map(n=>n.id===nodeId?{...n,data:{...n.data,inputDraft:{...(n.data.inputDraft as object||{}),num_wann:value}}}:n)}),false)}
              onProjections={(nodeId,value)=>changeProject(p=>({...p,nodes:p.nodes.map(n=>n.id===nodeId?{...n,data:{...n.data,inputDraft:{...(n.data.inputDraft as object||{}),projections:value}}}:n)}),false)}
              onWannierDraft={(nodeId,key,value)=>changeProject(p=>({...p,nodes:p.nodes.map(n=>n.id===nodeId?{...n,data:{...n.data,activeWannierDraft:key,wannierDrafts:{...(n.data.wannierDrafts as Record<string,unknown>||{}),[key]:value}}}:n)}),false)}
              onWindowChange={value=>changeProject(p=>({...p,nodes:p.nodes.map(n=>n.id===activeCalculation.id?{...n,data:{...n.data,windowScanDraft:value}}:n)}),false)}
              onWindowAdopt={value=>{changeProject(p=>({...p,nodes:p.nodes.map(n=>n.id===value.node_id?{...n,data:{...n.data,windowAdoption:value}}:n)}),false);toast('窗口已采用到对应计算节点草稿，请载入后重新审查');}}
              onScanDraft={(dimensions,reason)=>changeProject(p=>({...p,nodes:p.nodes.map(n=>n.id===activeCalculation.id?{...n,data:{...n.data,scanDraft:{dimensions,reason}}}:n)}),false)}/>:<div className="structure-empty"><h2>先建立计算节点</h2><p>在计算流程中添加节点并连接结构，然后在这里完成配置与运行审阅。</p><button className="button primary" onClick={()=>setTab('flow')}>打开计算流程</button></div>}
          </div>:tab==='datasets'?<section className="node-results" data-navigation-scroll><ResultDatasets projectId={project.id} focusId={navigation.focus} onFocus={focus=>navigate({...navigation,focus})}/></section>:tab==='problems'?<ProblemNotebook focusId={navigation.focus} onFocus={focus=>navigate({...navigation,focus})} onOpenSource={record=>{const target=workspace.projects.find(p=>p.id===record.project_id);if(!target||!target.nodes.some(n=>n.id===record.node_id)){toast('原项目或节点已删除，问题记录继续保留');return;}openWorkspace('results',record.node_id||undefined,record.source_id,target.id);}}/>:tab==='properties'?<ProjectProperties key={project.id} project={project} defaultRoot={defaultWorkspaceRoot} canEdit={!syncReadOnly&&disk.ready&&!disk.error}
            slugLocked={attemptStatus!=='ready'||attemptProjectId!==project.id||attemptRows.length>0}
            isSlugTaken={slug=>stateRef.current.projects.some(item=>item.id!==project.id&&item.remoteSlug===slug)}
            onSave={patch=>changeProject(p=>({...p,...patch}))}/>:tab==='settings'?<SettingsWorkbench
            section={navigation.section||'appearance'} context={navigation.alias?{section:navigation.section||'appearance',alias:navigation.alias,program:navigation.program}:undefined}
            focus={navigation.focus} onBack={()=>nav.returnToOrigin({page:'flow',projectId:project.id,nodeId:null})}
            onSection={(section,focus)=>navigate({page:'settings',section,focus,alias:navigation.alias,program:navigation.program})}
            onContext={(alias,program,replace)=>{if(nav.isCurrent(nav.entry.id))navigate({page:'settings',section:'server',alias,program,focus:replace?navigation.focus:undefined},replace);}}
            backup={<><DiskControls disk={disk} workspace={workspace} legacyRaw={initial.raw} onAppend={w=>{
              const merged=mergeWorkspace(stateRef.current,w,defaultWorkspaceRoot);snapshot();setWorkspace(merged);
              navigate({page:'flow',projectId:merged.activeId,nodeId:null});setIssues([]);toast('已追加恢复，原项目保留');
            }}/><div className="action-row"><button className="button" onClick={()=>{download(`fpilot-workspace-${new Date().toISOString().slice(0,10)}.json`,JSON.stringify(cleanWorkspace(workspace)));toast('已导出全部项目备份');}}><ArrowDownToLine size={16}/>导出全部项目</button>
             <button className="button" onClick={()=>importRef.current?.click()}><ArrowUpFromLine size={16}/>导入工作区备份</button><input ref={importRef} hidden type="file" accept=".json,application/json" onChange={e=>void importFile(e.target.files?.[0])}/>
            </div></>}/>
:tab==='structure'?<StructurePanel key={project.id} targetNodeId={node?.data.kind==='structure'?node.id:undefined} onTargetChange={id=>{setSelected(id);setCanvasSelectedIds([id]);setFocusNode({id,sequence:Date.now()});}} projectId={project.id} nodes={project.nodes} onImport={(id,structure)=>changeProject(p=>({...p,nodes:p.nodes.map(n=>n.id===id?{...n,data:{...n.data,structure,...(n.data.structureOrigin&&(n.data.structureOrigin as {sha256:string}).sha256!==structure.source.sha256?{structureOrigin:undefined}:{}),params:{...n.data.params,material:structure.formula.slice(0,200)}}}:n)}))}/>:<NodeResults key={project.id+String(selected)} project={project} initialNodeId={selected} initialAttemptId={resultAttemptId} subpage={navigation.subpage} onSubpage={subpage=>navigate({...navigation,subpage})} viewState={nav.view} viewKey={nav.entry.id} onViewState={view=>nav.remember(view)} onAttemptChange={updateAttempt} onAddStructure={!syncReadOnly&&disk.ready?addTaskStructure:undefined} onConfigure={id=>openWorkspace('config',id)} onRun={(id,attemptId)=>openWorkspace('run',id,attemptId)}/>}
          </Suspense></WorkbenchErrorBoundary>
        </section>
        {sideOpen&&tab==='flow'&&<><div className="workflow-panel-resizer" role="separator" aria-label="调整摘要面板宽度" aria-orientation="vertical" aria-valuemin={256} aria-valuemax={512} aria-valuenow={panelWidth} tabIndex={0} onPointerDown={e=>{if(e.button!==0)return;e.preventDefault();panelDrag.current={x:e.clientX,width:panelWidth};e.currentTarget.setPointerCapture(e.pointerId);}} onPointerMove={e=>{if(panelDrag.current)setPanelWidth(Math.max(256,Math.min(512,panelDrag.current.width+panelDrag.current.x-e.clientX)));}} onPointerUp={e=>{panelDrag.current=null;e.currentTarget.releasePointerCapture(e.pointerId);}} onPointerCancel={()=>{panelDrag.current=null;}} onKeyDown={e=>{if(['ArrowLeft','ArrowRight','Home'].includes(e.key)){e.preventDefault();setPanelWidth(w=>e.key==='Home'?384:Math.max(256,Math.min(512,w+(e.key==='ArrowLeft'?16:-16))));}}}><span/></div><aside className="inspector workflow-inspector"><div className="inspector-heading"><strong>{node?'节点摘要':'项目概况'}</strong><button className="icon-btn" aria-label="关闭摘要面板" onClick={()=>setSideOpen(false)}><X size={16}/></button></div><div className="workflow-inspector-scroll">{node?<><WorkflowSummary key={project.id+node.id} project={project} node={node} rows={attemptProjectId===project.id?attemptRows:[]} scan={scanDetails[node.id]} onOpen={openWorkspace} onRefresh={()=>setAttemptRetry(n=>n+1)} loading={attemptStatus==='loading'} failed={attemptStatus==='error'}/>{node.data.kind!=='structure'&&<details className="workflow-node-advanced" key={project.id+node.id} onToggle={e=>setNodeAdvancedOpen(e.currentTarget.open)}>
          <summary>节点高级属性</summary>{nodeAdvancedOpen&&<div className="inspector-body">
           {['vasp','wannier90'].includes(node.data.executionMode)&&<label>阶段目录名<input key={node.data.stageSlug} defaultValue={node.data.stageSlug} maxLength={48}
             disabled={!disk.ready||syncReadOnly||attemptStatus!=='ready'||attemptProjectId!==project.id||Boolean(projectAttemptStates[node.id])}
             onBlur={event=>{const value=slugify(event.target.value||node.data.kind).slice(0,48);event.target.value=value;if(value!==node.data.stageSlug)changeProject(p=>({...p,nodes:p.nodes.map(item=>item.id===node.id?{...item,data:{...item.data,stageSlug:value}}:item)}));}}/>
             <small>阶段编号 {String(node.data.stageSequence).padStart(2,'0')} 自动添加。有运行记录的节点保留原目录标识。</small></label>}
           <BranchSettings project={project} nodeId={node.id}/>
           <button className="button" disabled={!disk.ready||syncReadOnly} onClick={()=>{
            const copied=independentBranch(project,node.id);
            snapshot();setWorkspace(w=>({...w,projects:[...w.projects,copied]}));
            navigate({page:'flow',projectId:copied.id,nodeId:copied.nodes.find(n=>n.data.kind!=='structure')?.id||null});
            toast('已复制为独立项目，请核对结构和输入');
           }}>复制为独立项目</button>
          </div>}</details>}<details className="workflow-node-edit" open={nodeEditOpen} onToggle={e=>setNodeEditOpen(e.currentTarget.open)}><summary>编辑节点</summary>{nodeEditOpen&&<div className="inspector-body"><NodeTypeSwitch key={project.id+node.id} project={project} node={node} disabled={!disk.ready||syncReadOnly||Boolean(disk.error)} onCopy={copyNode} onApply={next=>{changeProject(p=>({...p,nodes:p.nodes.map(n=>n.id===next.id?next:n)}));toast("已切换计算类型，请核对参数、来源和连线");}}/><label className="field">节点名称（可用中文）<input value={node.data.label} maxLength={100} onFocus={snapshot} onChange={e=>updateData('label',e.target.value)} onBlur={()=>{if(!node.data.label.trim())updateData('label',catalog[node.data.kind].label);}}/></label>{node.data.kind!=='structure'&&<p className="node-directory-hint">服务器阶段目录：<code>{String(node.data.stageSequence).padStart(2,'0')}-{node.data.stageSlug}</code></p>}<div className="field-heading">{node.data.kind==='structure'?'结构信息':node.data.kind==='scan'?'参数迭代':node.data.kind==='wannier90'?'Wannier90 参数与接口':'计算配置'}<span>草稿</span></div>{node.data.kind==='scan'?<><p>配置本节点的计算类型、上游和扫描范围；任务与结果在本节点查看。</p><button className="button primary" onClick={()=>openNodeConfiguration()}>打开参数迭代</button></>:node.data.kind==='wannier90'?<><p>从已完成的 VASP-Wannier 接口读取 .win 和矩阵；请在 Wannier90 计算配置页选择真实来源与通道。</p><button className="button primary" onClick={()=>openNodeConfiguration()}>打开 Wannier90 参数</button></>:node.data.kind==='structure'?<label className="field">材料标记<input value={node.data.params.material||''} maxLength={200} onFocus={snapshot} onChange={e=>updateData('params',{...node.data.params,material:e.target.value})}/><small>{node.data.structure?'已绑定真实结构；可在结构页查看。':'仅用于标记；尚未导入结构文件。'}</small></label>:<><button className="button primary" onClick={()=>openNodeConfiguration()}>打开计算配置</button></>}<label className="field">备注<textarea rows={3} maxLength={2000} value={node.data.note} placeholder="记录这个步骤的用途…" onFocus={snapshot} onChange={e=>updateData('note',e.target.value)}/></label><div className="dependency-box"><Link2 size={15}/><span>上游节点</span><strong>{project.edges.filter(e=>e.target===node.id).length}</strong></div></div>}</details></>:<><ProjectOverview project={project} attempts={attemptProjectId===project.id?attemptRows:[]} loading={attemptStatus==='loading'||attemptProjectId!==project.id&&attemptStatus!=='error'} error={attemptStatus==='error'} onRefresh={()=>setAttemptRetry(n=>n+1)} onOpen={openWorkspace}/><details className="workflow-project-sync" onToggle={e=>setSnapshotOpen(e.currentTarget.open)}><summary>项目快照</summary>{snapshotOpen&&<ProjectSync project={project} disk={disk} onChanged={()=>{setHistory([]);setAttemptRetry(n=>n+1);setSelected(null);setIssues([]);}}/>}</details></>}</div></aside></>}
      </div><footer className="statusbar"><span className="workspace-save-state" role="status" title={disk.error||disk.path}><span className="status-square"/>{disk.error?'保存失败 · 请打开保存与恢复':saveState.replace('到本机工作区','').replace('到本机…','…')}</span><button className="workspace-recovery-trigger" onClick={()=>setRecoveryOpen(true)}>保存与恢复</button><span>{project.nodes.length} 个节点 · {project.edges.length} 条连接</span><span>First Principles Pilot <b>{__FPILOT_VERSION__}</b></span></footer>
    </main>
    {message&&<div className="toast" role="status"><Check size={17}/>{message}<button className="icon-btn" aria-label="关闭提示" onClick={()=>setMessage('')}><X size={15}/></button></div>}
    {modal&&<div className="modal-backdrop" onClick={()=>setModal(null)}><section className={`modal ${modal==='appearance'?'appearance-modal':''}`} role="dialog" aria-modal="true" aria-label={modal==='appearance'?'外观设置':modal==='project'?'新建项目':modal==='connect'?'连接节点':modal==='checks'?'流程检查':'使用说明'} onClick={e=>e.stopPropagation()}><button className="modal-close icon-btn" aria-label="关闭对话框" onClick={()=>setModal(null)}><X size={20}/></button>{modal==='checks'?<><h2>流程检查</h2><p>检查节点连接与循环依赖；物理参数和来源仍需在冻结前审核。</p><div className="check-list flow-check-results">{issues.map((i,index)=><button key={index} className={`check-item ${i.level}`} onClick={()=>{if(i.nodeId){setSelected(i.nodeId);setSideOpen(true);setTab('flow');setModal(null);}}}>{i.level==='error'?<CircleHelp size={17}/>:<Check size={17}/>}<span>{i.message}</span>{i.nodeId&&<ChevronRight size={15}/>}</button>)}</div></>:modal==='appearance'?<AppearancePanel/>:modal==='project'?<form onSubmit={e=>{e.preventDefault();if(!newName.trim()||!newRemoteValid||newRemoteTaken||!newRemoteRootValid)return;if(workspace.projects.length>=100){toast('项目数量已达 100 上限');return;}snapshot();const p=newProject(newName.trim());p.remoteSlug=newRemoteSlug;p.remoteWorkspaceRoot=newRemoteRoot;setWorkspace(w=>({...w,activeId:p.id,projects:[...w.projects,p]}));navigate({projectId:p.id,page:'flow',nodeId:p.nodes[0].id});setIssues([]);setModal(null);toast('已创建项目');}}><span className="eyebrow">NEW PROJECT</span><h2>新建计算项目</h2><p>为材料或研究目标建立一个独立工作区。</p><label className="field">项目名称<input autoFocus required maxLength={120} placeholder="例如 CrFeO · 磁性研究" value={newName} onChange={e=>{const value=e.target.value;setNewName(value);if(!newRemoteSlug||newRemoteSlug===slugify(newName))setNewRemoteSlug(slugify(value));}}/></label><label className="field">远端项目目录名<input required maxLength={64} placeholder="例如 fecr2o4-test" value={newRemoteSlug} onChange={e=>setNewRemoteSlug(e.target.value)} onBlur={()=>setNewRemoteSlug(slugify(newRemoteSlug))}/></label><label className="field">工作区根目录（所有服务器通用）<input required value={newRemoteRoot} placeholder="/scratch/my-projects" onChange={e=>setNewRemoteRoot(e.target.value)}/></label><p>现在不必选择服务器。$HOME 会在提交时解析为该服务器的用户主目录；新目录使用英文命名。</p>{!newRemoteRootValid&&<p role="alert">请填写英文绝对路径，或 $HOME 下的英文路径。</p>}<div className="parameter-note path-layout-note"><FolderOpen size={18}/><span>项目目录预览 · 提交时再选服务器<br/>{newRemoteRoot.replace(/\/$/,'')}/<strong>{newRemoteSlug||'项目目录名'}</strong>/</span></div>{newRemoteTaken?<p role="alert">当前工作区已有这个远端目录名，请换一个名称。</p>:newRemoteSlug&&!newRemoteValid?<p role="alert">使用小写英文字母、数字和短横线，最多 64 个字符。</p>:null}<button className="button primary" type="submit" disabled={!newName.trim()||!newRemoteValid||newRemoteTaken||!newRemoteRootValid}><Plus size={16}/>创建项目</button></form>:modal==='connect'?<form onSubmit={e=>{e.preventDefault();connect({source,target});}}><span className="eyebrow">CONNECT STEPS</span><h2>连接计算节点</h2><p>选择数据从哪个步骤流向哪个步骤。</p><label className="field">上游节点<select autoFocus value={source} onChange={e=>setSource(e.target.value)}>{project.nodes.map(n=><option key={n.id} value={n.id}>{n.data.label}</option>)}</select></label><label className="field">下游节点<select value={target} onChange={e=>setTarget(e.target.value)}><option value="">选择下游节点</option>{project.nodes.filter(n=>n.data.kind!=='structure').map(n=><option key={n.id} value={n.id}>{n.data.label}</option>)}</select></label><button className="button primary" disabled={!source||!target} type="submit"><Link2 size={16}/>建立连接</button></form>:<><span className="eyebrow">GETTING STARTED</span><h2>从结构到计算结果</h2><ol className="help-list"><li>创建项目，或编辑预置的 Si 演示流程。</li><li>添加节点，从右侧端口拖向下游左侧端口；也可用连接按钮选择节点。</li><li>选中计算节点后打开“计算配置”，按顺序填写物理参数、k 点和赝势，并审查实际文件。</li><li>修改自动保存到本机工作区。导出 JSON 可备份全部项目，导入会追加为新项目。</li></ol><div className="parameter-note"><CircleHelp size={18}/><span>结构导入后，在计算配置页设置参数、k 点与赝势并冻结输入；进入运行与监控页审阅当前节点或整条计算链。结果与后处理页提供执行证据、绘图和分析；科学结论仍需单独审核。同一本机服务共用磁盘工作区，请等待保存完成再关闭。</span></div><button className="button primary" onClick={()=>setModal(null)}>开始编辑</button></>}</section></div>}
    <UnifiedRecycleBin onRestoreProject={restoreProject} onRestoreNode={restoreNode}/>
    {deletingProject&&<LifecycleDialog target={{kind:'projects',key:deletingProject.id,project:stateRef.current.projects.find(p=>p.id===deletingProject.id)}} label={deletingProject.name} onClose={()=>setDeletingProject(null)} onDone={()=>projectRecycled(deletingProject)}/>}
    {deletingNode&&<LifecycleDialog target={{kind:'nodes',key:deletingNode.nodeId,project_id:deletingNode.projectId,project:stateRef.current.projects.find(p=>p.id===deletingNode.projectId)}} label={deletingNode.label} plan={deletingNode.plan} onClose={()=>setDeletingNode(null)} onDone={()=>removeNodeDraft(deletingNode.projectId,deletingNode.nodeId,true)}/>}
    {recoveryOpen&&<WorkspaceRecoveryDialog disk={disk} workspace={workspace} legacyRaw={initial.raw} onClose={()=>setRecoveryOpen(false)} onAppend={value=>{snapshot();const merged=mergeWorkspace(stateRef.current,value,defaultWorkspaceRoot);setWorkspace(merged);navigate({page:'flow',projectId:merged.activeId,nodeId:null});setIssues([]);setRecoveryOpen(false);toast('已从检查点恢复为新项目，原项目保留');}}/>}
  </div>;
}

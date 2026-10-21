import {useState} from 'react';
import {ArrowRight,RefreshCw} from 'lucide-react';
import {RecycleBin} from './RecordCleanup';
import type {Project} from './model';
import {latestByNode,nextAction,taskTemplates} from './resultWorkspace';
import type {ResultAttempt} from './resultWorkspace';
import {attemptLabel} from './humanLabels';
import {statusLabel} from './statusLabels';
export default function ProjectOverview({project,attempts,loading,error,onOpen,onRefresh,onTemplate,onDelete}:{project:Project;attempts:ResultAttempt[];loading:boolean;error:boolean;onOpen:(view:string,nodeId?:string,attemptId?:string)=>void;onRefresh:()=>void;onTemplate:(id:string)=>void;onDelete:()=>void}){
 const [management,setManagement]=useState(false);
 const latest=latestByNode(attempts),calculations=project.nodes.filter(n=>n.data.kind!=='structure'),structures=project.nodes.filter(n=>n.data.kind==='structure');
 const attention=attempts.filter(a=>['UNKNOWN','FAILED','DISPATCHING'].includes(a.state));
 return <section className="workflow-project-overview"><div className="workflow-project-heading"><h2>{project.name}</h2><button className="icon-btn" aria-label="刷新本机项目记录" disabled={loading} onClick={onRefresh}><RefreshCw size={15}/></button></div><p className="summary-muted">{structures.length} 个结构 · {calculations.length} 个计算节点</p>
  {error&&<div className="summary-warning" role="alert">本机记录暂不可读，保留已有信息。<button className="text-button" onClick={onRefresh}>重试</button></div>}
  <details className="workflow-project-section" open><summary>待处理记录 <span>{attention.length}</span></summary>{attention.length?<div className="workflow-attention-list">{attention.slice(0,5).map(a=><button key={a.id} title={attemptLabel(a,project,true)} onClick={()=>onOpen('results',String(a.owner_node_id||a.execution_scope||a.node_id||''),a.id)}><span>{attemptLabel(a,project)}</span><strong>{statusLabel(a.state)}</strong></button>)}</div>:<p className="summary-muted">暂无待处理记录。</p>}</details>
  <details className="workflow-project-section" open><summary>节点下一步</summary>{!structures.some(n=>n.data.structure)&&<button className="workflow-next-action" onClick={()=>onOpen('structure',structures[0]?.id)}><span>导入初始结构</span><ArrowRight size={14}/></button>}<div className="workflow-next-actions">{calculations.map(node=>{const attempt=latest[node.id],action=nextAction(attempt);return <button className="workflow-next-action" key={node.id} disabled={error||loading} onClick={()=>onOpen(node.data.kind==='scan'?'run':action.view,node.id,action.view==='results'?attempt?.id:undefined)}><span>{node.data.label}<small>{node.data.kind==='scan'?'查看迭代批次':action.label}</small></span><ArrowRight size={14}/></button>;})}</div></details>
  <details className="workflow-project-section"><summary>从模板建立流程</summary><div className="workflow-template-list">{taskTemplates.map(t=><button key={t.id} onClick={()=>onTemplate(t.id)} title={t.description}>{t.name}<ArrowRight size={14}/></button>)}</div></details>
  <details className="workflow-project-section" onToggle={e=>setManagement(e.currentTarget.open)}><summary>项目管理与回收</summary>{management&&<><button className="button danger" onClick={onDelete}>删除整个项目</button><RecycleBin kind="attempts" projectId={project.id} title="计算记录回收区" onChanged={onRefresh}/></>}</details>
 </section>;
}

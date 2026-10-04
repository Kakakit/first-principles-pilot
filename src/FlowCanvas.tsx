import {ReactFlow,Background,Controls,MiniMap,Handle,Position,applyNodeChanges} from '@xyflow/react';
import type {Connection,NodeProps,NodeChange,Node} from '@xyflow/react';
import type {Step,Project} from './model';
import {catalog,canConnect} from './model';
import {statusLabel} from './statusLabels';
import {useAppearance} from './useAppearance';
import {useLayoutEffect,useRef} from 'react';
import {Activity,Atom,Box,GitBranch,Layers,MoreHorizontal} from 'lucide-react';
const icons={structure:Box,relax:Atom,scf:Activity,bands:GitBranch,dos:Layers,'vasp-wannier':Atom,wannier90:Atom,scan:Layers};
export type CaseFlowNode=Node<{label:string;state:string},'case'>;
function StepCard({data, selected}: NodeProps<Step>) {
  const card=useRef<HTMLDivElement>(null);const measured=useRef(data.onCardResize as undefined|((height:number)=>void));measured.current=data.onCardResize as typeof measured.current;
  useLayoutEffect(()=>{if(!data.expanded||!card.current)return;const report=()=>measured.current?.(Math.ceil(card.current!.offsetHeight));const observer=new ResizeObserver(report);observer.observe(card.current);report();return()=>observer.disconnect();},[data.expanded]);
  const meta=catalog[data.kind], Icon=icons[data.kind];
  const resultState=typeof data.resultState==='string'?data.resultState:'';const openResults=data.openResults as undefined|(()=>void);
  const attemptStatus=data.attemptStatus as 'loading'|'ready'|'error'|undefined;
  const toggleExpand=data.toggleExpand as undefined|(()=>void);
  const openChain=data.openChain as undefined|(()=>void);
  return <div ref={card} className={`step-card ${selected?'is-selected':''} ${data.kind==='scan'&&data.expanded?'scan-expanded':''}`} style={{'--node-color':meta.color} as React.CSSProperties}>
    {data.kind!=='structure' && <Handle type="target" position={Position.Left} aria-label={`${data.label}输入`}/>}
    <div className="step-top"><span className="step-icon"><Icon size={19}/></span><span className="step-en">{meta.en}</span><MoreHorizontal size={16}/></div>
    <strong>{data.label}</strong><p>{data.kind==='structure'?data.params.material || '未指定结构':meta.description}</p>
    <div className="step-foot"><span>{data.kind==='structure'?(data.structure?'已导入':'待导入'):data.kind==='scan'?'批次容器':'草稿'}</span>{resultState&&openResults?<button className="node-result-button" onClick={event=>{event.stopPropagation();openResults();}}>结果 · {statusLabel(resultState)}</button>:<span>{data.kind==='scan'?'按批次执行':attemptStatus==='error'?'状态暂不可读':attemptStatus==='loading'?'正在读取状态':'未提交'}</span>}</div>
    {Boolean(data.chainState)&&openChain&&<button className="node-result-button" onClick={event=>{event.stopPropagation();openChain();}}>计算链 · {String(data.chainState)}</button>}
    {data.kind==='scan'&&toggleExpand&&<button className="node-result-button" onClick={event=>{event.stopPropagation();toggleExpand();}}>{data.expanded?'收起 case':'展开 case'}</button>}
    <Handle type="source" position={Position.Right} aria-label={`${data.label}输出`}/>
  </div>;
}
function CaseCard({data}:NodeProps<CaseFlowNode>){return <div className="scan-case-node"><strong>{data.label}</strong><span>{data.state}</span></div>;}
const nodeTypes={step:StepCard,case:CaseCard};

export default function FlowCanvas({project,nodes,edgeId,onChange,onNodeClick,onEdgeClick,onPaneClick,onConnect,onDragStart}:{project:Project;nodes:(Step|CaseFlowNode)[];edgeId:string|null;onChange:(nodes:Step[])=>void;onNodeClick:(id:string)=>void;onEdgeClick:(id:string)=>void;onPaneClick:()=>void;onConnect:(c:Connection)=>void;onDragStart:()=>void}){
 const {theme}=useAppearance();
 return <ReactFlow key={project.id} colorMode={theme.appearance} nodes={nodes} edges={project.edges.map(e=>({...e,selected:e.id===edgeId,style:{stroke:theme.colors.canvasEdge,strokeWidth:1.7}}))} nodeTypes={nodeTypes} onNodesChange={(changes:NodeChange<Step|CaseFlowNode>[])=>{const tracked=changes.filter(c=>'id' in c&&project.nodes.some(n=>n.id===c.id)) as NodeChange<Step>[];if(tracked.length)onChange(applyNodeChanges(tracked,project.nodes));}} onNodeDragStart={onDragStart} onNodeClick={(_,n)=>onNodeClick(n.id)} onEdgeClick={(_,e)=>onEdgeClick(e.id)} onPaneClick={onPaneClick} onConnect={onConnect} isValidConnection={c=>canConnect(project.nodes,project.edges,c.source,c.target)} fitView fitViewOptions={{padding:0.15}} minZoom={0.25} maxZoom={1.7} deleteKeyCode={null} defaultEdgeOptions={{type:'smoothstep'}} proOptions={{hideAttribution:false}} aria-label="计算流程画布"><Background gap={20} size={1} color={theme.colors.canvasGrid}/><Controls showInteractive={false}/><MiniMap nodeColor={n=>n.type==='case'?theme.colors.mutedText:catalog[(n.data as Step['data']).kind].color} maskColor={theme.colors.canvas+'b3'} bgColor={theme.colors.surface} pannable zoomable/></ReactFlow>;
}

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
  const chainSelection=data.chainSelection as string|undefined;
  const toggleChain=data.toggleChain as undefined|(()=>void);
  const chainLocked=Boolean(data.chainLocked);
  return <div ref={card} className={`step-card ${selected?'is-selected':''} ${data.kind==='scan'&&data.expanded?'scan-expanded':''} ${chainSelection?'chain-'+chainSelection:''}`} style={{'--node-color':meta.color} as React.CSSProperties}
    role={toggleChain?'button':undefined} tabIndex={toggleChain&&!chainLocked?0:undefined} aria-pressed={toggleChain?selected:undefined} aria-disabled={toggleChain?chainLocked:undefined}
    onKeyDown={event=>{if(toggleChain&&!chainLocked&&(event.key==='Enter'||event.key===' ')){event.preventDefault();event.stopPropagation();toggleChain();}}}>
    {data.kind!=='structure' && <Handle type="target" position={Position.Left} aria-label={`${data.label}输入`}/>}
    <div className="step-top"><span className="step-icon"><Icon size={19}/></span><span className="step-en">{meta.en}</span><MoreHorizontal size={16}/></div>
    <strong>{data.label}</strong><p>{data.kind==='structure'?data.params.material || '未指定结构':meta.description}</p>
    {chainSelection?<div className="step-foot chain-selection-foot"><span>{chainSelection==='execute'?'本次执行':chainSelection==='reuse'?'复用历史结果':chainSelection==='context'?'流程上下文':'本次不执行'}</span></div>:
      <div className="step-foot"><span>{data.kind==='structure'?(data.structure?'已导入':'待导入'):data.kind==='scan'?'批次容器':'草稿'}</span>{resultState&&openResults?<button className="node-result-button" onClick={event=>{event.stopPropagation();openResults();}}>结果 · {statusLabel(resultState)}</button>:<span>{data.kind==='scan'?'按批次执行':attemptStatus==='error'?'状态暂不可读':attemptStatus==='loading'?'正在读取状态':'未提交'}</span>}</div>}
    {Boolean(data.chainState)&&openChain&&<button className="node-result-button" onClick={event=>{event.stopPropagation();openChain();}}>计算链 · {String(data.chainState)}</button>}
    {data.kind==='scan'&&toggleExpand&&<button className="node-result-button" onClick={event=>{event.stopPropagation();toggleExpand();}}>{data.expanded?'收起 case':'展开 case'}</button>}
    <Handle type="source" position={Position.Right} aria-label={`${data.label}输出`}/>
  </div>;
}
function CaseCard({data}:NodeProps<CaseFlowNode>){return <div className="scan-case-node"><strong>{data.label}</strong><span>{data.state}</span></div>;}
const nodeTypes={step:StepCard,case:CaseCard};
const multiSelectionKeys=['Meta','Control'];

export default function FlowCanvas({project,nodes,edgeId=null,onChange,onSelectionChange,onNodeClick,onEdgeClick,onPaneClick,onConnect,onDragStart,mode='edit'}:{project:Project;nodes:(Step|CaseFlowNode)[];edgeId?:string|null;onChange?:(nodes:Step[])=>void;onSelectionChange?:(ids:string[])=>void;onNodeClick:(id:string)=>void;onEdgeClick?:(id:string)=>void;onPaneClick?:()=>void;onConnect?:(c:Connection)=>void;onDragStart?:()=>void;mode?:'edit'|'chain-select'}){
 const {theme}=useAppearance();
 const editing=mode==='edit',dragging=useRef(false);
 const selectedIds=nodes.filter(node=>node.selected).map(node=>node.id);
 const beginDrag=()=>{if(!dragging.current){dragging.current=true;onDragStart?.();}};
 const endDrag=()=>{dragging.current=false;};
 const changeNodes=(changes:NodeChange<Step|CaseFlowNode>[])=>{
  if(!editing)return;
  const selectionChanges=changes.filter(change=>change.type==='select');
  if(selectionChanges.length)onSelectionChange?.(applyNodeChanges(selectionChanges,nodes).filter(node=>node.selected&&project.nodes.some(item=>item.id===node.id)).map(node=>node.id));
  const positions=changes.filter(change=>change.type==='position'&&change.position&&project.nodes.some(node=>node.id===change.id)) as NodeChange<Step>[];
  if(positions.length)onChange?.(applyNodeChanges(positions,project.nodes));
 };
 return <ReactFlow key={project.id} colorMode={theme.appearance} nodes={nodes} edges={project.edges.map(edge=>({...edge,selected:editing&&edge.id===edgeId,style:{stroke:theme.colors.canvasEdge,strokeWidth:1.7}}))} nodeTypes={nodeTypes} onNodesChange={changeNodes}
  nodesDraggable={editing} nodesConnectable={editing} edgesReconnectable={editing} elementsSelectable={editing} nodesFocusable={editing} edgesFocusable={editing}
  selectNodesOnDrag={false}
  selectionKeyCode={editing?'Shift':null} multiSelectionKeyCode={editing?multiSelectionKeys:null}
  onNodeDragStart={beginDrag} onSelectionDragStart={beginDrag} onNodeDragStop={endDrag} onSelectionDragStop={endDrag}
  onNodeClick={(event,node)=>{if(editing&&project.nodes.some(item=>item.id===node.id))onSelectionChange?.(event.metaKey||event.ctrlKey?(selectedIds.includes(node.id)?selectedIds.filter(id=>id!==node.id):[...selectedIds,node.id]):[node.id]);onNodeClick(node.id);}} onEdgeClick={editing?(_,edge)=>onEdgeClick?.(edge.id):undefined} onPaneClick={onPaneClick} onConnect={editing?onConnect:undefined} isValidConnection={connection=>editing&&canConnect(project.nodes,project.edges,connection.source,connection.target)}
  fitView fitViewOptions={{padding:0.15}} minZoom={0.25} maxZoom={1.7} deleteKeyCode={null} defaultEdgeOptions={{type:'smoothstep'}} proOptions={{hideAttribution:false}} aria-label={editing?'计算流程画布':'计算链节点选择画布'}>
  <Background gap={20} size={1} color={theme.colors.canvasGrid}/><Controls showInteractive={false}/><MiniMap nodeColor={node=>node.type==='case'?theme.colors.mutedText:catalog[(node.data as Step['data']).kind].color} maskColor={theme.colors.canvas+'b3'} bgColor={theme.colors.surface} pannable zoomable/>
 </ReactFlow>;
}

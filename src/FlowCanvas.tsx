import {structureOriginEdges} from './structureOrigin';
import {NodeIcon,nodePalette} from './nodeAppearance';
import {ReactFlow,Background,Controls,MiniMap,Handle,Position,applyNodeChanges} from '@xyflow/react';
import type {Connection,NodeProps,NodeChange,Viewport,ReactFlowInstance} from '@xyflow/react';
import type {Step,Project} from './model';
import {catalog,canConnect} from './model';
import {statusLabel} from './statusLabels';
import {useAppearance} from './useAppearance';
import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {measuredCanvasNodes,updateCanvasMeasurements} from './canvasState';
import type {CanvasMeasurements} from './canvasState';
function StepCard({data, selected}: NodeProps<Step>) {
  const meta=catalog[data.kind];const {theme}=useAppearance();const palette=nodePalette[data.kind][theme.appearance];
  const resultState=typeof data.resultState==='string'?data.resultState:'';const openResults=data.openResults as undefined|(()=>void);
  const attemptStatus=data.attemptStatus as 'loading'|'ready'|'error'|undefined;
  const toggleExpand=data.toggleExpand as undefined|(()=>void);
  const openChain=data.openChain as undefined|(()=>void);
  const chainSelection=data.chainSelection as string|undefined;
  const toggleChain=data.toggleChain as undefined|(()=>void);
  const chainLocked=Boolean(data.chainLocked);
  const inheritance=data.inheritance as {text:string;title:string}|undefined;
  return <div className={`step-card ${selected?'is-selected':''} ${data.kind==='scan'&&data.expanded?'scan-expanded':''} ${chainSelection?'chain-'+chainSelection:''}`} style={{'--node-color':palette.ink,'--node-fill':palette.fill,'--node-border':palette.border} as React.CSSProperties}
    role={toggleChain?'button':undefined} tabIndex={toggleChain&&!chainLocked?0:undefined} aria-pressed={toggleChain?selected:undefined} aria-disabled={toggleChain?chainLocked:undefined}
    onKeyDown={event=>{if(toggleChain&&!chainLocked&&(event.key==='Enter'||event.key===' ')){event.preventDefault();event.stopPropagation();toggleChain();}}}>
    {data.kind==='structure'&&Boolean(data.structureOrigin)&&<Handle id="structure-origin" type="target" position={Position.Left} isConnectable={false} style={{opacity:0}}/>}
    {data.kind!=='structure' && <Handle type="target" position={Position.Left} aria-label={`${data.label}输入`}/>}
    <div className="step-top"><span className="step-icon"><NodeIcon kind={data.kind} size={24}/></span><span className="step-en">{meta.label}</span></div>
    <strong title={data.label}>{data.label}</strong>{data.kind==='structure'&&<p>{data.params.material||'未指定结构'}</p>}
    {chainSelection?<div className="step-foot chain-selection-foot"><span>{chainSelection==='execute'?'本次执行':chainSelection==='reuse'?'复用历史结果':chainSelection==='context'?'流程上下文':'本次不执行'}</span></div>:
      <div className="step-foot"><span>{data.kind==='structure'?(data.structure?'已导入':'待导入'):data.kind==='scan'?'批次容器':resultState?'运行记录':'配置草稿'}</span>{data.kind!=='structure'&&(resultState&&openResults?<button className="node-result-button" onClick={event=>{event.stopPropagation();openResults();}}>结果 · {statusLabel(resultState)}</button>:<span>{data.kind==='scan'?'按批次执行':attemptStatus==='error'?'状态暂不可读':attemptStatus==='loading'?'正在读取状态':'未提交'}</span>)}</div>}
    {data.kind!=='structure'&&data.kind!=='wannier90'&&inheritance&&<div className="node-inheritance" title={inheritance.title}><span>{inheritance.text}</span></div>}
    {data.kind==='scan'&&Boolean(data.resultHint)&&<div className="node-inheritance" title={String(data.resultHint)}><span>{String(data.resultHint)}</span></div>}
    {data.kind==='structure'&&typeof data.openStructure==='function'&&<button className="node-result-button nodrag nopan" onClick={event=>{event.stopPropagation();(data.openStructure as ()=>void)();}}>打开结构详情</button>}
    {Boolean(data.chainState)&&openChain&&<button className="node-result-button node-chain-link" title={String(data.chainState)} onClick={event=>{event.stopPropagation();openChain();}}>计算链 · {String(data.chainState).split(' · ').map(part=>part==='DONE'?'节点已结束':statusLabel(part)).join(' / ')}</button>}
    {data.kind==='scan'&&toggleExpand&&<button className="node-result-button" onClick={event=>{event.stopPropagation();toggleExpand();}}>查看 case 列表</button>}
    <Handle type="source" position={Position.Right} aria-label={`${data.label}输出`}/>
  </div>;
}
const nodeTypes={step:StepCard};
const multiSelectionKeys=['Meta','Control'];

export default function FlowCanvas({project,nodes,edgeId=null,onChange,onSelectionChange,onNodeClick,onNodeOpen,onEdgeClick,onPaneClick,onConnect,onDragStart,initialViewport,onViewportChange,focusNode,mode='edit'}:{project:Project;nodes:Step[];edgeId?:string|null;onChange?:(nodes:Step[])=>void;onSelectionChange?:(ids:string[])=>void;onNodeClick:(id:string)=>void;onNodeOpen?:(id:string)=>void;onEdgeClick?:(id:string)=>void;onPaneClick?:()=>void;onConnect?:(c:Connection)=>void;onDragStart?:()=>void;initialViewport?:Viewport;onViewportChange?:(viewport:Viewport)=>void;focusNode?:{id:string;sequence:number};mode?:'edit'|'chain-select'}){
 const {theme}=useAppearance();
 const editing=mode==='edit',dragging=useRef(false);
 const container=useRef<HTMLDivElement>(null);const [flow,setFlow]=useState<ReactFlowInstance<Step>|null>(null);
 useEffect(()=>{if(!flow||!container.current)return;let width=container.current.clientWidth,height=container.current.clientHeight;const observer=new ResizeObserver(([entry])=>{const next=entry.contentRect;if(next.width>0&&next.height>0&&(Math.abs(next.width-width)>.5||Math.abs(next.height-height)>.5)){const v=flow.getViewport();void flow.setViewport({...v,x:v.x+(next.width-width)/2,y:v.y+(next.height-height)/2});}width=next.width;height=next.height;});observer.observe(container.current);return()=>observer.disconnect();},[flow]);
 useLayoutEffect(()=>{if(!flow||!focusNode)return;const node=project.nodes.find(n=>n.id===focusNode.id);if(node)void flow.setCenter(node.position.x+140,node.position.y+75,{zoom:1});},[flow,focusNode]);
 const [measurements,setMeasurements]=useState<CanvasMeasurements>(()=>({projectId:project.id,dimensions:new Map()}));
 const renderNodes=measuredCanvasNodes(nodes,project.id,measurements);
 const selectedIds=nodes.filter(node=>node.selected).map(node=>node.id);
 const beginDrag=()=>{if(!dragging.current){dragging.current=true;onDragStart?.();}};
 const endDrag=()=>{dragging.current=false;};
 const changeNodes=(changes:NodeChange<Step>[])=>{
  if(changes.some(change=>change.type==='dimensions'))setMeasurements(previous=>updateCanvasMeasurements(previous,project.id,nodes,changes));
  if(!editing)return;
  const selectionChanges=changes.filter(change=>change.type==='select');
  if(selectionChanges.length)onSelectionChange?.(applyNodeChanges(selectionChanges,nodes).filter(node=>node.selected&&project.nodes.some(item=>item.id===node.id)).map(node=>node.id));
  const positions=changes.filter(change=>change.type==='position'&&change.position&&Number.isFinite(change.position.x)&&Number.isFinite(change.position.y)&&project.nodes.some(node=>node.id===change.id)) as NodeChange<Step>[];
  if(positions.length)onChange?.(applyNodeChanges(positions,project.nodes));
 };
 return <div className="workflow-canvas-root" ref={container}><ReactFlow<Step> key={project.id} onInit={setFlow} onMoveEnd={(_,viewport)=>onViewportChange?.(viewport)} defaultViewport={initialViewport} colorMode={theme.appearance} nodes={renderNodes} edges={project.edges.map(edge=>({...edge,selected:editing&&edge.id===edgeId,style:{stroke:theme.colors.canvasEdge,strokeWidth:1.7}})).concat(structureOriginEdges(project).map(edge=>({...edge,selected:false,style:{stroke:theme.colors.canvasEdge,strokeWidth:1.5,strokeDasharray:'4 4'},labelStyle:{fill:theme.colors.text,fontSize:12},labelBgStyle:{fill:theme.colors.surface},labelBgPadding:[8,4] as [number,number]})))} nodeTypes={nodeTypes} onNodesChange={changeNodes}
  nodesDraggable={editing} nodesConnectable={editing} edgesReconnectable={editing} elementsSelectable={editing} nodesFocusable={editing} edgesFocusable={editing}
  selectNodesOnDrag={false}
  selectionKeyCode={editing?'Shift':null} multiSelectionKeyCode={editing?multiSelectionKeys:null}
  onNodeDragStart={beginDrag} onSelectionDragStart={beginDrag} onNodeDragStop={endDrag} onSelectionDragStop={endDrag}
  onNodeDoubleClick={(event,node)=>{if(!editing||!onNodeOpen||(event.target as HTMLElement).closest('button,a,input,select,textarea'))return;event.preventDefault();event.stopPropagation();onNodeOpen(node.id);}}
  onNodeClick={(event,node)=>{if(editing&&project.nodes.some(item=>item.id===node.id))onSelectionChange?.(event.metaKey||event.ctrlKey?(selectedIds.includes(node.id)?selectedIds.filter(id=>id!==node.id):[...selectedIds,node.id]):[node.id]);onNodeClick(node.id);}} onEdgeClick={editing?(_,edge)=>{if(project.edges.some(item=>item.id===edge.id))onEdgeClick?.(edge.id);}:undefined} onPaneClick={onPaneClick} onConnect={editing?onConnect:undefined} isValidConnection={connection=>editing&&canConnect(project.nodes,project.edges,connection.source,connection.target)}
  fitView={!initialViewport} fitViewOptions={{padding:0.15,minZoom:editing?.8:undefined,maxZoom:editing?1:undefined,nodes:editing?project.nodes.slice(0,3):undefined}} minZoom={0.25} maxZoom={1.7} deleteKeyCode={null} defaultEdgeOptions={{type:'smoothstep'}} proOptions={{hideAttribution:false}} aria-label={editing?'计算流程画布':'计算链节点选择画布'}>
  <Background gap={20} size={1} color={theme.colors.canvasGrid}/><Controls showInteractive={false}/><MiniMap nodeColor={node=>catalog[(node.data as Step['data']).kind].color} maskColor={theme.colors.canvas+'b3'} bgColor={theme.colors.surface} pannable zoomable/>
 </ReactFlow></div>;
}

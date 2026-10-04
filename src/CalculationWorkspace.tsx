import type {WindowDraft,WindowAdoption} from './windowScan';
import SourcePanel from './SourcePanel';
import type {SourceDraft} from './SourcePanel';
import {useState,useEffect} from 'react';
import type {Project} from './model';
import type {ParameterLayer} from './parameters';
import ParameterPanel from './ParameterPanel';
import InputPanel from './InputPanel';
import ExecutionPanel from './ExecutionPanel';
import WannierPanel from './WannierPanel';
import ScanPanel from './ScanPanel';

type Props={
  project:Project;nodeId:string;view:'config'|'run';bundleId?:string;
  onLayer:(nodeId:string|null,layer:ParameterLayer)=>void;
  onTemplates:(templates:NonNullable<NonNullable<Project['parameters']>['templates']>)=>void;
  onSource:(nodeId:string,value:SourceDraft)=>void;
  onDraft:NonNullable<Parameters<typeof InputPanel>[0]['onDraft']>;
  onSharedPotcar:NonNullable<Parameters<typeof InputPanel>[0]['onSharedPotcar']>;
  onNumWann:(nodeId:string,value:string)=>void;
  onProjections:(nodeId:string,value:string)=>void;
  onWindowChange:(value:WindowDraft)=>void;onWindowAdopt:(value:WindowAdoption)=>void;
  onScanDraft:NonNullable<Parameters<typeof ScanPanel>[0]['onDraft']>;
  onReview:(bundleId?:string)=>void;onConfigure:()=>void;onResults:(nodeId?:string)=>void;
};

export default function CalculationWorkspace(props:Props) {
  const {project,nodeId,view,onReview,onConfigure}=props;
  const node=project.nodes.find(item=>item.id===nodeId)!;
  useEffect(()=>{if(node.data.executionMode==='vasp'&&!['structure','scan','wannier90'].includes(node.data.kind)&&!node.data.sourceDraft){const parent=project.nodes.find(n=>n.id===project.edges.find(e=>e.target===nodeId)?.source);props.onSource(nodeId,{mode:parent&&parent.data.kind!=='structure'?'upstream':'new',purpose:'charge',files:'charge'});}},[nodeId]);
  const [part,setPart]=useState<'physics'|'files'>('physics');
  const [visitedRun,setVisitedRun]=useState(view==='run');
  // Keep the configuration mounted while reviewing a run: channel drafts and
  // review state belong to this exact project/node, not a new page instance.
  if(view==='run'&&!visitedRun)setVisitedRun(true);
  if(node.data.kind==='wannier90')return <WannierPanel project={project} initialNodeId={nodeId} embedded view={view} onReview={()=>onReview()} onConfigure={onConfigure}/>;
  if(node.data.kind==='scan')return <ScanPanel onWindowChange={props.onWindowChange} onWindowAdopt={props.onWindowAdopt} onSource={value=>props.onSource(nodeId,value)} project={project} scanNode={node} onDraft={props.onScanDraft} view={view} onReview={()=>onReview()} onConfigure={onConfigure}/>;
  return <div className="calculation-workspace">
    <div hidden={view!=='config'} className="configuration-body">
      <nav className="configuration-parts" aria-label="计算配置步骤">
        <button className={part==='physics'?'active':''} aria-pressed={part==='physics'} onClick={()=>setPart('physics')}>1 · 物理参数与继承</button>
        <button className={part==='files'?'active':''} aria-pressed={part==='files'} onClick={()=>setPart('files')}>2 · k 点、赝势与输入审查</button>
      </nav>
      <div hidden={part!=='physics'} className="configuration-section">
        <ParameterPanel sourcePanel={<SourcePanel project={project} nodeId={nodeId} onChange={value=>props.onSource(nodeId,value)}/>} project={project} initialNodeId={nodeId} embedded onLayer={props.onLayer} onTemplates={props.onTemplates} onNumWann={props.onNumWann} onProjections={props.onProjections}/>
        <div className="configuration-next"><p>继续设置 k 点和赝势，核对实际生成的输入文件。</p><button className="button primary" onClick={()=>setPart('files')}>继续：k 点与输入审查</button></div>
      </div>
      <div hidden={part!=='files'} className="configuration-section">
        <InputPanel project={project} initialNodeId={nodeId} embedded onDraft={props.onDraft} onSharedPotcar={props.onSharedPotcar} onReview={onReview}/>
      </div>
    </div>
    <div hidden={view!=='run'} className="review-body">
      <div className="review-guide"><button className="button" onClick={onConfigure}>返回计算配置</button></div>
      {(visitedRun||view==='run')&&<ExecutionPanel project={project} initialNodeId={nodeId} initialBundleId={props.bundleId} onResults={props.onResults}/>}
    </div>
  </div>;
}

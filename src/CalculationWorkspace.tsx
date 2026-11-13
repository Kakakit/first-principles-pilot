import IndependentScanWorkspace from './IndependentScanWorkspace';
import type {WindowDraft,WindowAdoption} from './windowScan';
import SourcePanel from './SourcePanel';
import type {SourceDraft} from './SourcePanel';
import {useEffect} from 'react';
import {useSubmissionDraft} from './useSubmissionDraft';
import type {Project} from './model';
import type {ParameterLayer} from './parameters';
import ParameterPanel from './ParameterPanel';
import InputPanel from './InputPanel';
import ExecutionPanel from './ExecutionPanel';
import WannierPanel from './WannierPanel';
import ScanPanel from './ScanPanel';
import type {ContinuationCreated} from './scanContinuation';

type Part='physics'|'source'|'files';
type Props={
  onContinuationCreated?:(result:ContinuationCreated)=>void;
  onProject:(project:Project)=>void;project:Project;nodeId:string;view:'config'|'run';bundleId?:string;configPart?:Part;onConfigPart?:(part:Part)=>void;
  onLayer:(nodeId:string|null,layer:ParameterLayer)=>void;
  onTemplates:(templates:NonNullable<NonNullable<Project['parameters']>['templates']>)=>void;
  onSource:(nodeId:string,value:SourceDraft)=>void;
  onDraft:NonNullable<Parameters<typeof InputPanel>[0]['onDraft']>;
  onSharedPotcar:NonNullable<Parameters<typeof InputPanel>[0]['onSharedPotcar']>;
  onNumWann:(nodeId:string,value:string)=>void;
  onProjections:(nodeId:string,value:string)=>void;
  onWannierDraft:(nodeId:string,key:string,value:import('./wannierDraft').WannierDraft)=>void;
  onWindowChange:(value:WindowDraft)=>void;onWindowAdopt:(value:WindowAdoption)=>void;
  onScanDraft:NonNullable<Parameters<typeof ScanPanel>[0]['onDraft']>;
  onReview:(bundleId?:string)=>void;onConfigure:()=>void;initialAttemptId?:string;onAttemptChange?:(id:string,reason:'user'|'default'|'replace')=>void;onResults:(nodeId:string,attemptId:string)=>void;
};

export default function CalculationWorkspace(props:Props) {
  const {project,nodeId,view,onReview,onConfigure}=props;
  const node=project.nodes.find(item=>item.id===nodeId)!;
  useEffect(()=>{if(node.data.executionMode==='vasp'&&!['structure','scan','wannier90'].includes(node.data.kind)&&!node.data.sourceDraft){const parent=project.nodes.find(n=>n.id===project.edges.find(e=>e.target===nodeId)?.source);props.onSource(nodeId,{mode:parent&&parent.data.kind!=='structure'?'upstream':'new',purpose:'charge',files:'charge'});}},[nodeId]);
  const [localPart,setLocalPart]=useSubmissionDraft<Part>('config:'+project.id+':'+nodeId+':part','physics');
  const part=props.configPart||localPart;
  const setPart=props.onConfigPart||setLocalPart;
  if(node.data.continuationBatchId)return <ScanPanel project={project} scanNode={node} view={view} onDraft={props.onScanDraft} onReview={()=>onReview()} onConfigure={onConfigure} onContinuationCreated={props.onContinuationCreated}/>;
  if(node.data.kind==='wannier90')return <WannierPanel project={project} initialNodeId={nodeId} embedded onDraft={props.onWannierDraft} view={view} onReview={()=>onReview()} onConfigure={onConfigure}/>;
  if(node.data.kind==='scan')return <IndependentScanWorkspace {...props}/>;
  return <div className="calculation-workspace">
    {view==='config'&&<div className="configuration-body">
      <nav className="configuration-parts" aria-label="计算配置步骤">
        <button className={part==='physics'?'active':''} aria-pressed={part==='physics'} onClick={()=>setPart('physics')}>1 · 物理参数与继承</button>
        <button className={part==='source'?'active':''} aria-pressed={part==='source'} onClick={()=>setPart('source')}>2 · 计算来源与重启</button>
        <button className={part==='files'?'active':''} aria-pressed={part==='files'} onClick={()=>setPart('files')}>3 · k 点、赝势与输入审查</button>
      </nav>
      {part==='physics'&&<div className="configuration-section">
        <ParameterPanel project={project} initialNodeId={nodeId} embedded onLayer={props.onLayer} onTemplates={props.onTemplates} onNumWann={props.onNumWann} onProjections={props.onProjections}/>
        <div className="configuration-next"><button className="button" onClick={()=>setPart('source')}>计算来源与重启</button><button className="button primary" onClick={()=>setPart('files')}>继续：k 点与输入审查</button></div>
      </div>}
      {part==='source'&&<SourcePanel advanced project={project} nodeId={nodeId} onChange={value=>props.onSource(nodeId,value)}/>}
      {part==='files'&&<div className="configuration-section">
        <InputPanel project={project} initialNodeId={nodeId} embedded onDraft={props.onDraft} onSharedPotcar={props.onSharedPotcar} onReview={onReview}/>
      </div>}
    </div>}
    {view==='run'&&<div className="review-body">
      <div className="review-guide"><button className="button" onClick={onConfigure}>返回计算配置</button></div>
      <ExecutionPanel project={project} initialNodeId={nodeId} initialBundleId={props.bundleId} initialAttemptId={props.initialAttemptId} onAttemptChange={props.onAttemptChange} onResults={props.onResults}/>
    </div>}
  </div>;
}

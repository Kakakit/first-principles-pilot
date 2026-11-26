import ProgramPanel from './ProgramPanel';
import IndependentScanWorkspace from './IndependentScanWorkspace';
import type {WindowDraft,WindowAdoption} from './windowScan';
import SourcePanel from './SourcePanel';
import type {SourceDraft} from './SourcePanel';
import {useEffect} from 'react';
import ConfigurationPage from './ConfigurationPage';
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
  onProject:(project:Project)=>void;project:Project;nodeId:string;view:'config'|'run';bundleId?:string;configPart?:Part;
  onLayer:(nodeId:string|null,layer:ParameterLayer)=>void;
  onTemplates:(templates:NonNullable<NonNullable<Project['parameters']>['templates']>)=>void;
  onSource:(nodeId:string,value:SourceDraft)=>void;
  onDraft:NonNullable<Parameters<typeof InputPanel>[0]['onDraft']>;
  onSharedPotcar:NonNullable<Parameters<typeof InputPanel>[0]['onSharedPotcar']>;
  onNumWann:(nodeId:string,value:string)=>void;
  onProjections:(nodeId:string,value:string)=>void;
  onProgramDraft:Parameters<typeof ProgramPanel>[0]['onChange'];
  onWannierDraft:(nodeId:string,key:string,value:import('./wannierDraft').WannierDraft)=>void;
  onWindowChange:(value:WindowDraft)=>void;onWindowAdopt:(value:WindowAdoption)=>void;
  onScanDraft:NonNullable<Parameters<typeof ScanPanel>[0]['onDraft']>;
  onReview:(bundleId?:string)=>void;onConfigure:()=>void;initialAttemptId?:string;onAttemptChange?:(id:string,reason:'user'|'default'|'replace')=>void;onResults:(nodeId:string,attemptId:string,subpage?:string)=>void;
};

export default function CalculationWorkspace(props:Props) {
  const {project,nodeId,view,onReview,onConfigure}=props;
  const node=project.nodes.find(item=>item.id===nodeId)!;
  useEffect(()=>{if(node.data.executionMode==='vasp'&&!['structure','scan','wannier90'].includes(node.data.kind)&&!node.data.sourceDraft){const parent=project.nodes.find(n=>n.id===project.edges.find(e=>e.target===nodeId)?.source);props.onSource(nodeId,{mode:parent&&parent.data.kind!=='structure'?'upstream':'new',purpose:'charge',files:'charge'});}},[nodeId]);
  if(node.data.kind==='program')return <ProgramPanel key={node.id} project={project} node={node} view={view} initialId={props.initialAttemptId||props.bundleId} onSelect={id=>props.onAttemptChange?.(id,'user')} onResults={id=>props.onResults(nodeId,id)} onChange={props.onProgramDraft} onPrepared={props.onReview}/>;
  if(node.data.continuationBatchId)return <ScanPanel project={project} scanNode={node} view={view} onDraft={props.onScanDraft} onReview={()=>onReview()} onConfigure={onConfigure} onContinuationCreated={props.onContinuationCreated}/>;
  if(node.data.kind==='wannier90')return <WannierPanel project={project} initialNodeId={nodeId} embedded onDraft={props.onWannierDraft} view={view} onReview={()=>onReview()} onConfigure={onConfigure}/>;
  if(node.data.kind==='scan')return <IndependentScanWorkspace {...props}/>;
  return <div className="calculation-workspace">
    {view==='config'&&<ConfigurationPage files part={props.configPart}>{(footer,preview)=><>
      <ParameterPanel key={nodeId} previewHost={preview} onFileDraft={props.onDraft} continuous sourcePanel={<div data-action-field="input.source"><SourcePanel advanced project={project} nodeId={nodeId} onChange={value=>props.onSource(nodeId,value)}/></div>} project={project} initialNodeId={nodeId} embedded onLayer={props.onLayer} onTemplates={props.onTemplates} onNumWann={props.onNumWann} onProjections={props.onProjections}/>
      <InputPanel reviewFooter={footer} project={project} initialNodeId={nodeId} embedded onDraft={props.onDraft} onSharedPotcar={props.onSharedPotcar} onReview={onReview}/>
    </>}</ConfigurationPage>}
    {view==='run'&&<div className="review-body">
      <div className="review-guide"><button className="button" onClick={onConfigure}>返回计算配置</button></div>
      <ExecutionPanel project={project} initialNodeId={nodeId} initialBundleId={props.bundleId} initialAttemptId={props.initialAttemptId} onAttemptChange={props.onAttemptChange} onResults={props.onResults}/>
    </div>}
  </div>;
}

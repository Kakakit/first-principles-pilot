import type{ResultAttempt}from'./resultWorkspace';
import{programDone}from'./resultWorkspace';
import{statusLabel}from'./statusLabels';
import{commandMode}from'./executionUi';
export default function ExecutionEvidence({attempt,evidence=attempt.evidence||{}}:{attempt:ResultAttempt;evidence?:Record<string,unknown>}){
 const direct=commandMode(attempt.config)==='direct';
 const convergence=evidence[attempt.kind==='wannier90'?'localization_converged':'ediff_reached'];
 return <div className="workflow-evidence"><div><span>{direct?'执行状态':'调度状态'}</span><strong>{direct&&attempt.state==='COMPLETED'?'执行结束':statusLabel(attempt.state)}</strong></div><div><span>程序结束</span><strong>{programDone({...attempt,evidence})?'已见结束标志':'未验证'}</strong></div><div><span>数值收敛</span><strong>{convergence===true?'已收敛':convergence===false?'未收敛':'未验证'}</strong></div><div><span>物理审核</span><strong>{statusLabel(String(evidence.scientific_validation||'NOT_PERFORMED'))}</strong></div></div>;
}

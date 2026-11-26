import type {ResultAttempt} from './resultWorkspace';

type LogFile={path:string;size_bytes:number;previewable:boolean};

export function diagnosticLogs(attempt:ResultAttempt,stage?:string):string[]{
 const evidence=attempt.evidence||{},failed=attempt.state==='FAILED';
 if(!failed&&attempt.state!=='COMPLETED')return [];
 if(attempt.kind==='wannier90'){
   if(!failed&&evidence.localization_converged!==false)return [];
   const seed=typeof attempt.seed==='string'?attempt.seed:'wannier90';
   return [seed+'.wout',seed+'.werr','wannier90.err','pbs.err','vasp.slurm.err'];
 }
 if(failed)return ['vasp.err','vasp.slurm.err','pbs.err','OUTCAR','vasp.out','OSZICAR'];
 if(evidence.ediff_reached===false)return ['OSZICAR','OUTCAR','vasp.out'];
 if(stage==='relax'&&evidence.ionic_converged===false)return ['OUTCAR','OSZICAR','vasp.out'];
 return [];
}

export function pickDiagnosticLog(paths:string[],files:LogFile[]){
 return paths.find(path=>files.some(f=>f.path===path&&f.previewable&&f.size_bytes>0));
}

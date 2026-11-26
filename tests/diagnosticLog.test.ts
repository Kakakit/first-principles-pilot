import test from 'node:test';
import assert from 'node:assert/strict';
import {diagnosticLogs,pickDiagnosticLog} from '../src/diagnosticLog.ts';

test('log shortcuts depend on final state and recorded evidence',()=>{
 const run={id:'run',state:'COMPLETED'};
 assert.deepEqual(diagnosticLogs(run),[]);
 assert.deepEqual(diagnosticLogs({...run,state:'RUNNING',evidence:{ediff_reached:false}}),[]);
 assert.equal(diagnosticLogs({...run,evidence:{ediff_reached:false}})[0],'OSZICAR');
 assert.equal(diagnosticLogs({...run,evidence:{ionic_converged:false}},'relax')[0],'OUTCAR');
 assert.deepEqual(diagnosticLogs({...run,evidence:{ionic_converged:false}},'dos'),[]);
 assert.equal(diagnosticLogs({...run,kind:'wannier90',seed:'spin.2',evidence:{localization_converged:false}})[0],'spin.2.wout');
});

test('ignore empty stderr, oversized files and unrelated nested paths',()=>{
 const paths=diagnosticLogs({id:'run',state:'FAILED'});
 const files=[
  {path:'vasp.err',previewable:true,size_bytes:0},
  {path:'OUTCAR',previewable:false,size_bytes:25000000},
  {path:'backup/vasp.err',previewable:true,size_bytes:12},
  {path:'OSZICAR',previewable:true,size_bytes:120},
 ];
 assert.equal(pickDiagnosticLog(paths,files),'OSZICAR');
 assert.equal(pickDiagnosticLog(paths,files.slice(0,3)),undefined);
});

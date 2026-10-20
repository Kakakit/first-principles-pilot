import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
// @ts-ignore Package-owned JS is also embedded verbatim in the sandbox view.
import {calculate,csvReport} from '../resources/extensions/lcalcpol-polarization-analysis/viewer/model.js';
const base={basis:[[2,0,0],[1,3,0],[.5,.5,4]],raw_p:[2.9,-.9,9.6],coefficients:[1.2,-.7,2.4],automatic:[1,-1,2],volume:24,factor:16.021766,endpoints:[]};
test('manual selection matches Python for a nonorthogonal basis',()=>{
 const result=calculate(base,[1,-1,2]);
 const code='import json,numpy as np; b=np.array('+JSON.stringify(base.basis)+'); p=np.array('+JSON.stringify(base.raw_p)+'); print(json.dumps((p-np.array([1,-1,2])@b).tolist()))';
 const run=spawnSync('/Users/kafka/Documents/FeCrO论文/06_工具/.venv-figure/bin/python',['-c',code],{encoding:'utf8'});assert.equal(run.status,0,run.stderr);assert.deepEqual(result.polarization,JSON.parse(run.stdout));
 result.polarization.forEach((p:number,i:number)=>assert.ok(Math.abs(result.dipole[i]*base.factor/base.volume-p)<1e-12));
 assert.deepEqual(calculate(base,[0,0,0]).polarization,base.raw_p);
 result.polarization.forEach((p:number,j:number)=>assert.ok(Math.abs(result.coefficients.reduce((sum:number,c:number,i:number)=>sum+c*base.basis[i][j],0)-p)<1e-12));
});
test('boundary crossing is difference-first and diagnostics differ by a quantum',()=>{
 const result=calculate({...base,basis:[[1,0,0],[0,1,0],[0,0,1]],raw_p:[.02,0,0],coefficients:[.02,0,0],automatic:[0,0,0],diagnostic_coefficients:[-.98,0,0]},[0,0,0]);assert.equal(result.polarization[0],.02);assert.deepEqual(result.diagnostic_gap,[-1,0,0]);
});
test('invalid integers rejected and CSV includes selection and units',()=>{
 for(const n of [[.5,0,0],[NaN,0,0],[Infinity,0,0],[1000001,0,0],[0,0]])assert.throws(()=>calculate(base,n));
 const csv=csvReport(base,calculate(base,[-1,0,2]));assert.match(csv,/chosen/);assert.match(csv,/μC\/cm²/);assert.match(csv,/Q3/);
});

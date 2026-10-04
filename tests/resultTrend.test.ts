import {test} from 'node:test';
import assert from 'node:assert/strict';
import {trendSegments} from '../src/resultTrend.ts';

const row=(value:number|null,model='A',converged=true)=>({stage:'scf',model_sha256:model,potcar_sha256:'P',program_done:true,ediff_reached:converged,metrics:{energy:value===null?null:{value,unit:'eV/cell',source:{sha256:'source'}}}});

test('trend lines break at missing, unverified and different-model results',()=>{
  const rows=[row(-10),row(-9),row(null),row(-8),row(-7,'B'),row(-6,'B'),row(-5,'B',false),row(-4,'B')];
  assert.deepEqual(trendSegments(rows,'energy'),[[{index:0,value:-10},{index:1,value:-9}],[{index:4,value:-7},{index:5,value:-6}]]);
});

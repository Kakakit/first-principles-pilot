import {test} from 'node:test';
import assert from 'node:assert/strict';
import {trendSegments} from '../src/resultTrend.ts';

const row=(value:number|null,model='A',converged=true)=>({stage:'scf',model_sha256:model,potcar_sha256:'P',program_done:true,ediff_reached:converged,metrics:{energy:value===null?null:{value,unit:'eV/cell',source:{sha256:'source'}}}});

test('trend lines break at missing, unverified and different-model results',()=>{
  const rows=[row(-10),row(-9),row(null),row(-8),row(-7,'B'),row(-6,'B'),row(-5,'B',false),row(-4,'B')];
  assert.deepEqual(trendSegments(rows,'energy'),[[{index:0,value:-10},{index:1,value:-9}],[{index:4,value:-7},{index:5,value:-6}]]);
});

test('lattice trends allow parameter model changes but require cell composition and ionic convergence',()=>{
 const lattice=(value:number,model:string,composition='same-cell',ionic=true)=>({...row(-10,model),stage:'relax',cell_composition_sha256:composition,metrics:{energy:row(-10,model).metrics.energy,lattice_a:{value,unit:'Å',source:{sha256:'OUTCAR'}},ionic_converged:ionic}});
 const rows=[lattice(4,'U=1'),lattice(4.1,'U=2'),lattice(4.2,'U=3','same-cell',false),lattice(4.3,'U=4'),lattice(4.4,'U=5','supercell'),lattice(4.5,'U=6','supercell')];
 assert.deepEqual(trendSegments(rows,'lattice_a'),[[{index:0,value:4},{index:1,value:4.1}],[{index:4,value:4.4},{index:5,value:4.5}]]);
 assert.deepEqual(trendSegments(rows,'energy'),[]);
 assert.deepEqual(trendSegments([lattice(4,'A',''),lattice(4.1,'B','')],'lattice_a'),[]);
});

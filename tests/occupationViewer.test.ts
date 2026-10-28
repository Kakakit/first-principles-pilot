import test from 'node:test';import assert from 'node:assert/strict';
// @ts-ignore Package-owned functions are embedded in the independent viewer.
import {occupationSelection,occupationCSV} from '../resources/extensions/local-crystal-field-analysis/viewer/occupation-model.mjs';
const raw={diagonal:[.6,.6],natural_occupations:[.9,.3],trace:1.2,matrix:{real:[[.6,.3],[.3,.6]],imag:[[0,0],[0,0]]},weights:[[.5,.5],[.5,.5]]};
const atom={atom:5,element:'Fe',polyhedron:'FeO4',orbital_labels:['dz2','dx2-y2'],spin_moment_d:-3,spins:[{id:'up',label:'↑',global:raw,local:{...raw,diagonal:[.9,.3]},groups:{e_label:'e',t_label:'t2',e_occupation:1.2,t_occupation:0}}]};
test('occupation controls select frame and natural occupations without energy scaling',()=>{
 assert.deepEqual(occupationSelection(atom).values,[.9,.3]);assert.deepEqual(occupationSelection(atom,'up','global').values,[.6,.6]);assert.equal(occupationSelection(atom,'up','local','natural').unit,'electron');assert.deepEqual(occupationSelection(atom,'up','local','natural').labels,['自然轨道 1','自然轨道 2']);assert.throws(()=>occupationSelection(atom,'down'));
});
test('CSV exports selected occupations, frame, units and group definitions',()=>{
 const csv=occupationCSV('测试',atom,occupationSelection(atom));assert.match(csv,/electron/);assert.match(csv,/FeO4/);assert.match(csv,/approximate_group/);assert.doesNotMatch(csv,/eV|meV/);
});

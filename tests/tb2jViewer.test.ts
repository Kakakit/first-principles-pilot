import {test} from 'node:test';
import assert from 'node:assert/strict';
import {bonds,selectBonds,selectJ,orderRows,validateMapping,mappingMap,orbitalRows,sumOrbitals,csv,shells,validateFilters} from '../resources/extensions/tb2j/viewer/model.js';
const atoms=[{index:1,symbol:'Cr',orbitals:2},{index:5,symbol:'Fe',orbitals:2}];
const rows=bonds([[0,1,5,1,0,0,3,-2,null,null],[1,5,1,-1,0,0,3,4,null,null],[2,1,1,0,1,0,1,-5,null,null]],atoms);
const filters={pairs:['Cr–Fe'],atomI:'',atomJ:'',rMin:'',rMax:'',jMin:'',jMax:''};
test('element pair selection retains native reverse bonds and duplicate distances',()=>{const result=selectBonds(rows,filters);assert.equal(result.length,2);assert.deepEqual(result.map(r=>r.R),[[1,0,0],[-1,0,0]]);assert.deepEqual(result.map(r=>r.i),[1,5]);});
test('numeric sorting covers the full selection with signed J and stable ties',()=>{assert.deepEqual(orderRows(rows,'r').map(r=>r.id),[2,0,1]);assert.deepEqual(orderRows(rows,'J','desc').map(r=>r.J),[4,-2,-5]);assert.equal(selectJ(rows,{jMin:'-3',jMax:'0'}).length,1);const many=Array.from({length:1200},(_,i)=>({...rows[0],id:i,r:1200-i}));assert.equal(orderRows(many,'r')[0].id,1199);});
test('confirmed orbital mapping validates bounds and preserves original numbering',()=>{const mapping=validateMapping([{atom_index:1,orbital_index:1,label:'dxy',group:'confirmed'}],atoms);assert.throws(()=>validateMapping([...mapping,...mapping],atoms));assert.throws(()=>validateMapping([{atom_index:1,orbital_index:3,label:'x'}],atoms));const result=orbitalRows([[0,1,2,0.12345678901234567]],rows,[1],[2],mappingMap(mapping));assert.equal(result[0].li,'dxy');assert.equal(result[0].lj,'');assert.equal(result[0].oj,2);assert.match(csv(result,true),/0.12345678901234566/);});
test('orbital sums include only selected entries and remain distinct from total J',()=>{const entries=orbitalRows([[0,1,1,2],[0,2,2,3]],rows,[1,2],[1,2],new Map());const summed=sumOrbitals(entries);assert.equal(summed[0].J,5);assert.equal(summed[0].totalJ,-2);assert.equal(summed[0].count,2);});

test('strict maximum distance and lattice translation keep native reverse records',()=>{
 const edge=[...rows,{...rows[0],id:3,r:9},{...rows[0],id:4,r:8.999999}];
 assert.deepEqual(selectBonds(edge,{...filters,rMax:'9',Rx:'1'}).map(r=>r.id),[0,4]);
 assert.equal(selectBonds(edge,{...filters,rMax:'9',Rx:'0',Ry:'0',Rz:'0'}).length,0);
 assert.deepEqual(orderRows(rows,'absJ','desc').map(r=>r.J),[-5,4,-2]);
});

test('distance shells use a bounded span, signed ranges and raw record counts',()=>{
 const data=[{...rows[0],r:3,J:-2},{...rows[1],r:3.0008,J:4},{...rows[0],id:3,r:3.0016,J:8}];
 const result=shells(data,.001);assert.equal(result.length,2);assert.equal(result[0].count,2);assert.equal(result[0].medianJ,1);assert.equal(result[0].minJ,-2);assert.equal(result[0].maxJ,4);
 assert.throws(()=>validateFilters({...filters,rMin:'9',rMax:'6'}));assert.throws(()=>validateFilters({...filters,jMin:'3',jMax:'-1'}));
});

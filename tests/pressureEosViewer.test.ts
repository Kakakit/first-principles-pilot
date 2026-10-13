import {test} from 'node:test';
import assert from 'node:assert/strict';
import {PRESETS,defaults,validate,value,unit,series,fitMatches,csvReport} from '../resources/extensions/pressure-structure-eos/viewer/model.js';
const fields={volume_a3:{label:'体积',unit:'Å³',kind:'volume'},a_ang:{label:'a',unit:'Å',kind:'length'},b_ang:{label:'b',unit:'Å',kind:'length'},c_ang:{label:'c',unit:'Å',kind:'length'},energy_sigma0_ev:{label:'E',unit:'eV',kind:'energy'},external_pressure_gpa:{label:'P',unit:'GPa',kind:'pressure'}};
const point=(id:string,v:number)=>({id,label:'Ce '+id,job_id:'22808',step:1,group:'Ce',n_atoms:4,formula_units:4,fit_blockers:[],values:{volume_a3:v,a_ang:3,b_ang:4,c_ang:5,energy_sigma0_ev:-20,external_pressure_gpa:2}});
const data={identity:'frozen',options:{energy:'energy_sigma0_ev',model:'birch_murnaghan'},fields,points:[point('a',48),point('b',40)],fit_selection:{energy:'energy_sigma0_ev',model:'birch_murnaghan'}};
test('a/b/c–V preset and sorting preserve source sequence',()=>{const state=defaults(data);assert.deepEqual(PRESETS['a/b/c–V'].ys,['a_ang','b_ang','c_ang']);assert.equal(series(data,state).length,3);state.mode='line';assert.deepEqual(series(data,state)[0].points.map(p=>p.value[0]),[40,48]);assert.equal(data.points[0].id,'a');});
test('energy and volume share normalization; pressure does not',()=>{const state={...defaults(data),normalization:'atom'};assert.equal(value(data.points[0],'volume_a3',state,fields),12);assert.equal(value(data.points[0],'energy_sigma0_ev',state,fields),-5);state.pressureUnit='kbar';assert.equal(value(data.points[0],'external_pressure_gpa',state,fields),20);assert.equal(unit('volume_a3',state,fields),'Å³/atom');});
test('reject mixed Y units and bad axis range',()=>{const state=defaults(data);assert.throws(()=>validate({...state,ys:['a_ang','energy_sigma0_ev']},data),/相同单位/);assert.throws(()=>validate({...state,xmin:'50',xmax:'40'},data),/下限/);assert.throws(()=>validate({...state,xmin:'NaN'},data),/有限/);});
test('excluded and split groups invalidate previous fit; export raw values stays intact',()=>{const state=defaults(data);assert.equal(fitMatches(state,data),true);state.excluded=['a'];assert.equal(fitMatches(state,data),false);assert.ok(csvReport(data,state).includes('user_excluded'));state.excluded=[];state.groups.a='other phase';assert.equal(fitMatches(state,data),false);assert.equal(data.points[0].values.volume_a3,48);});

test('multi-select marks chosen values and disables incompatible or absent quantities',async()=>{
 const {yOptions}=await import('../resources/extensions/pressure-structure-eos/viewer/model.js');
 const state=defaults(data),options=yOptions(data,state,['a_ang','b_ang']);
 assert.equal(options.find(o=>o.key==='a_ang')?.selected,true);
 assert.equal(options.find(o=>o.key==='c_ang')?.reason,'');
 assert.match(options.find(o=>o.key==='energy_sigma0_ev')?.reason||'',/不兼容/);
 assert.equal(yOptions(data,state,[]).find(o=>o.key==='energy_sigma0_ev')?.reason,'');
 const missing={...data,fields:{...fields,missing:{label:'缺失',unit:'Å',kind:'length'}}};
 assert.match(yOptions(missing,state,[]).find(o=>o.key==='missing')?.reason||'',/未提供/);
});
test('batch grouping affects only selected sources and preserves blockers and raw data',async()=>{
 const {batchGroup}=await import('../resources/extensions/pressure-structure-eos/viewer/model.js');
 const state=defaults(data),before=JSON.stringify(data);
 batchGroup(state,data,['a','b'],'  新组  ');assert.deepEqual(state.groups,{a:'新组',b:'新组'});
 assert.equal(fitMatches(state,data),false);assert.equal(JSON.stringify(data),before);
 assert.throws(()=>batchGroup(state,data,[],'新组'),/勾选/);
 assert.throws(()=>batchGroup(state,data,['a'],'  '),/组名/);
 assert.throws(()=>batchGroup(state,data,['unknown'],'新组'),/无效/);
});

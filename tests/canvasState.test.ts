import {test} from 'node:test';
import assert from 'node:assert/strict';
import {adoptUserNodes,calculateNodePosition} from '@xyflow/system';
import {measuredCanvasNodes,updateCanvasMeasurements} from '../src/canvasState.ts';
import {newProject,makeStep} from '../src/model.ts';

test('measured controlled nodes remain initialized through selection, group movement and undo',()=>{
  const project=newProject('画布回归');
  project.nodes=[makeStep('relax',{x:10,y:20}),makeStep('scf',{x:320,y:20})];
  const original=structuredClone(project);
  let measurements=updateCanvasMeasurements({projectId:project.id,dimensions:new Map()},project.id,project.nodes,
    project.nodes.map(node=>({id:node.id,type:'dimensions',dimensions:{width:240,height:164}})));
  const lookup=new Map(),parents=new Map();
  for(const offset of [0,20,40,0]){
    const projected=project.nodes.map(node=>({...node,position:{x:node.position.x+offset,y:node.position.y+offset},selected:true,data:{...node.data,openResults:()=>{}}}));
    const result=adoptUserNodes(measuredCanvasNodes(projected,project.id,measurements),lookup,parents);
    assert.equal(result.nodesInitialized,true);
    const errors:string[]=[];
    for(const node of projected){
      const next=calculateNodePosition({nodeId:node.id,nextPosition:node.position,nodeLookup:lookup,onError:code=>errors.push(code)});
      assert.deepEqual(next.position,node.position);
    }
    assert.deepEqual(errors,[]);
    measurements=updateCanvasMeasurements(measurements,project.id,project.nodes,[]);
  }
  assert.deepEqual(project,original);
  assert.doesNotThrow(()=>structuredClone(project));
});

test('measurement feedback accepts resized scan cards and drops removed children and other project dimensions',()=>{
  const node=makeStep('scan');
  const child={...makeStep('scf'),parentId:node.id};
  let state=updateCanvasMeasurements({projectId:'a',dimensions:new Map()},'a',[node,child],
    [{id:node.id,type:'dimensions',dimensions:{width:280,height:350}},{id:child.id,type:'dimensions',dimensions:{width:200,height:36}}]);
  state=updateCanvasMeasurements(state,'a',[node],[{id:node.id,type:'dimensions',dimensions:{width:240,height:164}}]);
  assert.equal(state.dimensions.has(child.id),false);
  assert.deepEqual(measuredCanvasNodes([node],'a',state)[0].measured,{width:240,height:164});
  assert.equal(measuredCanvasNodes([node],'b',state)[0].measured,undefined);
  state=updateCanvasMeasurements(state,'b',[node],[{id:node.id,type:'dimensions',dimensions:{width:240,height:180}}]);
  assert.deepEqual(measuredCanvasNodes([node],'b',state)[0].measured,{width:240,height:180});
});

test('unchanged or invalid dimensions do not loop renders or corrupt the cached bounds',()=>{
  const node=makeStep('scf');
  const changes=[{id:node.id,type:'dimensions' as const,dimensions:{width:240,height:164}}];
  const state=updateCanvasMeasurements({projectId:'a',dimensions:new Map()},'a',[node],changes);
  assert.equal(updateCanvasMeasurements(state,'a',[node],changes),state);
  for(const width of [NaN,Infinity,0,-1])assert.equal(updateCanvasMeasurements(state,'a',[node],[{...changes[0],dimensions:{width,height:164}}]),state);
});

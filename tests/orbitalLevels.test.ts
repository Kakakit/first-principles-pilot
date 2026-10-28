import test from 'node:test';
import assert from 'node:assert/strict';
import {moment,groupMean,hLevels,relativeLevels} from '../resources/extensions/local-crystal-field-analysis/viewer/math.mjs';
test('linear PDOS moments include exact first moment and clipped endpoints',()=>{
 for(const [a,b] of [[0,2],[.25,1.75]]){const r=moment([0,1,2],[0,1,2],a,b);assert.ok(Math.abs(r.weight-(b*b-a*a)/2)<1e-12);assert.ok(Math.abs(r.centroid_ev-2*(b**3-a**3)/(3*(b*b-a*a)))<1e-12);}
});
test('invalid intervals, zero weights and negative spectra are explicit',()=>{
 assert.equal(moment([0,1],[0,0],0,1).centroid_ev,null);assert.equal(moment([0,1],[-1,-1],0,1).centroid_ev,null);assert.equal(moment([0,1],[-1,3],0,1).negative_values,true);
 assert.throws(()=>moment([0,1],[1,1],1,0));assert.throws(()=>moment([0,1],[1,1],-.1,1));assert.throws(()=>moment([1,0],[1,1],0,1));
});
test('diagonals and eigenlevels are different, groups use arithmetic means',()=>{
 const h={matrix:{real:[[0,1],[1,0]]},eigenvalues_ev:[-1,1]};assert.deepEqual(hLevels(h,'diagonal'),[0,0]);assert.deepEqual(hLevels(h,'eigen'),[-1,1]);assert.equal(groupMean([1,2,6],[0,2]),3.5);assert.equal(groupMean([1,null],[0,1]),null);assert.deepEqual(relativeLevels([1,2,6],'centroid'),[-2,-1,3]);
});

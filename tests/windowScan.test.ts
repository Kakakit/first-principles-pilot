import {test} from 'node:test';
import assert from 'node:assert/strict';
import {windowDraftReady,emptyWindowDraft,windowRequest,windowOrder,evidenceLabel} from '../src/windowScan';
import {canConnect,makeStep,issues} from '../src/model';
import {latestByNode} from '../src/resultWorkspace';
test('window cases belong to the scan execution identity, leaving the base status intact',()=>{const rows=latestByNode([{id:'case',node_id:'w',execution_scope:'scan',state:'COMPLETED',created_at:'2026-10-02'},{id:'base',node_id:'w',state:'PREPARED',created_at:'2026-10-01'}]);assert.equal(rows.w.id,'base');assert.equal(rows.scan.id,'case');});
test('legacy Wannier template connection remains compatible',()=>{const w=makeStep('wannier90',{x:0,y:0},1),s=makeStep('scan',{x:1,y:1},2);delete s.data.scanSpec;assert.equal(canConnect([w,s],[],w.id,s.id),true);assert.equal(canConnect([w,s],[],s.id,w.id),false);});
test('no guessed initial step and range validation',()=>{const d=emptyWindowDraft();assert.equal(windowDraftReady(d),false);d.frozen_id='a';d.target_min='-1';d.target_max='1';d.ranges.dis_win_max={lower:'1',upper:'5',step:'0.5'};assert.equal(windowDraftReady(d),true);assert.equal(windowRequest(d).step,.5);d.ranges.dis_win_max.step='';assert.equal(windowDraftReady(d),false);});
test('outer, frozen and recheck order; evidence remains distinct',()=>{assert.deepEqual(windowOrder.map(x=>x[0]),['outer','outer','frozen','frozen','recheck','recheck']);assert.equal(evidenceLabel('criteria_disabled'),'未启用判据');assert.notEqual(evidenceLabel('not_satisfied'),evidenceLabel('confirmed'));});

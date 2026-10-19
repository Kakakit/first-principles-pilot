import {test} from 'node:test';
import assert from 'node:assert/strict';
import {draftWindows,candidateWindows,draftProjections} from '../src/pyw90Advice';

test('adoption copies only energy boundaries, never native counts or model fields',()=>{
  assert.deepEqual(candidateWindows({dis_win_min:-6,N:108,Nmost:102,percent:.8,check:{valid:true}} as never),{dis_win_min:-6});
});
test('manual preview follows unapplied form values and explicit blank deletes old tags',()=>{
  const win='dis_win_min=-2\ndis_win_max=10\ndis_froz_max=3D0 # absolute\n';
  assert.deepEqual(draftWindows({dis_win_min:'-8',dis_win_max:''},win,5),{dis_win_min:-8,dis_froz_max:-2});
  assert.deepEqual(draftWindows({dis_froz_max:''},win,5),{dis_win_min:-7,dis_win_max:5});
  assert.throws(()=>draftWindows({dis_win_min:'oops'},win,5),/有限/);
});
test('PDOS diagnostic forwards the current projection block without inventing auto orbitals',()=>{
 assert.deepEqual(draftProjections('num_wann=54\nbegin projections\nFe:d ! model\nCr:d\nO:p\nend projections\n'),['Fe:d','Cr:d','O:p']);
 assert.deepEqual(draftProjections('auto_projections=true\n'),[]);
 assert.deepEqual(draftProjections('begin projections\nc=0,0,0:sp3\nend projections'),['c=0,0,0:sp3']);
});

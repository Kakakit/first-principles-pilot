import {test} from 'node:test';
import assert from 'node:assert/strict';
import {draftWindows,candidateWindows} from '../src/pyw90Advice';

test('adoption copies only energy boundaries, never native counts or model fields',()=>{
  assert.deepEqual(candidateWindows({dis_win_min:-6,N:108,Nmost:102,percent:.8,check:{valid:true}} as never),{dis_win_min:-6});
});
test('manual preview follows unapplied form values and blank fields retain .win defaults',()=>{
  const win='dis_win_min=-2\ndis_win_max=10\ndis_froz_max=3D0 # absolute\n';
  assert.deepEqual(draftWindows({dis_win_min:'-8',dis_win_max:''},win,5),{dis_win_min:-8,dis_win_max:5,dis_froz_max:-2});
  assert.throws(()=>draftWindows({dis_win_min:'oops'},win,5),/有限/);
});

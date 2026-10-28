import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from pymatgen.io.vasp import Poscar
from backend.structure_edit import edit_structure, geometry_hash
from backend.storage import WorkspaceStore, ConflictError
FIXTURE = Path(__file__).parent / 'fixtures' / 'Si.structure.json'
def structure(): return json.loads(FIXTURE.read_text())
def atoms(s, mode): return [dict(id=a['id'], element=a['element'], coordinates=a[mode], selective=a['selective']) for a in s['atoms']]
def workspace():
 return {'activeId':'p','projects':[{'id':'p','name':'test','remoteSlug':'test','updatedAt':'today','nodes':[{'id':'s','type':'step','position':{'x':0,'y':0},'data':{'kind':'structure','label':'structure','note':'','params':{},'structure':structure(),'executionMode':'vasp','stageSequence':1,'stageSlug':'structure'}}],'edges':[]}]}
class GeometryEditTests(unittest.TestCase):
 def test_cell_change_has_explicit_coordinate_semantics(self):
  s=structure(); original=copy.deepcopy(s); matrix=(np.array(s['lattice'])*2).tolist()
  f=edit_structure(s,matrix,atoms(s,'fractional'),'fractional');c=edit_structure(s,matrix,atoms(s,'cartesian'),'cartesian')
  np.testing.assert_allclose(f['atoms'][1]['cartesian'],np.array(s['atoms'][1]['cartesian'])*2)
  np.testing.assert_allclose(c['atoms'][1]['cartesian'],s['atoms'][1]['cartesian'])
  np.testing.assert_allclose(c['atoms'][1]['fractional'],np.array(s['atoms'][1]['fractional'])/2)
  self.assertEqual(s,original);self.assertEqual(f['source'],s['source'])
  self.assertEqual([a['id'] for a in f['atoms']],[a['id'] for a in s['atoms']])
  self.assertEqual(f['edit']['parent_geometry_sha256'],geometry_hash(s))
 def test_coordinate_and_constraints_roundtrip(self):
  s=structure();a=atoms(s,'fractional');a[1]['coordinates']=[1.1,-.2,.35];a[0]['selective']=[False,False,True]
  result=edit_structure(s,s['lattice'],a,'fractional');p=Poscar.from_str(result['poscar'])
  np.testing.assert_allclose(p.structure.frac_coords[1],[1.1,-.2,.35]);self.assertEqual(p.selective_dynamics[0].tolist(),[False,False,True])
  following=edit_structure(result,result['lattice'],atoms(result,'fractional'),'fractional')
  self.assertEqual(following['edit']['parent_edit_id'],result['edit']['id'])
 def test_reject_degenerate_nonfinite_or_reordered_geometry(self):
  s=structure()
  for matrix in [[[0,0,0]]*3,[[float('nan'),0,0],[0,1,0],[0,0,1]],[[-1,0,0],[0,1,0],[0,0,1]]]:
   with self.assertRaises(ValueError):edit_structure(s,matrix,atoms(s,'fractional'),'fractional')
  with self.assertRaises(ValueError):edit_structure(s,s['lattice'],atoms(s,'fractional')[::-1],'fractional')
class DiskStoreTests(unittest.TestCase):
 def setUp(self): self.temp=tempfile.TemporaryDirectory();self.store=WorkspaceStore(self.temp.name)
 def tearDown(self): self.temp.cleanup()
 def test_migration_reopen_originals_history_restore(self):
  w=workspace();original=copy.deepcopy(w);first=self.store.save(w,0,'migration');self.assertEqual(first['revision'],1);self.assertEqual(w,original)
  self.assertEqual(self.store.save(w,0,'migration')['revision'],1)
  w['projects'][0]['name']='edited';second=self.store.save(w,1);reopened=WorkspaceStore(self.temp.name)
  self.assertEqual(reopened.read()['workspace'],w);self.assertEqual(reopened.read(1)['workspace'],original)
  self.assertEqual(len(list((Path(self.temp.name)/'originals').iterdir())),1)
  self.assertTrue(all('base64' not in p.read_text() for p in (Path(self.temp.name)/'snapshots').iterdir()))
  self.assertEqual(len(reopened.history()),1)  # An autosaved draft is not a recovery checkpoint.
  reopened.checkpoint(second['revision'],'edited')
  self.assertEqual(len(reopened.history()),2)
  with sqlite3.connect(str(Path(self.temp.name)/'index.sqlite3')) as db:self.assertEqual(db.execute('SELECT name,revision FROM projects').fetchone(),('edited',second['revision']))
 def test_conflict_invalid_original_and_failed_write_keep_prior(self):
  w=workspace();self.store.save(w,0);changed=copy.deepcopy(w);changed['projects'][0]['name']='next'
  with self.assertRaises(ConflictError):self.store.save(changed,0)
  bad=copy.deepcopy(w);bad['projects'][0]['nodes'][0]['data']['structure']['source']['base64']='YQ=='
  with self.assertRaises(ValueError):self.store.save(bad,1)
  with patch.object(self.store,'put_file',side_effect=OSError('disk full')):
   with self.assertRaises(OSError):self.store.save(changed,1)
  self.assertEqual(self.store.read()['workspace'],w);self.assertEqual(self.store.read()['revision'],1)
 def test_backup_above_old_two_mib_limit_and_roundtrip(self):
  w=workspace();w['extension']='x'*(3*1024*1024);self.store.save(w,0);self.assertEqual(self.store.read()['workspace'],w)
if __name__=='__main__':unittest.main()

import copy
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from backend.node_editability import status, assert_changes
from backend.storage import WorkspaceStore, validate_workspace


def project():
    return {'id':'p','name':'test','remoteSlug':'test','nodes':[
        {'id':n,'data':{'kind':kind,'executionMode':'vasp','stageSequence':seq,'stageSlug':kind,'params':{}}}
        for n,kind,seq in [('structure','structure',1),('relax','relax',2),('scf','scf',9),('unused','scf',10)]],
        'edges':[], 'updatedAt':'now'}


class NodeEditabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.p=project()
    def tearDown(self):self.temp.cleanup()
    def freeze(self,manifest,owner=None):
        (self.root/'inputs').mkdir(exist_ok=True)
        with zipfile.ZipFile(self.root/'inputs/a.zip','w') as z:
            z.writestr('project.json',json.dumps(owner or self.p));z.writestr('manifest.json',json.dumps(manifest))
    def record(self,table,body):
        with sqlite3.connect(self.root/'execution.sqlite3') as d:
            d.execute('CREATE TABLE IF NOT EXISTS '+table+'(body TEXT)');d.execute('INSERT INTO '+table+' VALUES(?)',(json.dumps(body),))
    def test_empty_checks_are_read_only_and_legacy_policy_round_trips(self):
        before=set(self.root.iterdir());r=status(self.root,self.p);self.assertEqual(set(self.root.iterdir()),before)
        self.assertTrue(r['nodes']['scf']['can_switch']);self.assertFalse(r['nodes']['structure']['can_switch'])
        store=WorkspaceStore(self.root);w={'activeId':'p','projects':[self.p]};store.save(w,0)
        self.assertEqual(store.read()['workspace'],w)
    def test_recycled_freeze_locks_source_and_owner_but_not_whole_graph(self):
        self.freeze({'node_id':'scf','dependencies':[{'node_id':'relax'}]})
        (self.root/'recycle/inputs/a').mkdir(parents=True)
        r=status(self.root,self.p)['nodes'];self.assertFalse(r['scf']['can_switch']);self.assertFalse(r['relax']['can_switch']);self.assertTrue(r['unused']['can_switch'])
    def test_prepared_unknown_chain_scan_and_wannier_refs_are_included(self):
        self.record('attempts',{'id':'a','project_id':'p','node_id':'relax','state':'UNKNOWN'})
        self.record('chain_runs',{'project_id':'p','node_ids':['scf'],'project':self.p})
        self.record('scan_batches',{'project_id':'p','base_node_id':'unused','scan_node_id':'scan'})
        (self.root/'wannier-bundles').mkdir();(self.root/'wannier-bundles/w.json').write_text(json.dumps({'project_id':'p','node_id':'wannier','parent_attempt_id':'a','project':self.p}))
        r=status(self.root,self.p)['nodes'];self.assertTrue(all(not r[n]['can_switch'] for n in ('relax','scf','unused')))
        self.assertTrue(any('Wannier' in reason for reason in r['relax']['reasons']))
    def test_save_rechecks_usage_and_rejects_policy_migration(self):
        store=WorkspaceStore(self.root);w={'activeId':'p','projects':[self.p]};store.save(w,0)
        incoming=copy.deepcopy(w);incoming['projects'][0]['nodes'][2]['data'].update(kind='dos',stageSlug='dos')
        self.freeze({'node_id':'scf'})
        with self.assertRaisesRegex(ValueError,'复制'):store.save(incoming,1)
        self.assertEqual(store.read()['workspace'],w)
        incoming=copy.deepcopy(w);incoming['projects'][0]['stagePolicy']='kind-fixed-v1'
        with self.assertRaisesRegex(ValueError,'规则不能更改'):assert_changes(self.root,w,incoming)
    def test_new_copy_can_switch_but_old_sequence_cannot_change(self):
        before={'projects':[self.p]};after=copy.deepcopy(before);after['projects'][0]['nodes'][2]['data'].update(kind='dos',stageSlug='dos')
        assert_changes(self.root,before,after)
        after['projects'][0]['nodes'][2]['data']['stageSequence']=5
        with self.assertRaisesRegex(ValueError,'原阶段编号'):assert_changes(self.root,before,after)
        self.freeze({'node_id':'scf'});after['projects'][0]['nodes'][2]['id']='new-copy';assert_changes(self.root,before,after)
    def test_bad_or_foreign_contract_and_fixed_policy_validation(self):
        foreign=copy.deepcopy(self.p);foreign['id']='other';self.freeze({'node_id':'scf'},foreign)
        self.assertTrue(status(self.root,self.p)['nodes']['scf']['can_switch'])
        (self.root/'inputs/a.zip').write_bytes(b'incomplete')
        result=status(self.root,self.p)
        self.assertTrue(result['nodes']['scf']['can_switch'])
        self.assertIn('a.zip',result['warnings'][0])
        w={'activeId':'p','projects':[self.p]};self.p['stagePolicy']='kind-fixed-v1'
        with self.assertRaisesRegex(ValueError,'编号'):validate_workspace(w)

    def test_deleted_historical_identity_cannot_return_as_another_type(self):
        self.freeze({'node_id':'scf','stage':'scf'})
        previous={'projects':[copy.deepcopy(self.p)]}
        previous['projects'][0]['nodes']=[n for n in previous['projects'][0]['nodes'] if n['id']!='scf']
        incoming={'projects':[copy.deepcopy(self.p)]}
        assert_changes(self.root,previous,incoming)  # restoring the original is valid
        incoming['projects'][0]['nodes'][2]['data'].update(kind='dos',stageSlug='dos')
        with self.assertRaisesRegex(ValueError,'历史合同'):assert_changes(self.root,previous,incoming)

import base64
import copy
import json
import unittest
from unittest.mock import patch
import test_scans
from backend import execution as ex, artifacts, inputs, storage, structure_snapshots as snapshots


class StructureSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_scans.ScanTests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        self.project=copy.deepcopy(self.fixture.project);self.root=self.fixture.root
        self.addCleanup(patch.stopall)
        patch.object(artifacts,'ROOT',self.root).start()
        self.bundle=inputs.freeze(self.project,'c',reason='冻结备注')
        self.structure=self.project['nodes'][0]['data']['structure']
        self.raw=base64.b64decode(self.structure['source']['base64'])
        self.key='a'*32
        self.record={'id':self.key,'state':'FAILED','project_id':'p','node_id':'c','bundle_id':self.bundle['id'],
            'config':{'alias':'offline'},'job_id':'9.mock','path_schema':'fpilot.remote-attempt-path',
            'remote_workspace_root':'/scratch/test','remote_relative_path':'project/attempt','remote_path_absolute':'/scratch/test/project/attempt',
            'evidence':{'vasp_timing':False,'ediff_reached':False}}
        with ex.db() as db:db.execute('INSERT INTO attempts VALUES(?,?,?)',(self.key,self.record['state'],json.dumps(self.record)))
        self.read=patch.object(artifacts,'_small_bytes',return_value=(self.raw,len(self.raw))).start()
        patch.object(ex,'ssh',side_effect=AssertionError('No real SSH')).start()

    def test_terminal_structure_uses_existing_preview_cache_and_preserves_original(self):
        preview=artifacts.preview(self.key,'CONTCAR')
        value=snapshots.snapshot('p',self.key,'CONTCAR')
        again=snapshots.snapshot('p',self.key,'CONTCAR')
        self.assertEqual(self.read.call_count,1)
        self.assertEqual(value['structure']['source']['base64'],base64.b64encode(self.raw).decode())
        self.assertTrue(value['origin']['cached']);self.assertEqual(again['origin']['sha256'],preview['structure']['source']['sha256'])
        self.assertEqual(value['origin']['input_note'],'冻结备注')
        self.assertEqual(value['origin']['state'],'FAILED');self.assertTrue(value['warnings'])
        self.assertEqual(ex.get(self.key),self.record)

    def test_ownership_paths_rejected_before_read_and_live_copies_warn(self):
        for project,key,path in [('other',self.key,'CONTCAR'),('p',self.key,'../CONTCAR'),('p',self.key,'OUTCAR')]:
            with self.subTest(path=path,project=project),self.assertRaises(ValueError):snapshots.snapshot(project,key,path)
        self.read.assert_not_called()
        for state in ['RUNNING','UNKNOWN','DISPATCHING','PREPARED']:
            with ex.db() as db:db.execute('UPDATE attempts SET state=?,body=? WHERE id=?',(state,json.dumps({**self.record,'state':state}),self.key))
            result=snapshots.snapshot('p',self.key,'POSCAR')
            self.assertEqual(result['origin']['state'],state)
            self.assertIn('当前文件副本',result['warnings'][0])
            self.assertEqual(ex.get(self.key)['state'],state)

    def test_full_parse_required_and_saved_origin_is_validated(self):
        self.read.return_value=(b'',0)
        with self.assertRaises(ValueError):snapshots.snapshot('p',self.key,'CONTCAR')
        self.read.return_value=(self.raw,262145)
        with self.assertRaises(ValueError):snapshots.snapshot('p',self.key,'CONTCAR')
        import shutil
        shutil.rmtree(self.root/'artifact-cache'/'previews',ignore_errors=True)
        self.read.return_value=(self.raw,len(self.raw))
        result=snapshots.snapshot('p',self.key,'CONTCAR')
        self.project['nodes'][0]['data'].update(structure=result['structure'],structureOrigin=result['origin'])
        storage.validate_workspace({'activeId':'p','projects':[self.project]})
        self.project['nodes'][0]['data']['structureOrigin']['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'身份'):storage.validate_workspace({'activeId':'p','projects':[self.project]})

    def test_derived_input_is_local_geometry_with_no_dependency_on_source_attempt(self):
        result=snapshots.snapshot('p',self.key,'CONTCAR')
        structure={'id':'derived','type':'step','position':{'x':400,'y':0},'data':{'kind':'structure','executionMode':'vasp','stageSequence':1,'stageSlug':'structure','structure':result['structure'],'structureOrigin':result['origin']}}
        target=copy.deepcopy(self.project['nodes'][1]);target['id']='next'
        project={**self.project,'nodes':[structure,target],'edges':[{'source':'derived','target':'next'}]}
        report=inputs.generate(project,'next')
        self.assertEqual(report['errors'],[]);self.assertEqual(report['dependencies'],[])
        self.assertEqual(report['source_plan']['required_files'],[])
        self.assertIn('POSCAR',report['files'])
        frozen=inputs.freeze(project,'next',reason='派生结构新输入')
        self.assertNotEqual(frozen['id'],self.bundle['id'])
        self.assertEqual(ex.get(self.key)['state'],'FAILED')

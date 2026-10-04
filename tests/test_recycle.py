import tempfile
import unittest
import hashlib
from pathlib import Path
from unittest.mock import patch
from backend import recycle


class RecycleTests(unittest.TestCase):
    def test_recipe_project_delete_and_restore_api_preserves_original_identity(self):
        from backend.app import RecycleMove,recycle_move,recycle_restore,recycle_list
        key='recipe-f6bf04d180b899b1f5baf1f1'
        project={'id':key,'name':'FeCr2O4 unit_cell · A1/A2/B 自动计算链草稿','remoteSlug':'fecr2o4',
                 'nodes':[{'id':'s','data':{'kind':'structure','executionMode':'vasp',
                                           'stageSequence':1,'stageSlug':'structure'}}],'edges':[]}
        with tempfile.TemporaryDirectory() as temp,patch.object(recycle,'ROOT',Path(temp)):
            response=recycle_move('projects',key,RecycleMove(project=project))
            self.assertTrue(response['recycled'])
            rows=recycle_list('projects')
            self.assertEqual(rows[0]['key'],key);self.assertEqual(rows[0]['project'],project)
            response=recycle_restore('projects',key)
            self.assertEqual(response['project'],project)
            self.assertEqual(recycle_list('projects'),[])
            # A second roundtrip stores the current draft, not the old archive.
            project['name']='恢复后修改的项目'
            self.assertTrue(recycle_move('projects',key,RecycleMove(project=project))['recycled'])
            self.assertEqual(recycle_restore('projects',key)['project'],project)

    def test_project_marker_supports_document_ids_and_retains_legacy_paths(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(recycle,'ROOT',Path(temp)):
            for key in ('a'*32,'12345678-1234-1234-1234-123456789abc'):
                self.assertEqual(recycle.marker('projects',key),Path(temp)/'recycle'/'projects'/key)
            for key in ('recipe-f6bf04d180b899b1f5baf1f1','项目/导入草稿'):
                expected='id-'+hashlib.sha256(key.encode()).hexdigest()
                self.assertEqual(recycle.marker('projects',key),Path(temp)/'recycle'/'projects'/expected)
            with self.assertRaisesRegex(ValueError,'回收对象无效'):recycle.marker('attempts','recipe-test')

    def test_project_roundtrip_keeps_complete_local_record(self):
        project={'id':'12345678-1234-1234-1234-123456789abc','name':'sample','remoteSlug':'sample',
                 'nodes':[{'id':'s','data':{'kind':'structure','executionMode':'vasp',
                                           'stageSequence':1,'stageSlug':'structure'}}],'edges':[]}
        with tempfile.TemporaryDirectory() as temp, patch.object(recycle,'ROOT',Path(temp)):
            result=recycle.move('projects',project['id'],project)
            self.assertTrue(result['recycled'])
            self.assertEqual(recycle.list_items('projects')[0]['project'],project)
            restored=recycle.restore('projects',project['id'])
            self.assertEqual(restored['project'],project)
            self.assertEqual(recycle.list_items('projects'),[])

    def test_active_attempt_can_be_recycled(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(recycle,'ROOT',Path(temp)), \
             patch('backend.execution.get',return_value={'id':'a'*32,'state':'RUNNING'}):
            self.assertTrue(recycle.move('attempts','a'*32)['recycled'])

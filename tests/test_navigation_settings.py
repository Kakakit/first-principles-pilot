import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from backend import app, local_setup


class NavigationSettingsTests(unittest.TestCase):
    def test_project_directory_preview_uses_execution_precedence(self):
        project = {'id':'a','remoteSlug':'fe','remoteWorkspaceRoot':'$HOME/group',
                   'remoteDirectories':{'override':'/scratch/one'}}
        hosts = {'normal':{'remote_workspace_root':'/scratch/base'},'override':{}}
        with patch('backend.servers.load_registry', return_value={'hosts':hosts}):
            preview = lambda alias: app.preview_project_directory('a', {'project':project,'alias':alias})
            self.assertEqual(preview('normal'),{'path':'$HOME/group/material-projects/fe','source':'project'})
            self.assertEqual(preview('override'),{'path':'/scratch/one','source':'override'})
            project['remotePathPolicy'] = 'project-directory-v2'
            self.assertEqual(preview('normal')['path'],'$HOME/group/fe')
            del project['remoteWorkspaceRoot']
            self.assertEqual(preview('normal'),{'path':'/scratch/base/fe','source':'server'})
            with self.assertRaises(HTTPException):
                preview('unknown')

    def test_observer_setting_survives_an_older_full_save(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'local.json'
            baseline={'workspace':folder,'port':8765,'research_root':''}
            def read():
                return {'saved':json.loads(path.read_text()) if path.exists() else {},'current':baseline}
            with patch.object(local_setup,'CONFIG',path),patch.object(local_setup,'read',side_effect=read):
                result=local_setup.save({'worker_mode':'track'},partial=True)
                self.assertTrue(result['restart_required'])
                self.assertEqual(local_setup.save(baseline)['saved']['worker_mode'],'track')
                with self.assertRaises(ValueError):local_setup.save({'worker_mode':'invalid'},partial=True)

    def test_worker_patch_preserves_unavailable_legacy_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json'
            previous={'saved':{'workspace':'/legacy/workspace','research_root':'/legacy/missing','worker_enabled':True,'port':8765},
                      'current':{'workspace':folder,'research_root':'','port':8765}}
            with patch.object(local_setup,'CONFIG',path),patch.object(local_setup,'read',return_value=previous):
                result=local_setup.save({'worker_enabled':False},partial=True)
                self.assertTrue(result['restart_required'])
                updated=json.loads(path.read_text())
                self.assertEqual(updated['research_root'],'/legacy/missing')
                self.assertEqual(updated['workspace'],'/legacy/workspace')
                self.assertFalse(updated['worker_enabled'])
                with self.assertRaises(ValueError):
                    local_setup.save({'potcar_library':'/duplicate'},partial=True)

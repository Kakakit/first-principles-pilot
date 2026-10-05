import hashlib
import io
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from backend import execution as ex, remote_paths as paths


class RemotePathTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.patch=patch.object(ex,'ROOT',self.root/'local');self.patch.start()
    def tearDown(self):
        self.patch.stop();self.temp.cleanup()
    def frozen(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            z.writestr('manifest.json',json.dumps({'node_id':'scf-1'}))
            z.writestr('project.json',json.dumps({'id':'project-1','name':'Project 1','remoteSlug':'project-1','nodes':[{'id':'scf-1','data':{'kind':'scf','executionMode':'vasp','stageSequence':3,'stageSlug':'scf'}}]}))
        raw=stream.getvalue();key=hashlib.sha256(raw).hexdigest()
        (ex.ROOT/'inputs').mkdir(parents=True);(ex.ROOT/'inputs'/(key+'.zip')).write_bytes(raw)
        return key
    def test_bad_roots_and_ids(self):
        for root in ('$HOME/other','/tmp/230269265lht/../outside','relative','/tmp/no-workspace','$HOME/230269265lht/$(whoami)'):
            with self.subTest(root=root),self.assertRaises(ValueError):paths.validate_root(root)
        with self.assertRaises(ValueError):paths.attempt_relative_path('../escape',3,'scf','20260921-120000-aaaa')
    def test_identity_mismatch(self):
        with self.assertRaises(ValueError):paths.frozen_identity({'project_id':'p','node_id':'s'},{'id':'q','nodes':[{'id':'s'}]})
        with self.assertRaises(ValueError):paths.frozen_identity({'node_id':'s'},{'id':'p','nodes':[{'id':'other'}]})
    def test_readonly_slurm_preview_no_capacity_or_directory_creation(self):
        key=self.frozen();root=self.root/'230269265lht';root.mkdir()
        def ssh(alias,command,**kwargs):
            self.assertEqual(alias,'gengzi-187')
            r=subprocess.run(['bash','-c',command],capture_output=True,text=True)
            return {'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
        with patch.object(ex,'ssh',side_effect=ssh),patch.object(ex.servers,'probe') as probe:
            result=ex.path_preview(key,'gengzi-187',str(root))
            probe.assert_not_called()
        self.assertFalse(result['remote_mutation']);self.assertEqual(list(root.iterdir()),[])
        self.assertIn('/project-1/flows/main/stages/03-scf/attempts/YYYYMMDD-HHMMSS-XXXX',result['remote_path_absolute'])
    def test_unknown_host_and_modified_bundle_fail_before_ssh(self):
        key=self.frozen()
        with patch.object(ex,'ssh') as ssh:
            with self.assertRaises(ValueError):ex.path_preview(key,'unknown')
            (ex.ROOT/'inputs'/(key+'.zip')).write_bytes(b'changed')
            with self.assertRaises(ValueError):ex.path_preview(key,'gengzi-187')
            ssh.assert_not_called()
    def test_missing_workspace_resolves_parent_without_creation(self):
        root=self.root/'230269265lht'
        r=subprocess.run(['bash','-c',paths.workspace_probe_command(str(root))],capture_output=True,text=True)
        probe=paths.parse_workspace_probe({'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr},str(root))
        self.assertEqual(probe['absolute_path'],str(root.resolve()))
        self.assertFalse(root.exists())
    def test_legacy_path_is_preserved_but_not_supported(self):
        r={'relative_path':'230269265lht/material-projects/workbench-validation/flows/single/stages/scf/attempts/old'}
        before=dict(r)
        with self.assertRaisesRegex(ValueError,'目录结构'):ex.attempt_path(r)
        self.assertEqual(r,before)
    def test_existing_destination_stops_before_unpack_and_qsub(self):
        root=self.root/'230269265lht';root.mkdir()
        relative=paths.attempt_relative_path('p',3,'scf','20260921-120000-aaaa');destination=root/relative;destination.mkdir(parents=True)
        sentinel=destination/'INCAR';sentinel.write_text('preserve original')
        record={'id':'a','state':'PREPARED','config':{'alias':'jutai-1'},'node':'node01','cores':1,'reason':'test','profile':{},'bundle_id':'test',
                'path_schema':'fpilot.remote-attempt-path','remote_workspace_root':str(root),'remote_workspace_root_abs':str(root),'remote_relative_path':relative,'remote_path_absolute':str(destination),
                'remote_project_path_absolute':str(root/'material-projects/p'),'project_id':'p','project_slug':'p',
                'script':'#!/bin/bash\ntrue\n','script_sha256':'test','input_audit':{}}
        with ex.db() as c:c.execute('INSERT INTO attempts VALUES(?,?,?)',('a','PREPARED',json.dumps(record)))
        def ssh(alias,command,data=None,**kwargs):
            r=subprocess.run(['bash','-c',command],input=data,capture_output=True)
            return {'exit_code':r.returncode,'stdout':r.stdout.decode(),'stderr':r.stderr.decode()}
        with patch.object(ex.servers,'probe',return_value={'id':'fresh'}),patch.object(ex.servers,'preview',return_value={'resource_ready':True,'profile':{}}),patch.object(ex,'bundle',return_value=({'stage':'scf'},{'INCAR':b'new'})),patch.object(ex,'ssh',side_effect=ssh),patch.object(ex,'start_watcher'):
            result=ex.submit('a')
        self.assertEqual(result['state'],'UNKNOWN');self.assertEqual(sentinel.read_text(),'preserve original');self.assertFalse((destination/'.job-id').exists())

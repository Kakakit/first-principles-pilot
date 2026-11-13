import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend import file_transfers as t, execution as ex


class FileTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.enterContext(patch.object(t,'ROOT',self.root))
        self.enterContext(patch.object(ex,'ROOT',self.root))
        t._stop.clear()
        self.source=self.root/'large file.bin';self.source.write_bytes(b'0123456789abcdef'*786433)
        self.location={'kind':'local','path':str(self.source)}
        self.stamp=t.observe(self.location)
    def enterContext(self,manager):
        result=manager.__enter__();self.addCleanup(manager.__exit__,None,None,None);return result
    def queue(self):return t._enqueue('project',self.stamp,'attempt','source','OUTCAR')
    def test_large_resume_restart_and_exact_bytes(self):
        row=self.queue();folder=t._base()/row['id'];folder.mkdir();partial=folder/'payload.part'
        raw=self.source.read_bytes();partial.write_bytes(raw[:1048576+7])
        self.assertFalse((folder/'payload').exists());self.assertEqual(t.get(row['id'])['bytes_transferred'],1048583)
        # A process disappearing while RUNNING can be claimed from persistent state.
        row=t._get(row['id']);row['state']='RUNNING';t._save(row)
        result=t.run(row['id']);self.assertEqual(result['state'],'COMPLETED',result.get('error'))
        path,name=t.download(row['id']);self.assertEqual(name,'OUTCAR');self.assertEqual(path.read_bytes(),raw)
        self.assertFalse(partial.exists());self.assertEqual(self.queue()['id'],row['id']);self.assertEqual(len(t.listing()),1)
    def test_cancel_keeps_partial_retry_and_releases_dependency(self):
        row=self.queue();folder=t._base()/row['id'];folder.mkdir();(folder/'payload.part').write_bytes(b'0123456789abcdef')
        self.assertTrue(t.dependents('attempt','source'))
        t.action(row['id'],'cancel');self.assertEqual(t.run(row['id'])['state'],'CANCELLED')
        self.assertFalse(t.dependents('attempt','source'));t.action(row['id'],'retry')
        self.assertEqual(t.run(row['id'])['state'],'COMPLETED');self.assertFalse(t.dependents('attempt','source'))
    def test_changed_source_rejects_before_resume_and_keeps_partial(self):
        row=self.queue();folder=t._base()/row['id'];folder.mkdir();(folder/'payload.part').write_bytes(b'0123')
        self.source.write_bytes(b'changed')
        result=t.run(row['id']);self.assertEqual(result['state'],'FAILED');self.assertIn('版本',result['error'])
        self.assertFalse((folder/'payload').exists());self.assertEqual((folder/'payload.part').read_bytes(),b'0123')
        self.assertTrue(t.dependents('attempt','source'))
    def test_mid_copy_source_change_never_publishes(self):
        row=self.queue();copy_file=t._copy_local
        def changed(record,part):copy_file(record,part);self.source.write_bytes(b'changed')
        with patch.object(t,'_copy_local',side_effect=changed):result=t.run(row['id'])
        self.assertEqual(result['state'],'FAILED');self.assertFalse((t._base()/row['id']/'payload').exists())
    def test_duplicate_request_and_current_source_verification(self):
        ref={'schema':'fpilot.artifact-ref.v1','project_id':'project','producer_kind':'attempt','producer_id':'source','artifact_id':'OUTCAR','version':{'size_bytes':self.stamp['size_bytes']}}
        with patch('backend.artifact_refs.resolve',return_value={'locations':[self.location]}):
            a=t.request('project',ref);self.assertEqual(t.request('project',ref)['id'],a['id'])
            ref['version']['size_bytes']=1
            with self.assertRaisesRegex(ValueError,'大小'):t.request('project',ref)
    def test_remote_origin_local_copy_uses_its_own_timestamp(self):
        ref={'schema':'fpilot.artifact-ref.v1','project_id':'project','producer_kind':'analysis','producer_id':'source','artifact_id':'OUTCAR','version':{'size_bytes':self.stamp['size_bytes'],'mtime_ns':1}}
        with patch('backend.artifact_refs.resolve',return_value={'locations':[self.location],'origin_kind':'remote'}):
            row=t.request('project',ref)
        self.assertEqual(t.run(row['id'])['state'],'COMPLETED')

    def test_cleaned_remote_analysis_keeps_local_copy_collectable(self):
        row=t._enqueue('project',self.stamp,'analysis','analysis-source','report.bin')
        with patch('backend.recycle.inactive',return_value=False),patch('backend.file_lifecycle.remote_is_cleaned',return_value=True) as cleaned,patch.object(t.remote_transport,'run',side_effect=AssertionError('local file needs no SSH')):
            result=t.run(row['id'])
        self.assertEqual(result['state'],'COMPLETED',result.get('error'))
        self.assertEqual(t.download(row['id'])[0].read_bytes(),self.source.read_bytes())
        cleaned.assert_not_called()

    def test_cleaned_remote_source_still_blocks_transfer(self):
        from contextlib import nullcontext
        source={**self.stamp,'kind':'remote','alias':'school','connection':{}}
        row=t._enqueue('project',source,'analysis','analysis-source','report.bin')
        with patch.object(t.site_registration,'host_operation',return_value=nullcontext()),patch.object(t.site_registration,'connection_identity',return_value={}),patch('backend.recycle.inactive',return_value=False),patch('backend.file_lifecycle.remote_is_cleaned',return_value=True),patch.object(t,'observe') as observe,patch.object(t,'_copy_remote') as copy_remote:
            result=t.run(row['id'])
        self.assertEqual(result['state'],'FAILED');self.assertIn('清理',result['error'])
        observe.assert_not_called();copy_remote.assert_not_called()

    def test_recycled_producer_still_blocks_local_collection(self):
        row=t._enqueue('project',self.stamp,'analysis','analysis-source','report.bin')
        with patch('backend.recycle.inactive',return_value=True),patch.object(t,'_copy_local') as copy_local:
            result=t.run(row['id'])
        self.assertEqual(result['state'],'FAILED');self.assertIn('回收',result['error']);copy_local.assert_not_called()

    def test_no_ssh_in_isolated_transfer(self):
        row=t._enqueue('project',{**self.stamp,'kind':'remote','alias':'school','connection':{}},'attempt','source','OUTCAR')
        with patch.object(t.site_registration,'host_operation',return_value=__import__('contextlib').nullcontext()),patch.object(t.site_registration,'connection_identity',return_value={}),patch.object(t,'observe',return_value=t._get(row['id'])['source']),patch.dict(os.environ,{'FPILOT_REMOTE_DISABLED':'1'}):
            result=t.run(row['id'])
        self.assertEqual(result['state'],'FAILED');self.assertIn('禁止',result['error'])
    @unittest.skipUnless(__import__('shutil').which('rsync') and os.name=='posix','requires local rsync')
    def test_rsync_adapter_resumes_through_local_ssh_stub(self):
        import sys
        tools=self.root/'tools';tools.mkdir();ssh=tools/'ssh'
        ssh.write_text('#!'+sys.executable+'\nimport os,sys\na=sys.argv[1:]\nwhile a and a[0]=="-o":a=a[2:]\nassert a.pop(0)=="synthetic"\nos.execv("/bin/sh",["sh","-c"," ".join(a)])\n');ssh.chmod(0o755)
        source={**self.stamp,'kind':'remote','alias':'synthetic','connection':{}}
        row=t._enqueue('project',source,'attempt','source','OUTCAR');folder=t._base()/row['id'];folder.mkdir()
        (folder/'payload.part').write_bytes(self.source.read_bytes()[:1048579])
        from contextlib import nullcontext
        with patch.dict(os.environ,{'PATH':str(tools)+os.pathsep+os.environ['PATH'],'FPILOT_REMOTE_DISABLED':'0'}),patch.object(t.site_registration,'host_operation',return_value=nullcontext()),patch.object(t.site_registration,'connection_identity',return_value={}),patch.object(t,'observe',return_value=source):
            result=t.run(row['id'])
        self.assertEqual(result['state'],'COMPLETED',result.get('error'))
        self.assertEqual(t.download(row['id'])[0].read_bytes(),self.source.read_bytes())

    def test_symlink_source_and_unknown_job(self):
        with self.assertRaises(ValueError):t.get('invalid')
        link=self.root/'link'
        try:
            link.symlink_to(self.source)
        except OSError as exc:
            if os.name=='nt' and exc.winerror==1314:
                self.skipTest('Windows user lacks symbolic-link privilege')
            raise
        with self.assertRaisesRegex(ValueError,'普通文件'):t.observe({'kind':'local','path':str(link)})
    def test_input_preparation_uses_version_pinned_cache(self):
        source={'attempt_id':'source','project_id':'project','alias':'school','remote_root':'/case','files':[{'name':'OUTCAR','size_bytes':self.stamp['size_bytes'],'modified_at':__import__('datetime').datetime.fromtimestamp(self.stamp['mtime_ns']/1e9,__import__('datetime').timezone.utc).isoformat()}]}
        with patch.object(t,'observe',return_value=self.stamp):
            dest=self.root/'inputs'/'OUTCAR';t.prepare_source(source,'OUTCAR',dest)
        self.assertEqual(dest.read_bytes(),self.source.read_bytes())


if __name__=='__main__':unittest.main()

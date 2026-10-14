"""Snapshot transport contracts; fixtures never represent scientific acceptance."""
import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from backend import project_sync as sync, snapshot_sync as transfer, project_snapshot as snap, project_sync_remote as protocol
from backend.storage import WorkspaceStore
from test_project_sync import initialize

class SnapshotSyncTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve();self.a=self.root/'Mac';self.b=self.root/'Windows';self.cloud=self.root/'任意快照 空格';self.a.mkdir();self.b.mkdir();initialize(self.a)
  self.calls=[];self.patches=[patch.object(sync.ex,'ROOT',self.a),patch.object(sync,'registered',return_value={'alias':'school','root':'$HOME/fpilot'})]
  def remote(config,action,pid=None,data=b'',**values):
   self.calls.append((action,copy.deepcopy(values)))
   return {'ok':True,**protocol.run(Path(config['root']),{'action':action,'project_id':pid,'snapshot_directory':bool(config.get('snapshot_directory')),**values},data)}
  self.patches.append(patch.object(sync,'remote',side_effect=remote))
  for p in self.patches:p.start()
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.temp.cleanup()
 def preview(self,direction,root=None):return sync.preview('project-a','school',direction,snapshot_directory=str(root or self.cloud))
 def upload(self):
  p=self.preview('upload');return sync.transfer('project-a',p['preview_id'])
 def home(self):sync.ex.ROOT=self.b
 def test_new_directory_preview_does_not_create_it_and_supports_spaces(self):
  p=self.preview('upload');self.assertEqual(p['absolute_directory'],str(self.cloud));self.assertFalse(self.cloud.exists())
  self.assertEqual(transfer.directory('$HOME/中文 空格'),'$HOME/中文 空格')
  for value in ('/','///','../a','$HOME/a/../b','C:\\home','/a\nq'):
   with self.subTest(value=value),self.assertRaises(ValueError):transfer.directory(value)
 def test_upload_download_keeps_ids_paths_inputs_and_pauses_without_claim(self):
  before=snap.read_tables(self.a,'execution.sqlite3')['attempts'][0]
  result=self.upload();self.assertEqual(result['local']['mode'],'SNAPSHOT');self.assertIsNone(result['state']['owner']);self.assertTrue(sync.locally_executable('project-a'))
  chain=snap.read_tables(self.a,'execution.sqlite3')['chain_runs'][0];self.assertEqual(chain['state'],'PAUSED');self.assertEqual(json.loads(chain['body'])['authorization'],{'approved':True})
  self.home();p=self.preview('download');sync.transfer('project-a',p['preview_id'])
  after=snap.read_tables(self.b,'execution.sqlite3')['attempts'][0]
  for key in ('id','state'):self.assertEqual(before[key],after[key])
  a,b=json.loads(before['body']),json.loads(after['body']);self.assertEqual(a['remote_path'],b['remote_path']);self.assertEqual(b['state'],'UNKNOWN')
  self.assertEqual((self.a/'inputs'/('c'*64+'.zip')).read_bytes(),(self.b/'inputs'/('c'*64+'.zip')).read_bytes())
  self.assertNotIn('claim',[a for a,_ in self.calls]);self.assertTrue(sync.local('project-a')['imported'])
 def test_uploaded_project_remains_editable_and_directory_can_change(self):
  self.upload();w=WorkspaceStore(self.a).read();w['workspace']['projects'][0]['name']='可以编辑';WorkspaceStore(self.a).save(w['workspace'],w['revision'])
  other=self.cloud.with_name('另一个库');p=self.preview('upload',other);self.assertEqual(p['name'],'可以编辑');self.assertFalse(other.exists())
  self.assertEqual(sync.catalog('school',str(other))['projects'],[])
 def test_dirty_download_needs_choice_and_original_backup(self):
  self.upload();self.home();initialize(self.b,rich=False);p=self.preview('download')
  with self.assertRaisesRegex(ValueError,'明确'):sync.transfer('project-a',p['preview_id'])
  result=sync.transfer('project-a',p['preview_id'],True);self.assertTrue(Path(result['backup']).exists())
 def test_failed_upload_remains_paused_and_receipt_recovery_never_republishes(self):
  original=sync.remote.side_effect
  def lose(config,action,*args,**kwargs):
   result=original(config,action,*args,**kwargs)
   if action=='publish':raise sync.SyncStopped('receipt lost')
   return result
  sync.remote.side_effect=lose
  with self.assertRaisesRegex(ValueError,'暂停'):self.upload()
  self.assertFalse(sync.locally_executable('project-a'));self.assertEqual(snap.read_tables(self.a,'execution.sqlite3')['chain_runs'][0]['state'],'PAUSED')
  sync.remote.side_effect=original;self.calls.clear();sync.recover('project-a');self.assertEqual(sync.local('project-a')['mode'],'SNAPSHOT');self.assertNotIn('publish',[a for a,_ in self.calls])
 def test_pending_analysis_is_paused_but_running_remote_keeps_tracking(self):
  path=self.a/'analyses-v2'/('d'*32)/'analysis.json';r=json.loads(path.read_bytes());r['state']='QUEUED';path.write_text(json.dumps(r))
  self.upload();self.assertEqual(json.loads(path.read_bytes())['state'],'PAUSED')
  self.home();p=self.preview('download');sync.transfer('project-a',p['preview_id']);self.assertEqual(json.loads((self.b/'analyses-v2'/('d'*32)/'analysis.json').read_bytes())['state'],'PAUSED')
 def test_v1_client_rejects_new_snapshot_and_cloud_identity_is_frozen(self):
  self.upload()
  raw=next((self.cloud/'project-a/versions').glob('*.json')).read_text();version=json.loads(raw)['version']
  self.assertEqual(json.loads((self.cloud/'project-a/objects'/version).read_bytes())['schema'],transfer.SCHEMA)
  self.home();p=self.preview('download');info=json.loads((sync.ex.ROOT/'project-sync/previews'/(p['preview_id']+'.json')).read_bytes());self.assertEqual(info['config']['root'],str(self.cloud))
 def test_unknown_old_handoff_is_not_unlocked(self):
  sync.write_json(sync.local_path('project-a'),{'mode':'STOPPED','root':str(self.cloud),'alias':'school','operation':'unknown'})
  with self.assertRaisesRegex(ValueError,'不直接解锁'):transfer.convert('project-a')
 def test_repeated_download_retains_original_jobs_and_no_ownership_claim(self):
  self.upload();self.home();p=self.preview('download');sync.transfer('project-a',p['preview_id']);p=self.preview('download');sync.transfer('project-a',p['preview_id']);self.assertEqual(len(snap.read_tables(self.b,'execution.sqlite3')['attempts']),1)
 def test_existing_v1_handoff_converts_without_changing_remote_library(self):
  legacy=self.root/'old-computation';legacy.mkdir()
  with patch.object(sync,'registered',return_value={'alias':'school','root':str(legacy)}):
   p=sync.preview('project-a','school','upload');sync.transfer('project-a',p['preview_id'])
   old=(legacy/'.fpilot/project-sync/project-a/state.json').read_bytes()
   transfer.convert('project-a');self.assertTrue(sync.locally_executable('project-a'));self.assertEqual(old,(legacy/'.fpilot/project-sync/project-a/state.json').read_bytes())
   self.home();p=sync.preview('project-a','school','download',snapshot_directory=str(legacy/'.fpilot/project-sync'));sync.transfer('project-a',p['preview_id'])
   self.assertEqual(snap.read_tables(self.b,'execution.sqlite3')['chain_runs'][0]['state'],'PAUSED')
 def test_foreign_operation_and_symlink_location_stay_blocked(self):
  self.cloud.mkdir();link=self.root/'linked';link.symlink_to(self.cloud,target_is_directory=True)
  with self.assertRaisesRegex(ValueError,'符号链接'):self.preview('upload',link)
  (self.cloud/'project-a').mkdir();(self.cloud/'project-a/operation.json').write_text(json.dumps({'token':'foreign','device':'other','purpose':'upload_snapshot'}))
  p=self.preview('upload')
  with self.assertRaisesRegex(ValueError,'未完成'):sync.transfer('project-a',p['preview_id'])
 def test_cloud_ssh_failure_still_pauses_before_attempting_transfer(self):
  p=self.preview('upload');sync.remote.side_effect=sync.SyncStopped('SSH timeout')
  with self.assertRaisesRegex(ValueError,'保持暂停'):sync.transfer('project-a',p['preview_id'])
  self.assertEqual(snap.read_tables(self.a,'execution.sqlite3')['chain_runs'][0]['state'],'PAUSED')
 def test_completed_and_unknown_analysis_are_never_resumed_by_download(self):
  path=self.a/'analyses-v2'/('d'*32)/'analysis.json';r=json.loads(path.read_bytes());r.update(state='UNKNOWN',submission_started=True);path.write_text(json.dumps(r))
  self.upload();self.home();p=self.preview('download');sync.transfer('project-a',p['preview_id']);self.assertEqual(json.loads((self.b/'analyses-v2'/('d'*32)/'analysis.json').read_bytes())['state'],'UNKNOWN')
 def test_worker_with_stale_active_plan_cannot_submit_after_pause(self):
  from backend.attempt_operations import AttemptBusy
  self.upload()
  with self.assertRaises(AttemptBusy),sync.dispatch_guard({'project_id':'project-a','chain_run_id':'chain_runs'}):self.fail('stale worker passed')
  with sqlite3.connect(self.a/'execution.sqlite3') as db:
   r=json.loads(db.execute('SELECT body FROM chain_runs').fetchone()[0]);r['state']='ACTIVE';db.execute('UPDATE chain_runs SET state=?,body=?',('ACTIVE',json.dumps(r)))
  with sync.dispatch_guard({'project_id':'project-a','chain_run_id':'chain_runs'}):pass
 def test_partial_restore_recovery_only_allows_download(self):
  self.upload();self.home();p=self.preview('download')
  with patch.object(snap,'restore',side_effect=OSError('interrupted restore')):
   with self.assertRaisesRegex(ValueError,'暂停'):sync.transfer('project-a',p['preview_id'])
  sync.recover('project-a');self.assertTrue(sync.local('project-a')['restore_required']);self.assertFalse(sync.locally_executable('project-a'))
  with self.assertRaisesRegex(ValueError,'只允许'):self.preview('upload')
  p=self.preview('download');sync.transfer('project-a',p['preview_id']);self.assertTrue(sync.locally_executable('project-a'))
 def test_imported_connection_binding_survives_another_upload(self):
  with sqlite3.connect(self.a/'execution.sqlite3') as db:
   r=json.loads(db.execute('SELECT body FROM attempts WHERE id=?',('a'*32,)).fetchone()[0]);r['settings_snapshot']={'connection':{'hostname':'server','identityfile':'mac-key'}};db.execute('UPDATE attempts SET body=? WHERE id=?',(json.dumps(r),'a'*32))
  self.upload();self.home();p=self.preview('download');sync.transfer('project-a',p['preview_id']);self.upload()
  self.assertTrue(sync.continuation_connection({'hostname':'server','identityfile':'mac-key'},{'hostname':'server','identityfile':'windows-key'},'school'))
  self.assertFalse(sync.continuation_connection({'hostname':'server','identityfile':'mac-key'},{'hostname':'wrong','identityfile':'windows-key'},'school'))
 def test_actual_legacy_api_rejects_v2_manifest(self):
  self.cloud=self.root/'.fpilot/project-sync';self.upload();self.home()
  with patch.object(sync,'registered',return_value={'alias':'school','root':str(self.root)}):
   with self.assertRaisesRegex(ValueError,'身份不符'):sync.preview('project-a','school','download')
 def test_path_migration_preserves_unrelated_json_parameter_text(self):
  original='{"resource_request":"{\\"gpus\\":2,\\"mode\\":\\"gpu\\"}","asset":"/old/file"}'
  changed=json.loads(snap.migrate(original,'/old','/new'))
  self.assertEqual(changed['asset'],'/new/file');self.assertEqual(changed['resource_request'],'{"gpus":2,"mode":"gpu"}')

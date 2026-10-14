import json,sqlite3,tempfile,unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from backend import site_registration as sites,servers,execution,attempt_operations


class SiteRemovalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        host={'scheduler':'pbs','queue':'batch','status':'USER_CONFIGURED','runtime_source':'script','runtimes':[], 'python':{'known_interpreter':'/opt/python3'}}
        self.builtin=self.root/'builtin.json';self.builtin.write_text(json.dumps({'hosts':{'builtin':host}}))
        (self.root/'site-registry.json').write_text(json.dumps({'schema':'fpilot.site-registry.v1','hosts':{'mine':host}}))
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for module in (sites,servers,execution):self.stack.enter_context(patch.object(module,'ROOT',self.root))
        self.stack.enter_context(patch.object(servers,'REGISTRY_PATH',self.builtin))
        with execution.db() as db:
            self.record={'id':'one','state':'RUNNING','config':{'alias':'mine'},'job_id':'123.node01'}
            db.execute('INSERT INTO attempts VALUES(?,?,?)',('one','RUNNING',json.dumps(self.record)));db.commit()

    def test_remove_restore_keeps_exact_history_config_and_ssh(self):
        saved=servers.select_profile('mine');raw=json.dumps(saved).encode();(self.root/'server-config.json').write_bytes(raw)
        before=(self.root/'execution.sqlite3').read_bytes()
        removed=sites.remove('mine',sites.removal_preview('mine')['revision'])
        self.assertEqual(removed['config']['alias'],'builtin');self.assertNotIn('mine',servers.load_registry()['hosts'])
        self.assertEqual((self.root/'server-config.json').read_bytes(),raw);self.assertEqual((self.root/'execution.sqlite3').read_bytes(),before)
        from backend import remote_transport
        self.assertEqual(remote_transport._interpreter('mine'),'/opt/python3')
        sites.restore('mine');self.assertEqual(servers.read_config()['config'],saved);self.assertEqual(execution.get('one'),self.record)

    def test_builtin_and_last_server_can_be_hidden_and_restored(self):
        sites.remove('mine',sites.removal_preview('mine')['revision'])
        sites.remove('builtin',sites.removal_preview('builtin')['revision'])
        self.assertEqual(servers.registered_profiles(),[]);self.assertEqual(servers.read_config()['config']['alias'],'')
        sites.restore('builtin');self.assertEqual(servers.registered_profiles()[0]['alias'],'builtin')

    def test_reregister_restores_one_alias_without_losing_other_removals(self):
        for alias in ('mine','builtin'):sites.remove(alias,sites.removal_preview(alias)['revision'])
        value={'alias':'mine','scheduler':'pbs','queue':'batch','runtime_source':'script','remote_workspace_root':'$HOME/fpilot'}
        with patch.object(sites,'ssh_aliases',return_value=['mine']),patch.object(servers,'save_config'):
            sites.register(value)
        self.assertIn('mine',servers.load_registry()['hosts']);self.assertIn('builtin',sites.removed_records())

    def test_stale_preview_dispatching_and_dispatch_lock_block_remove(self):
        with self.assertRaisesRegex(ValueError,'变化'):sites.remove('mine','stale')
        with sites.host_operation('mine'):
            from contextvars import Context
            with self.assertRaisesRegex(ValueError,'正在派发'):Context().run(sites.remove,'mine',sites.removal_preview('mine')['revision'])
        with execution.db() as db:
            body={**self.record,'state':'DISPATCHING'};db.execute('UPDATE attempts SET state=?,body=?',('DISPATCHING',json.dumps(body)));db.commit()
        with self.assertRaisesRegex(ValueError,'提交回执'):sites.remove('mine',sites.removal_preview('mine')['revision'])

    def test_prepared_dispatch_stops_but_historical_monitoring_survives(self):
        sites.remove('mine',sites.removal_preview('mine')['revision'])
        function=attempt_operations.serialized_submit(lambda key:'read-only history')
        self.assertEqual(function('one'),'read-only history')
        with execution.db() as db:
            body={**self.record,'state':'PREPARED'};db.execute('UPDATE attempts SET state=?,body=?',('PREPARED',json.dumps(body)));db.commit()
        with self.assertRaisesRegex(ValueError,'登记已移除'):function('one')

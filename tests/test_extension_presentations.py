"""Recovery, detached execution, and an ID-independent presentation lifecycle."""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack,contextmanager
from pathlib import Path
from unittest.mock import patch
from backend import extension_protocol as p,extension_runs as runs,extension_remote as remote,extension_presentations as views,extension_process as process,execution as ex,artifacts

BASE=Path(__file__).resolve().parents[1]


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.stack=ExitStack()
        for module in (p,runs,remote,ex,artifacts):self.stack.enter_context(patch.object(module,'ROOT',self.root))
        self.addCleanup(self.stack.close);self.addCleanup(self.temp.cleanup)
        self.record={'id':'a'*32,'state':'UNKNOWN','created_at':'2026-10-07T00:00:00+00:00','manifest':{},'extension_id':'unknown-fixture','location':'ssh','remote_dir':'/analysis/original','runtime':{'python':sys.executable,'alias':'fixture'},'sources':[],'result':None,'outputs':[]}
        runs.save(self.record)
    def test_old_successful_cli_receipt_recovers_without_inventing_exit_or_rerunning(self):
        def collect(record):record.update(state='COMPLETED');runs.save(record)
        receipt={'exit':None,'run_receipt':{'schema':'fpilot.extension-run.v2','result_file':'/analysis/original/outputs/result.json'},'process_alive':False}
        with patch.object(remote,'inspect',return_value=receipt),patch.object(runs,'_collect',side_effect=collect),patch.object(remote,'direct') as direct,patch.object(remote,'submit') as submit:
            record=runs.refresh(self.record['id']);runs._execute(self.record['id']);direct.assert_not_called();submit.assert_not_called()
        self.assertEqual(record['state'],'COMPLETED');self.assertIsNone(record.get('exit_code'));self.assertFalse(record['verification']['wrapper_exit_recorded']);self.assertIn('收集',record['refresh_message'])
    def test_wrong_receipt_and_dead_process_remain_unknown(self):
        with patch.object(remote,'inspect',return_value={'exit':None,'run_receipt':{'schema':'fpilot.extension-run.v2','result_file':'/other/result.json'}}),patch.object(runs,'_collect') as collect:
            record=runs.refresh(self.record['id']);collect.assert_not_called()
        self.assertEqual(record['state'],'UNKNOWN');self.assertIsNotNone(record['last_checked_at'])
    def test_live_process_after_lost_connection_is_running(self):
        with patch.object(remote,'inspect',return_value={'exit':None,'process_alive':True,'launch':{'pid':123}}):record=runs.refresh(self.record['id'])
        self.assertEqual(record['state'],'RUNNING');self.assertNotIn('error',record)
        with patch.object(remote,'inspect',return_value={'exit':None,'process_alive':True}),patch.object(remote,'direct') as direct:runs.tick();direct.assert_not_called()
    def test_concurrent_refresh_has_feedback_without_changing_record(self):
        @contextmanager
        def busy(_):yield False
        with patch.object(runs,'lock',busy):record=runs.refresh(self.record['id'])
        self.assertTrue(record['refresh_busy']);self.assertIn('后台',record['refresh_message']);self.assertNotIn('last_checked_at',runs.get(self.record['id']))
    def test_pid_reuse_and_reboot_do_not_count_as_alive(self):
        marker={'pid':42,'start_ticks':'10','boot':'old'}
        for replacement in ({**marker,'start_ticks':'11','state':'R'},{**marker,'boot':'new','state':'R'},{**marker,'state':'Z'},None):
            with patch.object(process,'identity',return_value=replacement):self.assertFalse(process.alive(marker))
    def test_detached_launch_survives_caller_exit_and_is_reserved_once(self):
        runner=self.root/'runner.py';shutil.copy2(BASE/'backend/extension_process.py',runner)
        (self.root/'launch.sh').write_text("sleep 0.3\nprintf 'one\\n' >> invoked.txt\n")
        command=[sys.executable,str(runner),'start',str(self.root),'--timeout','3']
        first=json.loads(subprocess.check_output(command,text=True));second=json.loads(subprocess.check_output(command,text=True))
        self.assertTrue(first['started']);self.assertFalse(second['started'])
        deadline=time.monotonic()+5
        while not (self.root/'exit.json').exists() and time.monotonic()<deadline:time.sleep(.02)
        self.assertEqual((self.root/'invoked.txt').read_text(),'one\n');self.assertEqual(json.loads((self.root/'exit.json').read_text())['exit_code'],0)


class PresentationHostTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.stack=ExitStack()
        for module in (p,runs,remote,ex,artifacts):self.stack.enter_context(patch.object(module,'ROOT',self.root))
        self.stack.enter_context(patch.object(runs,'schedule'));self.stack.enter_context(patch.object(views._executor,'submit',side_effect=lambda f:f()))
        self.stack.enter_context(patch.object(ex,'ssh',side_effect=AssertionError('real SSH forbidden')))
        self.addCleanup(self.stack.close);self.addCleanup(self.temp.cleanup);p._cache.clear();p._doctor_cache.clear()
    def package(self,bad=False):
        package=self.root/'package';shutil.copytree(BASE/'extension-templates/structure-statistics',package)
        manifest=json.loads((package/'extension.manifest.json').read_text());manifest.update(id='different-viewer-name',presentation={'entrypoint':'scripts/view.py'});p.atomic_json(package/'extension.manifest.json',manifest)
        script="import json,pathlib,sys\nr=json.load(open(sys.argv[-1]));p=pathlib.Path(r['output_dir']);p.mkdir(exist_ok=True);(p/'view.html').write_text('<html><head></head><body>independent</body></html>');(p/'data.json').write_text('{}');print(json.dumps({'schema':'fpilot.extension-presentation.v1','entry_html':'view.html','assets':[{'path':'view.html','media_type':'text/html'},{'path':'data.json','media_type':'application/json'}]}))\n"
        if bad:script="raise ValueError('presentation fixture failure')\n"
        (package/'scripts/view.py').write_text(script);return p.install_directory(package)
    def execute(self):
        payload={'extension_id':'different-viewer-name','sources':[{'kind':'structure','poscar':(BASE/'tests/fixtures/Si.POSCAR').read_text()}],'options':{'include_elements':True}}
        key=runs.start(payload)['analysis_id'];runs._execute(key);return runs.get(key)
    def test_generic_view_snapshot_survives_uninstall_and_state_versions_preserve_data(self):
        self.package();record=self.execute();self.assertEqual(record['state'],'COMPLETED');view=record['presentation'];self.assertEqual(view['status'],'READY')
        before=runs.output(record['id'],'elements.csv')[0].read_bytes();saved=views.save_state(record['id'],view['id'],{'labels':['confirmed']});self.assertEqual(saved['revision'],1)
        second=views.save_state(record['id'],view['id'],{'labels':['revised']});self.assertEqual(second['revision'],2)
        self.assertEqual(json.loads((views.root(record['id'])/view['id']/'states/1.json').read_text())['value']['labels'],['confirmed'])
        p.uninstall('different-viewer-name');self.assertIn('independent',views.asset(record['id'],view['id'],'view.html')[0].read_text());self.assertEqual(runs.output(record['id'],'elements.csv')[0].read_bytes(),before)
        with self.assertRaisesRegex(ValueError,'声明'):views.asset(record['id'],view['id'],'../../analysis.json')
    def test_view_failure_does_not_fail_analysis(self):
        self.package(bad=True);record=self.execute();self.assertEqual(record['state'],'COMPLETED');self.assertEqual(record['presentation']['status'],'FAILED');self.assertTrue(runs.output(record['id'],'elements.csv')[0].is_file())
    def test_update_preserves_saved_view_state_and_uses_frozen_python(self):
        self.package();record=self.execute();view=record['presentation'];views.save_state(record['id'],view['id'],{'confirmed':True})
        views.schedule(record['id']);new=runs.get(record['id'])['presentation'];self.assertNotEqual(view['id'],new['id']);self.assertEqual(views.state(record['id'],new['id'])['value'],{'confirmed':True});self.assertEqual(new['python'],record['runtime']['python'])
    def test_protocol_keeps_old_packages_and_bounds_optional_assets(self):
        manifest=p.inspect_package(BASE/'extension-templates/structure-statistics')['manifest'];self.assertNotIn('presentation',manifest)
        with self.assertRaisesRegex(ValueError,'10 MiB'):views.validate({'schema':'fpilot.extension-presentation.v1','entry_html':'view.html','assets':[{'path':'view.html','media_type':'text/html'}]},self.root,{'view.html':artifacts.MAX_PULL_BYTES+1})


if __name__=='__main__':unittest.main()

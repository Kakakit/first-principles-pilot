import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import patch
from backend import execution as ex, execution_control as control, problem_records as problems, recycle


class ProblemTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.stack=ExitStack()
        for module in (ex,recycle):self.stack.enter_context(patch.object(module,'ROOT',self.root))
        self.remote=self.stack.enter_context(patch.object(ex,'ssh',side_effect=AssertionError('notebook must never contact a cluster')))
        self.project={'id':'project','name':'测试材料','nodes':[{'id':'scf','data':{'label':'SOC 自洽','kind':'scf'}}]}
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            for name,text in {'project.json':json.dumps(self.project),'manifest.json':json.dumps({'stage':'scf','node_id':'scf','files':{}}),'INCAR':'NCORE = 8\nLCALCPOL = .TRUE.\n','KPOINTS':'Regular mesh\n0\nGamma\n4 4 4\n0 0 0\n'}.items():archive.writestr(name,text)
        raw=stream.getvalue();self.bundle=hashlib.sha256(raw).hexdigest();(self.root/'inputs').mkdir();(self.root/'inputs'/(self.bundle+'.zip')).write_bytes(raw)
        self.add('a'*32,'FAILED')

    def tearDown(self):
        self.remote.assert_not_called();self.stack.close();self.temp.cleanup()

    def add(self,key,state,**extra):
        record={'id':key,'project_id':'project','node_id':'scf','state':state,'created_at':'2026-10-08T00:00:00Z','bundle_id':self.bundle,'config':{'alias':'test-server'},'evidence':{'wrapper_exit':1},'note':'The PEAD routines do not work for NCORE /= 1',**extra}
        with closing(ex.db()) as connection:connection.execute('INSERT INTO attempts VALUES(?,?,?)',(key,state,json.dumps(record)));connection.commit()
        return record

    def test_backfill_only_failed_and_explicit_notes_not_warnings_or_unknown(self):
        self.add('b'*32,'COMPLETED',evidence={'warnings':['harmless warning'],'ediff_reached':False})
        self.add('c'*32,'UNKNOWN');self.add('d'*32,'PREPARED')
        self.add('e'*32,'COMPLETED')
        (self.root/'failure-notes').mkdir();(self.root/'failure-notes'/('e'*32+'.json')).write_text(json.dumps({**dict.fromkeys(problems.NOTE_FIELDS,''),'user_note':'人工判断需要记录的警告'}))
        problems.backfill();problems.backfill()
        rows=problems.listing();self.assertEqual({r['source_id'] for r in rows},{'a'*32,'e'*32})
        self.assertEqual(next(r for r in rows if r['source_id']=='e'*32)['collection'],'manual')
        row=next(r for r in rows if r['source_id']=='a'*32)
        self.assertEqual((row['project_name'],row['node_name']),('测试材料','SOC 自洽'))
        self.assertIn('NCORE = 8',row['evidence']['input']['incar'])

    def test_failure_summary_prefers_receipt_over_old_preparation_note(self):
        record = ex.get('a'*32)
        record.update(note='输入准备完成，等待授权', status_receipt={'stderr':'bash: dirname: command not found'})
        row = problems.capture('attempts',record['id'],record)
        self.assertIn('dirname',row['error_summary'])
        self.assertNotIn('等待授权',row['error_summary'])

    def test_shared_notes_and_facts_survive_actual_purge(self):
        row=problems.capture('attempts','a'*32)
        notes={**row['notes'],'user_note':'检查并行参数','suspected_cause':'NCORE 与 PEAD 不兼容'}
        control.save_notes('a'*32,notes)
        row=problems.save(row['id'],{**notes,'confirmed_cause':'NCORE 必须为 1','confirmation_evidence':'OUTCAR 末尾原始错误'},['NCORE','LCALCPOL'])
        self.assertEqual(control.notes('a'*32),row['notes'])
        original=row['evidence']
        recycle.move('attempts','a'*32);recycle.purge('attempts','a'*32,True)
        (self.root/'inputs'/(self.bundle+'.zip')).unlink()
        retained=problems.get(row['id']);self.assertTrue(retained['source_deleted']);self.assertEqual(retained['evidence'],original)
        retained=problems.save(row['id'],{**notes,'user_note':'删除原任务后仍可完善经验'},['NCORE'])
        self.assertIn('删除原任务',problems.export([row['id']],'markdown'))
        self.assertIn('NCORE',problems.export([row['id']],'json'))

    def test_manual_log_excerpt_is_bounded_redacted_deduplicated_and_validated(self):
        self.add('b'*32,'COMPLETED')
        excerpt={'file':'OUTCAR','location':'预览第 12 行','text':'warning token=private'}
        row=problems.capture('attempts','b'*32,manual=True,excerpt=excerpt)
        row=problems.capture('attempts','b'*32,manual=True,excerpt=excerpt)
        self.assertEqual(len(row['excerpts']),1);self.assertNotIn('private',row['excerpts'][0]['text'])
        with self.assertRaises(ValueError):problems.capture('attempts','b'*32,manual=True,excerpt={**excerpt,'text':'x'*8193})
        with self.assertRaisesRegex(ValueError,'确认依据'):problems.save(row['id'],{**row['notes'],'confirmed_cause':'猜测'},[])
        with self.assertRaises(ValueError):problems.save(row['id'],row['notes'],['NCORE=1'])

    def test_analysis_failures_are_not_backfilled_or_manually_collected(self):
        for kind,directory in {'analyses':'analyses-v2','local-analyses':'analyses','plots':'plots'}.items():
            path=self.root/directory/('f'*32);path.mkdir(parents=True)
            record={'id':'f'*32,'project_id':'project','state':'FAILED','error':'analysis error','manifest':{'name':'后处理工具'},'sources':[{'attempt_id':'a'*32}]}
            (path/'analysis.json').write_text(json.dumps(record))
            problems.try_capture(kind,record['id'],record)
            with self.assertRaisesRegex(ValueError,'仅登记'):
                problems.capture(kind,record['id'],record,manual=True)
        problems.backfill();rows=problems.listing()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['source_id'],'a'*32)
        self.assertEqual(rows[0]['category'],'计算任务')

    def test_wannier_calculations_are_included_but_other_attempt_types_are_not(self):
        self.add('b'*32,'FAILED',kind='wannier90')
        self.add('c'*32,'FAILED',kind='wannier90-plot')
        other=self.add('d'*32,'FAILED',kind='analysis')
        problems.try_capture('attempts',other['id'],other)
        problems.backfill();rows=problems.listing()
        self.assertEqual({r['source_id']for r in rows},{'a'*32,'b'*32,'c'*32})
        self.assertEqual({r['program']for r in rows},{'VASP','Wannier90'})
        with self.assertRaisesRegex(ValueError,'仅登记'):
            problems.capture('attempts',other['id'],other,manual=True)

    def test_old_analysis_notes_are_retained_but_cannot_be_listed_saved_or_exported(self):
        task=problems.capture('attempts','a'*32)
        key='f'*32
        legacy={**task,'id':key,'source_kind':'analyses','source_id':'e'*32,
                'program':'分析工具','notes':{**task['notes'],'user_note':'保留旧分析备注'}}
        with closing(problems.db())as c:
            c.execute('INSERT INTO problems VALUES(?,?,?,?)',(key,'analyses','e'*32,json.dumps(legacy)));c.commit()
        self.assertEqual([r['id']for r in problems.listing()],[task['id']])
        with self.assertRaisesRegex(ValueError,'仅登记'):problems.get(key)
        with self.assertRaisesRegex(ValueError,'仅登记'):problems.save(key,task['notes'],[])
        for format in ('json','markdown'):
            with self.assertRaisesRegex(ValueError,'仅登记'):problems.export([key],format)
        self.assertEqual(problems.raw(key),legacy)

    def test_notebook_failure_cannot_turn_execution_failure_into_unknown(self):
        record=ex.get('a'*32)
        with patch.object(problems,'capture',side_effect=OSError('disk unavailable')),self.assertLogs('backend.problem_records',level='ERROR'):
            problems.try_capture('attempts',record['id'],record)
        self.assertEqual(ex.get(record['id'])['state'],'FAILED')

    def test_manual_warning_later_failure_preserves_annotations_and_refreshes_facts(self):
        record = self.add('b'*32, 'RUNNING')
        row = problems.capture('attempts', record['id'], manual=True,
                               excerpt={'file':'OUTCAR','location':'预览第 2 行','text':'early warning'})
        row = problems.save(row['id'], {**row['notes'], 'user_note':'人工保留'}, ['NCORE'])
        record.update(state='FAILED', error='confirmed execution failure', evidence={'wrapper_exit':2})
        ex.save(record)
        updated = problems.get(row['id'])
        self.assertEqual((updated['state'], updated['collection']), ('FAILED', 'failure'))
        self.assertEqual(updated['error_summary'], 'confirmed execution failure')
        self.assertEqual(updated['evidence']['facts']['wrapper_exit'], 2)
        self.assertEqual(updated['notes']['user_note'], '人工保留')
        self.assertEqual(updated['incar_tags'], ['NCORE'])
        self.assertEqual(updated['excerpts'], row['excerpts'])
        self.assertEqual(len(problems.listing()), 2)


if __name__=='__main__':unittest.main()

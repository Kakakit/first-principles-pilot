import copy
import unittest
from tests import test_inputs as fixtures
from backend import inputs,action_issues,extension_protocol as protocol

class ActionIssueTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.InputTests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        self.project=copy.deepcopy(self.fixture.project)
    def test_input_blockers_keep_original_errors_and_precise_targets(self):
        draft=self.project['nodes'][1]['data']['inputDraft'];draft.update(grid='invalid',potcar_id='')
        report=inputs.generate(self.project,'c')
        self.assertFalse(report['ready']);self.assertEqual(len(report['issues']),len(report['errors']))
        issues={issue['code']:issue for issue in report['issues']}
        self.assertEqual(issues['input.kpoints']['target']['field'],'input.grid')
        self.assertEqual(issues['input.potcar']['target']['field'],'input.potcar')
        self.assertEqual(issues['input.potcar']['target']['nodeId'],'c')
        with self.assertRaises(action_issues.ActionBlocked) as caught:inputs.freeze(self.project,'c','reviewed')
        self.assertEqual(caught.exception.issues,report['issues'])
        self.assertIn('请先修正输入错误',action_issues.error_detail(caught.exception)['message'])
    def test_warnings_remain_nonblocking_and_outputs_stay_unchanged(self):
        report=inputs.generate(self.project,'c');self.assertTrue(report['warnings']);self.assertEqual(report['issues'],[]);self.assertTrue(report['ready'])
        self.assertIn('4 4 4',report['files']['KPOINTS'])
    def test_extension_schema_errors_point_to_actual_option_path(self):
        manifest={'options_schema':{'type':'object','properties':{'tolerance':{'type':'number','minimum':1}},'required':['tolerance']}}
        with self.assertRaises(action_issues.ActionBlocked) as caught:protocol.options_for(manifest,{'tolerance':0})
        self.assertEqual(caught.exception.issues[0]['target']['field'],'extension.options.tolerance')
        self.assertEqual(action_issues.error_detail(ValueError('legacy')),'legacy')

    def test_upstream_blocker_keeps_case_attempt_and_visible_owner(self):
        from backend import extension_runs
        manifest={'sources':{'min':1,'max':1},'execution':{'locations':['local']},'contexts':['attempt'],'stages':[],'evidence':['vasp_timing'],'inputs':[]}
        source={'kind':'attempt','name':'source case','node_id':'virtual-case','owner_node_id':'scan-card','attempt_id':'exact-history','stage':'scf','state':'COMPLETED','evidence':{}}
        issues=extension_runs.blockers(manifest,[source]).issues
        self.assertEqual(issues[0]['action'],'inspect')
        self.assertEqual(issues[0]['target']['nodeId'],'scan-card')
        self.assertEqual(issues[0]['target']['attemptId'],'exact-history')

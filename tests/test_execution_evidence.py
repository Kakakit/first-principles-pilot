import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import execution as ex
from backend.execution_evidence import electronic, read_record


class ElectronicEvidenceTests(unittest.TestCase):
    def test_finite_convergence_requires_both_deltas(self):
        for de, deps, expected in [('1e-9', '-1e-9', True), ('1e-3', '1e-9', False), ('1e-9', '1e-3', False)]:
            with self.subTest(de=de, deps=deps):
                result = electronic(f'FPILOT_EDIFF_NUMERIC=1e-8|3|{de}|{deps}\n')
                self.assertIs(result['ediff_reached'], expected)
                self.assertEqual(result['last_electronic_step'], 3)
                self.assertNotIn('numeric_error', result)
                json.dumps(result, allow_nan=False)

    def test_overflow_nan_and_invalid_numeric_never_claim_convergence(self):
        for token in ('-1e999', '1e999', '-Infinity', 'Infinity', 'NaN', '****', 'bad'):
            for position in range(3):
                with self.subTest(token=token, position=position):
                    values = ['1e-8', '1e-9', '1e-9']; values[position] = token
                    result = electronic(f'FPILOT_EDIFF=1\nFPILOT_EDIFF_NUMERIC={values[0]}|3|{values[1]}|{values[2]}\n')
                    self.assertIs(result['ediff_reached'], False)
                    self.assertIn('numeric_error', result)
                    self.assertIsNone(result[('ediff_value', 'last_delta_e', 'last_delta_eps')[position]])
                    json.dumps(result, allow_nan=False)

    def test_ediff_must_be_positive(self):
        for ediff in ('0', '-1e-8'):
            result = electronic(f'FPILOT_EDIFF_NUMERIC={ediff}|3|0|0\n')
            self.assertFalse(result['ediff_reached'])
            self.assertIn('numeric_error', result)

    def test_absent_numeric_evidence_keeps_old_marker_behavior(self):
        self.assertTrue(electronic('FPILOT_EDIFF=1\n')['ediff_reached'])
        self.assertFalse(electronic('')['ediff_reached'])
        self.assertIsNone(electronic('')['ediff_source'])

    def test_legacy_read_preserves_raw_receipt_and_identity(self):
        for value in (float('-inf'), float('inf'), float('nan')):
            raw = json.dumps(dict(id='original', state='RUNNING', job_id='25438.node01',
                status_receipt={'stdout':'FPILOT_EDIFF_NUMERIC=1e-8|3|-250.46|-Infinity'},
                evidence={'ediff_value':1e-8, 'last_delta_e':-250.46, 'last_delta_eps':value, 'ediff_reached':True}))
            result = read_record(raw)
            self.assertEqual(result['job_id'], '25438.node01')
            self.assertEqual(result['state'], 'RUNNING')
            self.assertEqual(result['status_receipt'], json.loads(raw)['status_receipt'])
            self.assertEqual(result['evidence']['last_delta_e'], -250.46)
            self.assertIsNone(result['evidence']['last_delta_eps'])
            self.assertIs(result['evidence']['ediff_reached'], False)
            json.dumps(result, allow_nan=False)

    def test_refresh_keeps_running_and_raw_receipt_with_overflow(self):
        record = dict(id='soc', state='RUNNING', job_id='25438.node01', config={'alias':'demo', 'scheduler':'pbs'})
        receipt = dict(exit_code=0, stdout='job_state = R\nFPILOT_EDIFF_NUMERIC=1e-8|3|-250.46|-1e999\n', stderr='')
        with patch.object(ex, 'get', return_value=record), patch.object(ex, 'assert_recovery_target'), \
             patch.object(ex, 'attempt_path', return_value={'shell':'/isolated/attempt'}), \
             patch.object(ex, 'ssh', return_value=receipt) as ssh, \
             patch.object(ex, 'save', side_effect=lambda row, **kw: row):
            result = ex.refresh('soc')
        self.assertEqual(result['state'], 'RUNNING')
        self.assertEqual(result['job_id'], '25438.node01')
        self.assertIsNone(result['evidence']['last_delta_eps'])
        self.assertFalse(result['evidence']['ediff_reached'])
        self.assertEqual(result['status_receipt'], receipt)
        self.assertNotIn('qsub', ssh.call_args.args[1])
        json.dumps(result, allow_nan=False)


class LegacyEvidenceApiTests(unittest.TestCase):
    def test_api_projections_serialize_preserve_database_and_do_not_connect(self):
        from starlette.responses import JSONResponse
        from backend import app as api
        with tempfile.TemporaryDirectory() as temp, patch.object(ex, 'ROOT', Path(temp)), \
             patch.object(ex, 'ssh', side_effect=AssertionError('no SSH')), \
             patch.object(ex.servers, 'probe', side_effect=AssertionError('no server probe')):
            raw = json.dumps(dict(id='legacy', project_id='p', node_id='soc', state='RUNNING',
                job_id='25438.node01', config={'alias':'demo'}, created_at='2026-10-21T00:00:00Z',
                evidence={'ediff_value':1e-8, 'last_delta_e':-250.46, 'last_delta_eps':float('-inf'), 'ediff_reached':False},
                status_receipt={'stdout':'raw -Infinity retained'}))
            with ex.db() as db:
                db.execute('INSERT INTO attempts VALUES(?,?,?)', ('legacy', 'RUNNING', raw))
            projections = [lambda: api.execution_history('p'),
                           lambda: api.execution_history('p', view='summary', node_id='soc'),
                           lambda: api.execution_detail('legacy', 'p'),
                           lambda: api.project_task_summary('p'),
                           lambda: api.node_attempts('p', 'soc', view='summary'),
                           lambda: api.node_attempts('p', 'soc')]
            for projection in projections:
                response = JSONResponse(projection())
                self.assertEqual(response.status_code, 200)
                json.dumps(json.loads(response.body), allow_nan=False)
                self.assertIn(b'25438.node01', response.body)
            with ex.db() as db:
                self.assertEqual(db.execute('SELECT body FROM attempts WHERE id=?', ('legacy',)).fetchone()[0], raw)


if __name__ == '__main__':
    unittest.main()

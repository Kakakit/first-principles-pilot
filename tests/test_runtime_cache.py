import copy
import unittest
from unittest.mock import patch

from backend import servers
import test_servers


class RuntimeCacheTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_servers.ServerTests()
        self.fixture.setUp()
        self.config = self.fixture.config

    def tearDown(self):
        self.fixture.tearDown()

    def test_live_capacity_changes_without_repeating_runtime_diagnostics(self):
        first = self.fixture.probe()
        capacity = '===== jutai-1 =====\nSCHEDULER=PBS/Torque\nNODE=node01 STATUS=BUSY CPUS_FREE=0 CPUS_TOTAL=48\n'
        queues = 'Queue: batch\n enabled = True\n started = True\n resources_max.walltime = 48:00:00\n'
        with patch.object(servers, 'run', return_value={'exit_code':0,'stdout':capacity+'FPILOT_DIAGNOSTICS_BEGIN\n'+queues,'stderr':''}) as run:
            second = servers.capacity_probe(self.config)
        self.assertEqual(second['runtime_checked_at'], first['observed_at'])
        self.assertEqual(second['diagnostic_mode'], 'live-capacity')
        self.assertNotIn('CHECK_vasp_executable', run.call_args.args[0][-1])
        self.assertFalse(servers.preview(self.config, second['id'], 'node01')['resource_ready'])
        self.assertEqual(run.call_count, 1)

    def test_configuration_change_and_runtime_invalidation_trigger_full_probe(self):
        self.fixture.probe()
        config = copy.deepcopy(self.config)
        config['vasp_executable'] = config['vasp_executable'].replace('vasp_std', 'vasp_ncl')
        self.assertFalse(servers._runtime_cache(config, 'vasp').exists())
        servers.invalidate_runtime(self.config)
        self.assertFalse(servers._runtime_cache(self.config, 'vasp').exists())
        with patch.object(servers, 'probe', return_value={'id': 'fresh'}) as probe:
            self.assertEqual(servers.capacity_probe(self.config)['id'], 'fresh')
        probe.assert_called_once_with(self.config)

    def test_standard_and_soc_are_checked_together_and_share_cycle_capacity(self):
        config = copy.deepcopy(self.config)
        config['vasp_executable'] = config['vasp_executable'].replace('vasp_std', 'vasp_ncl')
        capacity = '===== jutai-1 =====\nSCHEDULER=PBS/Torque\nNODE=node01 STATUS=FULLY_IDLE CPUS_FREE=48 CPUS_TOTAL=48\n'
        details = 'CHECK_mpi_executable=OK\nCHECK_vasp_executable=OK\nCHECK_environment_script=OK\nCHECK_vasp_std=OK\nCHECK_vasp_ncl=OK\nQueue: batch\n enabled = True\n started = True\n resources_max.walltime = 48:00:00\n'
        with patch.object(servers, 'run', return_value={'exit_code':0,'stdout':capacity+'FPILOT_DIAGNOSTICS_BEGIN\n'+details,'stderr':''}) as run:
            reports = servers.probe_many([self.config, config, self.config])
        self.assertEqual(len(reports), 2)
        self.assertEqual(run.call_count, 1)  # one capacity + one runtime SSH
        self.assertIn('CHECK_vasp_ncl', run.call_args.args[0][-1])
        context = {}
        with patch.object(servers, 'run', return_value={'exit_code':0,'stdout':capacity+'FPILOT_DIAGNOSTICS_BEGIN\n'+details,'stderr':''}) as run:
            servers.capacity_probe(self.config, context=context)
            servers.capacity_probe(config, context=context)
        self.assertEqual(run.call_count, 1)

    def test_offline_probe_invalidates_cached_runtime_and_blocks_dispatch(self):
        self.fixture.probe()
        bad = {'exit_code': 255, 'stdout': '', 'stderr': 'offline'}
        with patch.object(servers, 'run', return_value=bad):
            report = servers.capacity_probe(self.config)
        self.assertEqual(report['diagnostics']['exit_code'], 255)
        self.assertFalse(servers._runtime_cache(self.config, 'vasp').exists())

    def test_failed_forced_diagnostic_cannot_leave_an_old_runtime_cache(self):
        self.fixture.probe()
        bad = {'exit_code': 255, 'stdout': '', 'stderr': 'offline'}
        with patch.object(servers, 'run', return_value=bad):
            servers.probe(self.config, force=True)
        self.assertFalse(servers._runtime_cache(self.config, 'vasp').exists())


if __name__ == '__main__':
    unittest.main()

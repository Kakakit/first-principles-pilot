"""GPU telemetry and its node binding stay separate from allocation decisions."""
import base64
import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from backend import servers, job_templates

spec=importlib.util.spec_from_file_location('gpu_facts',Path(__file__).resolve().parents[1]/'resources/preflight/gpu_node_facts.py')
facts=importlib.util.module_from_spec(spec);spec.loader.exec_module(facts)

XML='''<nvidia_smi_log><gpu><minor_number>2</minor_number><uuid>GPU-a</uuid><product_name>Test GPU</product_name>
<compute_mode>Default</compute_mode><mig_mode><current_mig>Disabled</current_mig></mig_mode>
<fb_memory_usage><total>100 MiB</total><used>10 MiB</used><reserved>5 MiB</reserved><free>85 MiB</free></fb_memory_usage>
<utilization><gpu_util>0 %</gpu_util></utilization><processes><process_info><type>C</type></process_info>
<process_info><type>G</type></process_info></processes></gpu></nvidia_smi_log>'''

class GpuFactsTests(unittest.TestCase):
    def test_reserved_memory_is_occupied_and_zero_utilization_is_not_idle(self):
        devices,method=facts.xml_devices(XML)
        self.assertEqual(method,'NVIDIA_SMI_XML')
        self.assertEqual(devices[0]['memory_used_mib'],15)
        self.assertEqual(devices[0]['memory_free_mib'],85)
        self.assertEqual(devices[0]['utilization_percent'],0)
        self.assertEqual(devices[0]['compute_processes'],1)

    def test_missing_process_evidence_mig_and_unknown_type_are_not_idle(self):
        for text in (XML.replace('<processes>','<missing>').replace('</processes>','</missing>'),
                     XML.replace('<processes>','<processes>N/A'),
                     XML.replace('<type>C</type>','<type>Unknown</type>'),
                     XML.replace('<current_mig>Disabled','<current_mig>Enabled')):
            with self.subTest(text=text),self.assertRaises(ValueError):facts.xml_devices(text)

    def test_existing_nvml_failure_uses_one_xml_query_without_installing(self):
        from types import SimpleNamespace
        with patch.object(facts,'nvml_devices',side_effect=ImportError),patch.object(facts.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=XML)) as query:
            self.assertEqual(facts.collect()[0][0]['compute_processes'],1)
            query.assert_called_once_with(['nvidia-smi','-q','-x'],capture_output=True,text=True,timeout=12)

    def test_details_bind_only_to_exact_direct_node_and_bad_data_stays_readable(self):
        detail=base64.b64encode(json.dumps(facts.xml_devices(XML)[0]).encode()).decode()
        text='===== host =====\nSCHEDULER=PBS/Torque\nNODE=node01 STATUS=FULLY_IDLE CPUS_TOTAL=10 CPUS_FREE=10\nNODE=node02 STATUS=FULLY_IDLE CPUS_TOTAL=10 CPUS_FREE=10\n'
        rows=servers.parser.parse_preflight(text+'GPU_NODE=node02 GPU_QUERY=OK GPU_DETAIL_B64='+detail+'\n')['hosts'][0]['nodes']
        self.assertNotIn('GPU_DEVICES_JSON',rows[0]);self.assertEqual(json.loads(rows[1]['GPU_DEVICES_JSON'])[0]['index'],2)
        rows=servers.parser.parse_preflight(text+'GPU_NODE=node02 GPU_QUERY=OK GPU_DETAIL_B64=invalid\n')['hosts'][0]['nodes']
        self.assertIn('GPU_DETAIL_ERROR',rows[1]);self.assertNotIn('GPU_DEVICES_JSON',rows[1])

    def test_query_failure_remains_unknown(self):
        output=io.StringIO()
        with patch.object(facts,'collect',side_effect=ValueError),patch.object(facts.socket,'gethostname',return_value='node01'),patch('sys.stdout',output):facts.main()
        self.assertIn('GPU_QUERY=UNKNOWN',output.getvalue());self.assertNotIn('GPU_ACTIVE_PROCESSES=0',output.getvalue())

    def test_only_resource_derived_rank_assignment_changes(self):
        source='NPROCS=`wc -l < "$PBS_NODEFILE"`\nother="keep"\n'
        self.assertEqual(job_templates.render_gpu_ranks(source,2),'NPROCS=2\nother="keep"\n')
        self.assertEqual(job_templates.render_gpu_ranks('NPROCS=3\n',2),'NPROCS=3\n')

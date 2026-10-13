"""Exercise the real preflight shell parser with scheduler command fixtures."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from backend import servers

SCRIPT=Path(__file__).resolve().parents[1]/'resources/preflight/check_cluster_availability.sh'
BODY=SCRIPT.read_text().split("<<'REMOTE_CHECK'",1)[1].split('\n',1)[1].split('\nREMOTE_CHECK',1)[0]

class PreflightLoadTests(unittest.TestCase):
    def run_checker(self, scheduler, data, threshold='auto'):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name,text in ({'sinfo':'','scontrol':data,'squeue':''} if scheduler=='slurm' else {'pbsnodes':data,'qstat':''}).items():
                p=root/name;p.write_text('#!/bin/sh\ncat <<\'DATA\'\n'+text+'\nDATA\n');p.chmod(0o755)
            # Avoid an installed scheduler on the local machine influencing this fixture.
            code=BODY if scheduler=='slurm' else BODY.replace('if command -v scontrol','if false && command -v scontrol',1)
            env={**os.environ,'PATH':str(root)+':/usr/bin:/bin','UNTRACKED_LOAD_THRESHOLD':threshold,'MOM_MAX_AGE':'300','EXCLUDED_NODES':''}
            r=subprocess.run(['/bin/bash','-c',code],env=env,capture_output=True,text=True,timeout=5)
            self.assertEqual(r.returncode,0,r.stderr)
            rows=servers.parser.parse_preflight('===== fixture =====\n'+r.stdout)['hosts'][0]['nodes']
            self.assertTrue(rows,r.stdout+r.stderr)
            return rows

    def test_slurm_boundary_busy_fault_and_unknown_load(self):
        data='\n'.join('NodeName='+name+' CPUTot='+str(cpus)+' CPUAlloc='+str(allocated)+' State='+state+' CPULoad='+load for name,cpus,allocated,state,load in (
            ('residual',56,0,'IDLE','1.23'),('boundary',56,0,'IDLE','5.6'),('over',56,0,'IDLE','5.61'),
            ('master',64,0,'IDLE','120'),('small',8,0,'IDLE','1.0'),('busy',56,56,'ALLOCATED','0'),
            ('down',56,0,'DOWN*','0'),('missing',56,0,'IDLE','N/A')))
        rows={n['NODE']:n for n in self.run_checker('slurm',data)}
        for key in ('residual','boundary','small'):self.assertEqual(rows[key]['STATUS'],'FULLY_IDLE')
        for key in ('over','master','missing'):self.assertEqual(rows[key]['STATUS'],'SUSPECT_LOAD');self.assertEqual(rows[key]['CPUS_FREE'],'0')
        self.assertEqual(rows['master']['CPUS_UNALLOCATED'],'64');self.assertEqual(rows['master']['LOAD_LIMIT'],'6.40')
        self.assertEqual(rows['missing']['LOAD_VALID'],'0');self.assertEqual(rows['missing']['LOAD'],'unknown')
        self.assertEqual(rows['down']['STATUS'],'UNAVAILABLE');self.assertEqual(rows['busy']['STATUS'],'BUSY')
        with self.assertRaises(servers.selector.ResourceSelectionError):servers.selector.select_resources({'hosts':[{'host':'fixture','classification':'READY','nodes':[rows['master']]}]},'fixture','master')

    def test_pbs_same_threshold_and_mom_checks(self):
        import time
        now=int(time.time())
        data='\n\n'.join(f'{name}\n     state = {state}\n     np = 56\n     status = rectime={stamp},loadave={load}\n' for name,state,stamp,load in (
            ('residual','free',now,'1.23'),('over','free',now,'5.61'),('fault','down',now,'0'),
            ('stale','free',now-1000,'0'),('missing','free',now,'bad')))
        rows={n['NODE']:n for n in self.run_checker('pbs',data)}
        self.assertEqual(rows['residual']['STATUS'],'FULLY_IDLE');self.assertEqual(rows['residual']['LOAD_LIMIT'],'5.60')
        self.assertEqual(rows['over']['STATUS'],'SUSPECT_LOAD');self.assertEqual(rows['missing']['CPUS_FREE'],'0')
        self.assertEqual(rows['fault']['STATUS'],'UNAVAILABLE');self.assertEqual(rows['stale']['STATUS'],'UNAVAILABLE')

    def test_numeric_override_and_web_command_use_common_parser(self):
        data='NodeName=node CPUTot=56 CPUAlloc=0 State=IDLE CPULoad=1.23'
        self.assertEqual(self.run_checker('slurm',data,'1.0')[0]['STATUS'],'SUSPECT_LOAD')
        command=servers.availability_command({'alias':'fixture'})
        self.assertIn('UNTRACKED_LOAD_THRESHOLD=auto',command)
        self.assertIn('load_limit(total)',command)
        r=subprocess.run(['/bin/bash',str(SCRIPT),'fixture'],env={**os.environ,'UNTRACKED_LOAD_THRESHOLD':'not-a-number'},capture_output=True,text=True,timeout=5)
        self.assertEqual(r.returncode,2)

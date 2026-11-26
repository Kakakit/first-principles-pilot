import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from backend import ssh_onboarding as wizard, site_registration as sites


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.home=Path(temp.name)
        home=patch.object(Path,'home',return_value=self.home);home.start();self.addCleanup(home.stop)
        self.target={'host':'compute.invalid','user':'researcher','port':'2201'}
        self.jump={'host':'jump.invalid','user':'visitor','port':'2222'}

    def test_direct_and_jump_profiles_resolve_and_retry_without_overwriting(self):
        root=self.home/'.ssh';root.mkdir()
        path=root/'config';original=b'Host original\n HostName old.invalid\n'
        path.write_bytes(original)
        data={'target':self.target,'alias':'research','jump':self.jump}
        wizard.save_config(data)
        saved=path.read_bytes()
        self.assertTrue(saved.endswith(original))
        wizard.save_config(data);self.assertEqual(path.read_bytes(),saved)
        self.assertEqual(list(root.glob('config.before-*'))[0].read_bytes(),original)
        resolved=sites.check_ssh('research')['resolved']
        self.assertEqual((resolved['hostname'],resolved['user'],resolved['port'],resolved['proxyjump']),('compute.invalid','researcher','2201','research-jump'))
        self.assertEqual(sites.check_ssh('research-jump')['resolved']['port'],'2222')
        self.assertEqual(sites.check_ssh('original')['resolved']['hostname'],'old.invalid')
        with self.assertRaises(ValueError):wizard.save_config({**data,'target':self.jump})

    def test_password_not_accepted_and_input_cannot_change_config_shape(self):
        for value in ({'password':'do not record'},{'jump':{'host':'ok\nHost injected','port':'22','user':'x'}},{'alias':'-bad'}):
            with self.assertRaises(ValueError):wizard.profile({'alias':'new','target':self.target,**value})
        wizard.save_config({'alias':'new','target':self.target})
        self.assertNotIn('ProxyJump',(self.home/'.ssh/config').read_text())

    def test_fresh_verification_checks_both_hops_and_no_python_is_still_connected(self):
        wizard.save_config({'target':self.target,'alias':'research','jump':self.jump})
        def verify(argv,**kwargs):
            import subprocess
            config=argv[argv.index('-F')+1]
            for host in ('research','research-jump'):
                resolved=subprocess.check_output(['ssh','-G','-F',config,host],text=True,stderr=subprocess.DEVNULL)
                self.assertRegex(resolved,r'(?m)^batchmode (yes|true)$')
                self.assertRegex(resolved,r'(?m)^controlmaster (no|false)$')
                self.assertNotRegex(resolved,r'(?m)^controlpath (?!none$)')
            self.assertIn('FPILOT_CONNECTION_OK',argv[-1])
            return {'exit_code':0,'stdout':'FPILOT_SSH_OK\nFPILOT_CONNECTION_OK\n','stderr':''}
        with patch('backend.remote_transport.legacy',side_effect=verify) as call:
            result=sites.test_connection('research',fresh=True)
            self.assertTrue(result['connected']);self.assertIsNone(result['python'])
            call.assert_called_once()

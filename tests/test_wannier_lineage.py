import hashlib
import unittest
from contextlib import ExitStack
from unittest.mock import patch
from backend import wannier_lineage as lineage, wannier_bands as bands, wannier_execution as w90

POSCAR = b'test\n1\n3 0 0\n0 3 0\n0 0 3\nFe O\n1 1\nDirect\n0 0 0\n0.5 0.5 0.5\n'
ROUNDING = POSCAR.replace(b'0.5 0.5 0.5', b'0.5000001 0.5 0.5')

class WannierLineageTests(unittest.TestCase):
    def test_legacy_chain_and_audit_binding_without_mutation(self):
        r={'chain_source_snapshot':{'source_attempt_id':'scf','kind':'dependency','files':{'CONTCAR':{'sha256':'old'}}}}
        self.assertEqual(lineage.scf_id(r),'scf')
        self.assertEqual(lineage.scf_id({'input_audit':{'dependency_snapshot':r['chain_source_snapshot']}}),'scf')
        self.assertNotIn('dependency_snapshot',r)
        with self.assertRaisesRegex(ValueError,'冲突'):
            lineage.scf_id({**r,'dependency_snapshot':{'upstream_attempt_id':'other'}})
        self.assertIsNone(lineage.scf_id({'chain_source_snapshot':{'source_attempt_id':'scf','kind':'restart'}}))

    def proposal(self, parent_poscar=ROUNDING, scf='scf'):
        parent={'id':'interface','state':'COMPLETED','project_id':'p','config':{'alias':'host'},'dependency_snapshot':{'upstream_attempt_id':'scf','files':{'POSCAR':{'sha256':'new'}}}}
        band={'id':'band','state':'COMPLETED','project_id':'p','config':{'alias':'host'},'bundle_id':'bundle','chain_source_snapshot':{'source_attempt_id':scf,'kind':'dependency','files':{'CONTCAR':{'sha256':'old'}}}}
        self.parent,self.band=parent,band
        def hashes(alias,path,names):
            return {name:hashlib.sha256((parent_poscar if path=='/interface' else POSCAR) if name=='POSCAR' else b'kpoints').hexdigest() for name in names}
        with ExitStack() as stack:
            stack.enter_context(patch.object(w90,'_parent',return_value=(parent,{})))
            stack.enter_context(patch.object(bands.execution,'get',return_value=band))
            stack.enter_context(patch.object(bands.execution,'bundle',return_value=({'stage':'bands'},{})))
            stack.enter_context(patch.object(bands.execution,'attempt_path',side_effect=lambda r:{'shell':'/'+r['id']}))
            stack.enter_context(patch.object(bands.pyw90_remote,'_file_hashes',side_effect=hashes))
            stack.enter_context(patch.object(bands.artifacts,'_small_bytes',side_effect=lambda r,*a,**k:((parent_poscar,len(parent_poscar)) if r['id']=='interface' else (POSCAR,len(POSCAR)))))
            stack.enter_context(patch.object(bands.pyw90_remote,'_run',return_value={'arguments':['pyw90','pre','kpath'],'stdout':'begin kpoint_path\nG 0 0 0 X 0.5 0 0\nend kpoint_path','stderr':'','exit_code':0}))
            stack.enter_context(patch.object(bands.execution,'ssh',return_value={'exit_code':0,'stdout':'40'}))
            return bands.path_proposal({'id':'p'},'w','interface','host','band')

    def test_mixed_v1_contcar_v2_poscar_uses_actual_geometry(self):
        p=self.proposal()
        self.assertEqual(p['scf_attempt_id'],'scf')
        self.assertEqual(p['bands_num_points'],40)
        self.assertIn('parent_poscar_sha256',p)
        with self.assertRaisesRegex(ValueError,'同一 SCF'):
            self.proposal(scf='soc-scf')
        with self.assertRaisesRegex(ValueError,'实际结构不同'):
            self.proposal(POSCAR.replace(b'0.5 0.5 0.5',b'0.6 0.5 0.5'))
        with self.assertRaisesRegex(ValueError,'元素顺序'):
            self.proposal(POSCAR.replace(b'Fe O',b'O Fe'))

    def test_recheck_detects_parent_changed_and_supports_old_proposals(self):
        proposal=self.proposal()
        hashes={'KPOINTS':proposal['kpoints_sha256'],'POSCAR':proposal['poscar_sha256']}
        with ExitStack() as stack:
            stack.enter_context(patch.object(bands.execution,'get',side_effect=lambda k:self.band if k=='band' else self.parent))
            stack.enter_context(patch.object(bands.execution,'attempt_path',side_effect=lambda r:{'shell':'/'+r['id']}))
            stack.enter_context(patch('backend.recycle.assert_available'))
            stack.enter_context(patch('backend.remote_cache.read',return_value=None))
            stack.enter_context(patch('backend.remote_cache.write',side_effect=lambda *a:a[-1]))
            mock=stack.enter_context(patch.object(bands.pyw90_remote,'_file_hashes',side_effect=[hashes,{'POSCAR':'changed'}]))
            with self.assertRaisesRegex(ValueError,'实际结构已变化'):bands.recheck(proposal)
            old={k:v for k,v in proposal.items() if k not in ('parent_poscar_sha256','parent_source_path')}
            mock.side_effect=None;mock.return_value=hashes
            self.assertTrue(bands.recheck(old)['validated'])

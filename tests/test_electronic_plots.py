"""The bounded server-side tables remain usable by the bundled local plotter."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from backend import electronic_plots


class BoundedPlotTests(unittest.TestCase):
    def test_noncollinear_dos_uses_charge_projection_only(self):
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'DOSCAR'
            header='1\n0\n0\nCAR\nSi\n5 -5 2 0 1\n'
            total='-1 1 0\n0 2 0\n'
            block='5 -5 2 0 1\n'
            # s, p, d: charge, mx, my, mz for each orbital.
            projected='-1 0.1 9 9 9 0.2 9 9 9 0.3 9 9 9\n0 0.1 9 9 9 0.2 9 9 9 0.4 9 9 9\n'
            source.write_text(header+total+block+projected)
            receipt=subprocess.run([sys.executable,str(electronic_plots.EXTRACTOR),'doscar',str(source),
                                    'all','d','noncollinear'],capture_output=True,text=True,check=True)
            lines=receipt.stdout.splitlines()
            self.assertIn('"projection_component": "charge"',lines[0])
            self.assertEqual(lines[1:3],['-1 1 0.3','0 2 0.4'])

    def test_fixture_tables_generate_local_figures(self):
        fixture = Path(__file__).parent / 'fixtures/banddos'
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for kind, source, plot, extra in (
                ('doscar', 'DOSCAR', 'dos', ['--labels', 'Total up,Total down,PDOS up,PDOS down', '--fermi', '0']),
                ('procar', 'PROCAR', 'fatband', ['--x-col', '2', '--energy-col', '3', '--weight-col', '4']),
            ):
                extract = subprocess.run([sys.executable, str(electronic_plots.EXTRACTOR), kind,
                                          str(fixture / source), 'all', 'd'] + (['0'] if kind == 'procar' else []),
                                         capture_output=True, text=True, timeout=30, check=True)
                lines = extract.stdout.splitlines()
                self.assertTrue(lines[0].startswith('FPILOT_META='))
                self.assertEqual(lines[-1], 'FPILOT_END')
                (directory / 'plot-table.dat').write_text('\n'.join(lines[1:-1]) + '\n')
                render = subprocess.run([sys.executable, str(electronic_plots.SCRIPT), plot,
                                         '--table', 'plot-table.dat', *extra,
                                         '--formats', 'png,pdf', '--output', kind], cwd=directory,
                                        capture_output=True, text=True, timeout=90)
                self.assertEqual(render.returncode, 0, render.stderr)
                self.assertTrue((directory / (kind + '.png')).is_file())
                self.assertTrue((directory / (kind + '.pdf')).is_file())


if __name__ == '__main__':
    unittest.main()

"""Process-local pre DOS compatibility adapter; also executable via SSH -c.

Reuse pymatgen's EIGENVAL reader without changing PyW90 or research inputs.
"""
import json
import os
import sys

REVISION = 'fpilot.pyw90-manual.v3'


def validate_layout(path):
    """Reject truncation: older pymatgen versions leave unread bands as zero."""
    with open(path, encoding='utf-8') as stream:
        ispin = int(stream.readline().split()[-1])
        if ispin not in (1, 2):
            raise ValueError('EIGENVAL 自旋通道数无效')
        for _ in range(4):
            stream.readline()
        _, nkpts, nbands = map(int, stream.readline().split())
        if nkpts < 1 or nbands < 1:
            raise ValueError('EIGENVAL k 点或能带数无效')
        for _ in range(nkpts):
            line = stream.readline()
            while line and not line.strip():
                line = stream.readline()
            if len(line.split()) != 4:
                raise ValueError('EIGENVAL k 点块不完整')
            for band in range(1, nbands + 1):
                row = stream.readline().split()
                if len(row) != (5 if ispin == 2 else 3) or int(row[0]) != band:
                    raise ValueError('EIGENVAL 能带行缺失、错序或通道格式不符')
        if any(line.strip() for line in stream):
            raise ValueError('EIGENVAL 包含超出声明数量的数据')


def install(spin_down=False, nwann_override=None):
    import numpy as np
    import pandas as pd
    from pymatgen.electronic_structure.core import Spin
    from pymatgen.io.vasp.outputs import Eigenval
    from pyw90.lib.w90 import W90
    from pyw90.lib.dos import DOS
    from pyw90 import pre_w90_tool

    # Keep each numerical table in one block for the SSH receipt parser.
    pd.set_option('display.expand_frame_repr', False)
    pd.set_option('display.max_columns', None)
    pd.set_option('display.max_rows', None)
    pd.set_option('display.precision', 10)

    spin = Spin.down if spin_down else Spin.up
    original = W90.read_eigenval
    original_features = pre_w90_tool.main_features
    original_vasprun = pre_w90_tool.Vasprun
    original_analysis = DOS.get_dos_analysis_df
    original_frame = DOS.get_dos_df
    context = {'spinors': False, 'channels': None}

    def features(args):
        def vasprun(*positional, **keywords):
            parsed = original_vasprun(*positional, **keywords)
            context['spinors'] = bool(parsed.parameters.get('LSORBIT') or
                                      parsed.parameters.get('LNONCOLLINEAR'))
            # Noncollinear XML has charge, mx, my, mz sets. Older pymatgen
            # exposes charge as Spin.up and the last magnetic set as Spin.down;
            # those keys do not represent two eigenvalue channels.
            context['channels'] = 1 if context['spinors'] else len(parsed.tdos.densities)
            if (context['spinors'] and spin_down) or spin not in parsed.tdos.densities:
                raise ValueError('所选 DOS 为单通道（含 SOC/非共线），不能选择 spin-down')
            # Each spatial orbital supplies two spinor projections. Do not
            # assert Kramers degeneracy: the actual SOC bands stay individual.
            if context['spinors']:
                args.deg = 2
            return parsed
        pre_w90_tool.Vasprun = vasprun
        try:
            return original_features(args)
        finally:
            pre_w90_tool.Vasprun = original_vasprun

    def read_eigenval(self):
        if self.eig.endswith('.eig'):
            return original(self)
        path = os.path.join(self.path, self.eig)
        validate_layout(path)
        data = Eigenval(path)
        if context['channels'] is not None and data.ispin != context['channels']:
            raise ValueError('DOS 与 EIGENVAL 自旋通道数不一致')
        if context['spinors'] and data.ispin != 1:
            raise ValueError('SOC/非共线 DOS 与 EIGENVAL 通道格式不一致')
        if spin not in data.eigenvalues:
            raise ValueError('所选 EIGENVAL 为单通道（含 SOC/非共线），不能选择 spin-down')
        values = data.eigenvalues[spin]
        if (data.nkpt < 1 or data.nbands < 1 or
                values.shape != (data.nkpt, data.nbands, 2) or
                not np.isfinite(values).all() or
                np.shape(data.kpoints) != (data.nkpt, 3) or
                not np.isfinite(data.kpoints).all() or
                np.shape(data.kpoints_weights) != (data.nkpt,) or
                not np.isfinite(data.kpoints_weights).all()):
            raise ValueError('EIGENVAL 数据不完整或含非有限值')
        # PyW90's window methods operate on slot zero. Select, never flatten,
        # the requested collinear channel; ISPIN=1 includes spinor SOC bands.
        self.nspin = 1
        self.nelect = data.nelect
        self.ir_nkpts = data.nkpt
        self.nbnds = data.nbands
        self.ir_kpath = np.asarray(data.kpoints)
        self.ir_kptwt = np.asarray(data.kpoints_weights)
        self.ir_ebands = values[:, :, 0][None, :, :]
        self.emax = self.ir_ebands.max()
        self.emin = self.ir_ebands.min()
        self.eband_max = self.ir_ebands.max(axis=1)[0]
        self.eband_min = self.ir_ebands.min(axis=1)[0]
        suggested = self.nwann
        if nwann_override is not None:
            self.nwann = nwann_override
        print('FPILOT_EIGENVAL=' + json.dumps({
            'adapter': REVISION, 'source_ispin': data.ispin,
            'channel': ('down' if spin_down else 'up') if data.ispin == 2 else 'single',
            'density_channel': 'charge' if context['spinors'] else ('down' if spin_down else 'up'),
            'spinors': context['spinors'], 'nkpts': data.nkpt, 'nbands': data.nbands,
            'suggested_num_wann': suggested, 'window_num_wann': self.nwann}))

    def dos_frame(cls, complete_dos, left, right, spin=Spin.up):
        frame = original_frame(complete_dos, left, right, spin=spin)
        # Native output has already divided by the largest orbital integral.
        # Retain the unnormalised integrals using the same native integrator.
        frame['integral'] = [cls.get_dos_given_site_orb(complete_dos, (left,right), key, spin=spin)
                             for key in frame['key_string']]
        return frame

    def dos_analysis(cls, frame, lb=0.1):
        if frame.empty or not np.isfinite(frame['dos']).all():
            raise ValueError('当前 DOS 范围没有可归一化的轨道积分；请核对投影数据和能量范围')
        count, simplified = original_analysis(frame, lb=lb)
        threshold = float(frame['dos'].max()) * lb
        rows = frame.copy()
        rows['selected'] = rows['dos'] > threshold
        print('FPILOT_DOS_SELECTION=' + json.dumps({
            'lb': lb, 'threshold': threshold, 'spatial_orbitals': int(count),
            'suggested_num_wann': int(count * (2 if context['spinors'] else 1)),
            'spinors': context['spinors'],
            'normalization_integral': float(frame['integral'].max()) if 'integral' in frame else None,
            'channel': 'charge' if context['spinors'] else ('down' if spin_down else 'up') if context['channels'] == 2 else 'single',
            'orbitals': json.loads(rows.to_json(orient='records'))}))
        return count, simplified

    def selected_dos(cls, complete_dos, erange, selected):
        # Upstream 1.0.7 otherwise defaults each selected orbital to Spin.up.
        return np.sum([cls.get_dos_given_site_orb(complete_dos, erange, key, spin=spin)
                       for key in selected])

    W90.read_eigenval = read_eigenval
    DOS.get_dos_from_selected = classmethod(selected_dos)
    DOS.get_dos_df = classmethod(dos_frame)
    DOS.get_dos_analysis_df = classmethod(dos_analysis)
    pre_w90_tool.main_features = features


def main():
    if sys.argv[1] == 'window-check':
        print('FPILOT_WINDOW_CHECK=' + json.dumps(window_check(json.loads(sys.argv[2])), allow_nan=False))
        return
    override = None
    if '--fpilot-num-wann' in sys.argv:
        i = sys.argv.index('--fpilot-num-wann')
        override = int(sys.argv[i + 1])
        del sys.argv[i:i + 2]
    if sys.argv[1:3] == ['pre', 'dos']:
        install('--spin-down' in sys.argv[3:], override)
    else:
        import pandas as pd
        pd.set_option('display.expand_frame_repr', False)
        pd.set_option('display.max_columns', None)
        pd.set_option('display.max_rows', None)
        pd.set_option('display.precision', 10)
    from pyw90.pyw90_cli import main_cli
    main_cli()

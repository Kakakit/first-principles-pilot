"""Process-local pre DOS compatibility adapter; also executable via SSH -c.

Reuse pymatgen's EIGENVAL reader without changing PyW90 or research inputs.
"""
import json
import os
import sys

REVISION = 'fpilot.pyw90-manual.v4'


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


def install(spin_down=False, nwann_override=None, integration_method='spline'):
    import numpy as np
    import pandas as pd
    from pymatgen.electronic_structure.core import Spin
    from pymatgen.io.vasp.outputs import Eigenval
    from pyw90.lib.w90 import W90
    from pyw90.lib.dos import DOS
    from pyw90 import pre_w90_tool
    integrator_class=globals().get('DosIntegrator')
    if integrator_class is None:from .pyw90_dos import DosIntegrator as integrator_class
    integrator=integrator_class(integration_method)
    DOS.get_dos_integral=staticmethod(integrator)

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
    context = {'spinors': False, 'channels': None}
    parsed_xml={};parsed_eigenvalues={}

    def load_vasprun(*positional, **keywords):
        key=repr((positional,sorted(keywords.items())))
        if key not in parsed_xml:parsed_xml[key]=original_vasprun(*positional, **keywords)
        parsed=parsed_xml[key]
        context['spinors']=bool(parsed.parameters.get('LSORBIT') or parsed.parameters.get('LNONCOLLINEAR'))
        context['channels']=1 if context['spinors'] else len(parsed.tdos.densities)
        if (context['spinors'] and spin_down) or spin not in parsed.tdos.densities:
            raise ValueError('所选 DOS 为单通道（含 SOC/非共线），不能选择 spin-down')
        return parsed

    def features(args):
        def vasprun(*positional, **keywords):
            parsed = load_vasprun(*positional, **keywords)
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
        if path not in parsed_eigenvalues:
            validate_layout(path);parsed_eigenvalues[path]=Eigenval(path)
        data = parsed_eigenvalues[path]
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
        rows=[]
        for index,site in enumerate(complete_dos.structure):
            for orbital,densities in sorted(complete_dos.pdos.get(site,{}).items(),key=lambda item:item[0].value):
                if spin not in densities:continue
                value=integrator(complete_dos.energies,densities[spin],(left,right))
                species=site.species_string
                rows.append({'species':species,'structure_id':index,'orb_id':orbital.value,'orb_name':orbital.name,
                             'key_string':f'{species}_{index}_{orbital.name}','integral':value,'dos':value})
        if not rows or max(row['integral'] for row in rows)<=0:raise ValueError('当前 DOS 范围没有可归一化的轨道积分；请核对投影数据和能量范围')
        frame=pd.DataFrame(rows);frame['dos']=frame['dos']/frame['integral'].max()
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
    return {'load':load_vasprun,'context':context,'integrator':integrator}


def dos_report(request):
    """One XML parse for projection suggestion, fixed-model candidates and manual window."""
    import io
    from contextlib import redirect_stdout
    import numpy as np
    from pymatgen.electronic_structure.core import Spin
    from pyw90.lib.dos import DOS
    from pyw90.lib.w90 import W90
    keys_for=globals().get('projection_keys');ratio_for=globals().get('dos_ratio')
    if keys_for is None:from .pyw90_dos import projection_keys as keys_for,dos_ratio as ratio_for
    down=request.get('channel')=='down';spin=Spin.down if down else Spin.up
    state=install(down,request.get('num_wann'),request.get('integration_method','spline'))
    parsed=state['load']('vasprun.xml',parse_eigen=False,parse_projected_eigen=False,parse_potcar_file=False)
    complete=parsed.complete_dos;lo,hi=request['absolute_range']
    output=io.StringIO()
    with redirect_stdout(output):
        frame=DOS.get_dos_df(complete,lo,hi,spin=spin)
        _,simple=DOS.get_dos_analysis_df(frame)
    marker=next(line.split('=',1)[1] for line in output.getvalue().splitlines() if line.startswith('FPILOT_DOS_SELECTION='))
    selection=json.loads(marker)
    result={'projections':DOS.get_projections_w90_str(simple,complete.structure).strip().splitlines(),
            'dos_selection':selection,'frozen':[],'outer':[],'numerics':state['integrator'].metadata()}
    if request.get('num_wann') is None:return result
    manual=request.get('projection_selection','').strip()
    lines=[s.strip() for s in manual.splitlines() if s.strip()] if manual else request.get('projection_lines',[])
    try:keys=keys_for(lines,frame)
    except ValueError as exc:
        result['projection_basis']={'status':'UNAVAILABLE','source':'manual' if manual else 'current_model','reason':str(exc),'lines':lines}
        return result
    result['projection_basis']={'status':'READY','source':'manual' if manual else 'current_model','lines':lines,'keys':keys}
    # Parsing and validating EIGENVAL happens once; full interface .eig checks remain separate.
    with redirect_stdout(output):obj=W90(eig='EIGENVAL',path='.',nwann=request['num_wann'])
    eigen_marker=next(line.split('=',1)[1] for line in output.getvalue().splitlines() if line.startswith('FPILOT_EIGENVAL='))
    result['eigenval']=json.loads(eigen_marker)
    def measure(left,right):
        pi=float(DOS.get_dos_from_selected(complete,(left,right),keys))
        ti=float(DOS.get_dos_integral(parsed.tdos.energies,parsed.tdos.densities[spin],(left,right)))
        return ratio_for(pi,ti)
    with redirect_stdout(output):candidates=obj.get_dis_froz_df((lo,hi))
    for row in candidates.to_dict(orient='records'):
        result['frozen'].append({**row,**measure(row['dis_froz_min'],row['dis_froz_max'])})
    result['frozen'].sort(key=lambda row:-(row['percent'] if row['percent'] is not None else -float('inf')))
    windows=request.get('absolute_windows') or {}
    if all(tag in windows for tag in ('dis_froz_min','dis_froz_max')):
        result['current_dos']={'dis_froz_min':windows['dis_froz_min'],'dis_froz_max':windows['dis_froz_max'],
                               **(measure(windows['dis_froz_min'],windows['dis_froz_max']) if windows['dis_froz_min']<windows['dis_froz_max'] else
                                  {'pdos':None,'tdos':None,'percent':None,'warning':'当前冻窗上下界无效；候选仍可独立查看'})}
    with redirect_stdout(output):upper=obj.suggest_win_max(lo)
    if upper is not None and np.isfinite(upper) and upper>lo and np.min(np.sum((obj.ir_ebands[0]>=lo)&(obj.ir_ebands[0]<=upper),axis=1))>=request['num_wann']:
        result['minimum_outer_max']=float(upper)
    result['numerics']=state['integrator'].metadata()
    return result


def runtime_info():
    import pyw90
    from pathlib import Path
    from importlib.metadata import distribution, PackageNotFoundError
    version=getattr(pyw90,'__version__',None)
    try:
        installed=distribution('pyw90')
        if not version and Path(installed.locate_file('pyw90/__init__.py')).resolve()==Path(pyw90.__file__).resolve():
            version=installed.version
    except (PackageNotFoundError,TypeError,AttributeError):pass
    return {'version':version,'python':sys.executable,'module_file':getattr(pyw90,'__file__',None),
            'adapter_revision':REVISION}


def main():
    if sys.argv[1]!='window-check':
        print('FPILOT_PYW90_RUNTIME='+json.dumps(runtime_info()),flush=True)
    if sys.argv[1]=='dos-report':
        print('FPILOT_DOS_REPORT='+json.dumps(dos_report(json.loads(sys.argv[2])),allow_nan=False))
        return
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

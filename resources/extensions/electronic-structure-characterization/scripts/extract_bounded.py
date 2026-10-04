"""Stream a bounded DOS/PROCAR plotting table from a large remote VASP file.

This script uses only Python's standard library. It is sent through SSH stdin,
reads one existing file, and writes no remote files.
"""
import hashlib
import json
import re
import sys

MAX_ROWS = 100000


def selected_ions(text):
    if text == 'all':
        return None
    result = set()
    for part in text.split(','):
        bounds = part.split('-')
        if len(bounds) == 1:
            result.add(int(bounds[0]))
        elif len(bounds) == 2:
            result.update(range(int(bounds[0]), int(bounds[1]) + 1))
        else:
            raise ValueError('invalid ion selector')
    return result


def selected_orbitals(names, text):
    wanted = text.lower().split(',')
    indexes = {i for i, name in enumerate(names) if any(
        token == 'all' or name.lower() == token or token in ('p', 'd', 'f') and name.lower().startswith(token)
        for token in wanted)}
    if not indexes:
        raise ValueError('requested orbitals absent: ' + ','.join(names))
    return sorted(indexes)


def source_hash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def doscar(path, ions, orbitals, noncollinear=False):
    picked = selected_ions(ions)
    with open(path, encoding='utf-8', errors='replace') as source:
        for _ in range(5):
            next(source)
        header = next(source).split()
        nedos, fermi = int(float(header[2])), float(header[3])
        if nedos > MAX_ROWS:
            raise ValueError('DOS energy grid exceeds bounded extraction limit')
        total = [[float(value) for value in next(source).split()] for _ in range(nedos)]
        if len(total[0]) not in (3, 5):
            raise ValueError('unsupported total DOS layout')
        spin = len(total[0]) == 5
        sums = [[0.0, 0.0] for _ in range(nedos)]
        blocks = 0
        for line in source:
            if not line.strip():
                continue
            fields = line.split()
            if len(fields) < 3:
                break
            try:
                count = int(float(fields[2]))
            except ValueError:
                break
            blocks += 1
            if count != nedos:
                raise ValueError('projected DOS energy grid differs')
            rows = [[float(value) for value in next(source).split()] for _ in range(count)]
            if picked is not None and blocks not in picked:
                continue
            columns = len(rows[0]) - 1
            names = {3:['s','p','d'],9:['s','py','pz','px','dxy','dyz','dz2','dxz','dx2-y2'],
                     16:['s','py','pz','px','dxy','dyz','dz2','dxz','dx2-y2','f1','f2','f3','f4','f5','f6','f7']}
            basis = columns // 4 if noncollinear else columns // 2 if columns not in names and columns % 2 == 0 else columns
            if basis not in names:
                raise ValueError('unsupported projected DOS layout')
            orbital_indexes = selected_orbitals(names[basis], orbitals)
            collinear = columns == basis * 2
            if noncollinear and columns != basis * 4:
                raise ValueError('noncollinear DOSCAR requires total,mx,my,mz per orbital')
            for index, row in enumerate(rows):
                if noncollinear:
                    sums[index][0] += sum(row[1 + 4 * oi] for oi in orbital_indexes)
                elif collinear:
                    sums[index][0] += sum(row[1 + 2 * oi] for oi in orbital_indexes)
                    sums[index][1] += sum(row[2 + 2 * oi] for oi in orbital_indexes)
                else:
                    sums[index][0] += sum(row[1 + oi] for oi in orbital_indexes)
        if not blocks or picked is not None and max(picked) > blocks:
            raise ValueError('selected ion absent from DOSCAR')
    print('FPILOT_META=' + json.dumps({'fermi_eV': fermi, 'spin': spin, 'noncollinear':noncollinear,
                                      'projection_component':'charge', 'sha256':source_hash(path), 'kind':'doscar'}))
    for row, projected in zip(total, sums):
        values = [row[0], row[1], *( [row[2]] if spin else [] ), projected[0], *( [projected[1]] if spin else [] )]
        print(' '.join(format(value, '.10g') for value in values))
    print('FPILOT_END')


def procar(path, ions, orbitals, fermi):
    picked = selected_ions(ions)
    number = re.compile(r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?')
    spin, k, band, energy, weight = 1, 0, 0, None, 0.0
    labels, orbital_indexes = [], []
    seen = set()
    records = []
    previous_k = 0
    explicit_spin = False
    def finish():
        if energy is not None and abs(energy-fermi) <= 6 and (spin,k,band) in seen:
            if len(records) >= MAX_ROWS:
                raise ValueError('selected PROCAR records exceed bounded extraction limit')
            records.append((spin,k,energy,weight))
    with open(path, encoding='utf-8', errors='replace') as source:
        for raw in source:
            line = raw.strip()
            match = re.match(r'spin component\s+(\d+)',line,re.I)
            if match:
                finish();spin=int(match.group(1));explicit_spin=True;energy=None;continue
            match = re.match(r'k-point\s+(\d+)\s*:',line,re.I)
            if match:
                finish();next_k=int(match.group(1))
                if next_k == 1 and previous_k > 1 and not explicit_spin:
                    spin += 1
                k=next_k;previous_k=k;energy=None;continue
            match = re.match(r'band\s+(\d+).*energy\s+([-+0-9.Ee]+)',line,re.I)
            if match:
                finish();band=int(match.group(1));energy=float(match.group(2));weight=0.0;continue
            if line.startswith('ion ') and energy is not None:
                labels=line.split()[1:]
                if labels and labels[-1].lower()=='tot':labels=labels[:-1]
                orbital_indexes=selected_orbitals(labels,orbitals)
                table_key=(spin,k,band)
                if table_key not in seen:seen.add(table_key)
                else:orbital_indexes=[]
                continue
            fields=line.split()
            if energy is not None and orbital_indexes and fields and fields[0].isdigit():
                ion=int(fields[0])
                if picked is None or ion in picked:
                    weight += sum(float(fields[1+index]) for index in orbital_indexes)
    finish()
    if not records:
        raise ValueError('no selected PROCAR weights')
    print('FPILOT_META=' + json.dumps({'fermi_eV':fermi,'sha256':source_hash(path),'kind':'procar','spins':sorted({row[0] for row in records})}))
    for spin,k,energy,weight in records:
        print(f'{spin} {k} {energy-fermi:.10g} {weight:.10g}')
    print('FPILOT_END')


if __name__ == '__main__':
    try:
        kind, path, ions, orbitals, *rest = sys.argv[1:]
        if kind == 'doscar':
            doscar(path, ions, orbitals, bool(rest and rest[0] == 'noncollinear'))
        elif kind == 'procar':
            procar(path, ions, orbitals, float(rest[0]))
        else:
            raise ValueError('unknown input kind')
    except (OSError, ValueError, IndexError, StopIteration) as exc:
        print('FPILOT_ERROR=' + str(exc)[:500])
        raise SystemExit(2)

"""Read-only chain benchmark; run from the repository root with registered Python."""
import argparse
import base64
import json
import math
import sqlite3
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from backend import chain_runs, inputs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--project-id')
    parser.add_argument('--runs', type=int, default=60)
    args = parser.parse_args()
    if not 10 <= args.runs <= 1000:
        parser.error('runs must be 10–1000')
    root = args.workspace.resolve()
    with sqlite3.connect((root/'index.sqlite3').as_uri()+'?mode=ro', uri=True) as db:
        revision = db.execute('SELECT revision FROM current WHERE id=1').fetchone()[0]
        digest = db.execute('SELECT digest FROM revisions WHERE revision=?', (revision,)).fetchone()[0]
    workspace = json.loads((root/'snapshots'/digest).read_bytes())
    project = next(item for item in workspace['projects'] if item['id'] == (args.project_id or workspace['activeId']))
    for node in project['nodes']:
        structure = node['data'].get('structure')
        if structure and 'base64' not in structure['source']:
            structure['source']['base64'] = base64.b64encode((root/'originals'/structure['source']['sha256']).read_bytes()).decode()
    ids = [item['id'] for item in project['nodes'] if item['data']['kind'] in chain_runs.KINDS]
    inputs.ROOT = root
    samples = []
    for index in range(args.runs+3):
        start = time.perf_counter()
        report = chain_runs.preview(project, ids)
        elapsed = (time.perf_counter()-start)*1000
        if not report['ready']:
            raise ValueError('Benchmark project inputs must pass preview')
        if index >= 3:
            samples.append(elapsed)
    print(json.dumps({'project_id': project['id'], 'workspace_revision': revision,
                      'node_count': len(ids), 'warmups': 3, 'samples': len(samples),
                      'median_ms': round(statistics.median(samples), 3),
                      'p95_ms': round(sorted(samples)[math.ceil(len(samples)*.95)-1], 3),
                      'min_ms': round(min(samples), 3), 'max_ms': round(max(samples), 3),
                      'milliseconds': [round(value, 3) for value in samples]}, ensure_ascii=False))


if __name__ == '__main__':
    main()

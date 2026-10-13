"""Compare full clean-clone regressions without exporting private test setup.

Legacy tests require developer-only server registration/examples. Their existing
failures are reported, never relabelled as passes. Any newly failing test blocks
the installer release. Native installation/lifecycle checks remain mandatory.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


def one(root, receipt):
    root=root.resolve();sys.path.insert(0,str(root))
    suite=unittest.defaultTestLoader.discover(str(root/'tests'),pattern='test_*.py')
    result=unittest.TextTestRunner(verbosity=1).run(suite)
    failed=[test.id() for test,_ in result.failures]
    errors=[test.id() for test,_ in result.errors]
    value={'tests':result.testsRun,'failures':sorted(set(failed)),'errors':sorted(set(errors)),
           'skipped':[test.id() for test,_ in result.skipped],'whole_suite_green':result.wasSuccessful()}
    receipt.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',type=Path)
    parser.add_argument('--root',type=Path,default=Path.cwd())
    parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--one',action='store_true')
    args=parser.parse_args()
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    if args.one:
        one(args.root,args.receipt);return
    if args.baseline is None:parser.error('--baseline is required')
    records={}
    for kind,root in (('baseline',args.baseline),('current',args.root)):
        receipt=args.receipt.with_name(kind+'-full-regression.json').resolve()
        log=args.receipt.with_name(kind+'-full-regression.log').resolve()
        with log.open('w',encoding='utf-8') as stream:
            subprocess.run([sys.executable,str(Path(__file__).resolve()),'--one','--root',str(root.resolve()),
                '--receipt',str(receipt)],cwd=root,check=True,stdout=stream,stderr=stream,
                env={**os.environ,'FPILOT_WORKSPACE':str(args.receipt.parent.resolve()/('test-data-'+kind)),
                     'FPILOT_NOTIFICATIONS':'0'})
        records[kind]=json.loads(receipt.read_text())
    previous=set(records['baseline']['failures']+records['baseline']['errors'])
    current=set(records['current']['failures']+records['current']['errors'])
    new=sorted(current-previous)
    summary={'schema':'fpilot.clean-regression.v1','status':'BLOCKED' if new else 'NO_NEW_FAILURES',
        'whole_suite_green':records['current']['whole_suite_green'],'new_failures':new,
        'existing_failures':sorted(current & previous),'baseline':records['baseline'],'current':records['current'],
        'note':'Existing environment-dependent failures remain failures. Native installer smoke checks must pass separately.'}
    args.receipt.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k not in ('baseline','current','existing_failures')},ensure_ascii=False))
    print('Baseline/current cases:',records['baseline']['tests'],records['current']['tests'],
          '; existing failing cases:',len(current & previous),'; new failures:',len(new))
    if new:raise SystemExit(1)


if __name__=='__main__':main()

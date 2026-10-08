"""Read GPU inventory/processes only on the directly connected compute node.

The parser associates this report only with a matching scheduler NODE. This
does not equate physical inventory or zero utilization with free allocation.
"""
import shutil
import socket
import subprocess


def query(fields, kind):
    result = subprocess.run(['nvidia-smi', '--query-' + kind + '=' + fields,
                             '--format=csv,noheader,nounits'], capture_output=True,
                            text=True, timeout=12)
    if result.returncode: raise ValueError('GPU read-only query failed')
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def main():
    node = socket.gethostname().split('.')[0]
    if not shutil.which('nvidia-smi'):
        print('GPU_NODE=' + node + ' GPU_QUERY=UNKNOWN')
        return
    try:
        inventory = query('index', 'gpu')
        if not inventory or any(not value.isdigit() for value in inventory):
            raise ValueError('GPU inventory is ambiguous')
        processes = query('gpu_uuid,pid', 'compute-apps')
        if any(',' not in value or not value.rsplit(',', 1)[1].strip().isdigit() for value in processes):
            raise ValueError('GPU process report is ambiguous')
        print('GPU_NODE={} GPU_QUERY=OK GPUS_PHYSICAL_TOTAL={} GPU_INDICES={} GPU_ACTIVE_PROCESSES={}'.format(
            node, len(inventory), ','.join(inventory), len(processes)))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        print('GPU_NODE=' + node + ' GPU_QUERY=UNKNOWN')


main()

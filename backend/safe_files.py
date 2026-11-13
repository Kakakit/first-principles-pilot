"""Bounded file reads over the host SSH session; standalone CLI shares the codec."""
from resources.preflight.safe_pull_from_cluster import prepare_batch, publish_batch, MAX_BYTES
from . import remote_transport


def pull_many(alias, files, companion_command='', timeout=180, lane='normal'):
    command, targets = prepare_batch(files, remote_transport._interpreter(alias), companion_command)
    receipt = remote_transport.run(alias, command, timeout=timeout,
        stdout_limit=len(files)*2*MAX_BYTES+1024*1024, lane=lane, mutating=False)
    return publish_batch(alias, targets, receipt)

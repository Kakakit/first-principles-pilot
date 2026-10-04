"""Batch live validators, then reuse their receipts within preparation."""
import functools
import inspect
from . import remote_transport, remote_paths, servers, execution as ex


def batched(function, kind):
    signature = inspect.signature(function)
    @functools.wraps(function)
    def prepare(*args, **kwargs):
        params = signature.bind(*args, **kwargs)
        params.apply_defaults()
        p = params.arguments
        if p.get('attempt_id'):
            try: ex.get(p['attempt_id'])
            except ValueError: pass
            else: return function(*params.args, **params.kwargs)
        with remote_transport.operation(kind+'-prepare'):
            config = p['config']
            from . import recycle
            recycle.assert_available('inputs' if kind=='vasp' else 'projects',p['bundle_id'] if kind=='vasp' else p['project']['id'])
            root = remote_paths.normalize_workspace_root(config.get('remote_workspace_root'))
            validators = [lambda: servers.capacity_probe(config, **({'purpose':'wannier90'} if kind=='wannier90' else {})),
                          lambda: ex.ssh(config['alias'],remote_paths.workspace_probe_command(root),timeout=30)]
            if kind == 'wannier90':
                from . import wannier_execution as w, source_files, remote_energy, wannier_bands
                frozen = w.configuration(p['frozen_id']) if p.get('frozen_id') else None
                parent_id = frozen['parent_attempt_id'] if frozen else p['parent_id']
                parent, _ = w._parent(frozen['project'] if frozen else p['project'],p['node_id'],parent_id,config['alias'])
                if frozen:
                    validators.append(lambda: source_files.snapshot(config['alias'],frozen['source_path'],frozen['source_files'],ex.ssh,mode=frozen.get('verification_mode','full')))
                else:
                    validators.append(lambda: w.inspect(p['project'],p['node_id'],parent_id,config['alias'],p['seed'],force=True))
                validators.append(lambda: remote_energy.reference(parent,force=True))
                path = (frozen or p).get('band_path')
                if path: validators.append(lambda: wannier_bands.recheck(path))
            else:
                from . import source_contracts
                manifest, files = ex.bundle(p['bundle_id'],allow_dependencies=True)
                contract = manifest.get('source_contract')
                if contract and not p.get('chain_source_id'):
                    validators.append(lambda: source_contracts.materialize(contract,config['alias']))
                elif p.get('chain_source_id'):
                    validators.append(lambda: ex.chain_source_snapshot(p['bundle_id'],manifest,p['chain_source_id'],config['alias'],p['chain_source_kind']))
                elif p.get('restart_attempt_id'):
                    validators.append(lambda: ex.restart_snapshot(p['bundle_id'],manifest,files,p['restart_attempt_id'],config['alias'],p['restart_mode'],p['restart_reason']))
                elif manifest.get('dependencies'):
                    validators.append(lambda: ex.dependency_snapshot(p['bundle_id'],manifest,files,p['dependency_attempt_id'],config['alias'],p['dependency_reason']))
            results = remote_transport.parallel(validators)
            params.arguments['probe_id'] = results[0]['id']
            result = function(*params.args,**params.kwargs)
            result['dispatch_protocol'] = 'fpilot.batch-dispatch.v1'
            ex.save(result)
            return result
    return prepare


def batched_freeze(function):
    signature=inspect.signature(function)
    @functools.wraps(function)
    def freeze(*args,**kwargs):
        params=signature.bind(*args,**kwargs);params.apply_defaults();p=params.arguments
        from . import wannier_execution as w,wannier_bands
        with remote_transport.operation('wannier-review'):
            validators=[lambda:w.inspect(p['project'],p['node_id'],p['parent_id'],p['alias'],p['seed'],max_cache_age=60)]
            if p.get('band_path'):validators.append(lambda:wannier_bands.recheck(p['band_path'],force=False,max_cache_age=60))
            remote_transport.parallel(validators)
            return function(*params.args,**params.kwargs)
    return freeze

"""One generic API for every v2 package; legacy reports keep their old routes."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
import subprocess
import zipfile
from . import extension_protocol as p, extension_runs as runs, extension_remote as remote, extension_environments as environments, extension_presentations as views, servers

router=APIRouter(prefix='/api/extension-host')

def call(function,*args,**kwargs):
    try:return function(*args,**kwargs)
    except (ValueError,KeyError,TypeError,OSError,subprocess.TimeoutExpired,zipfile.BadZipFile) as exc:raise HTTPException(422,str(exc)) from exc

@router.get('/packages')
def packages():return {'packages':call(p.catalog)}

@router.post('/packages/import')
def import_package(payload:dict):
    if set(payload)=={'path'}:return call(p.install_directory,payload['path'])
    if set(payload)=={'base64'}:return call(p.import_zip,payload['base64'])
    raise HTTPException(422,'请选择本地目录或单个 ZIP')

@router.post('/packages/{key}/install')
def install(key:str):return call(p.install_builtin,key)

@router.put('/packages/{key}/enabled')
def enabled(key:str,payload:dict):
    if set(payload)!={'enabled'} or type(payload['enabled']) is not bool:raise HTTPException(422,'enabled 须为布尔值')
    return call(p.enable,key,payload['enabled'])

@router.post('/packages/{key}/doctor')
def doctor(key:str,payload:dict=None):
    result=call(environments.check,key,payload)
    return {**p.public(p.installed(key),p.registry()['installed'][key]),**result}

@router.put('/packages/{key}/runtime')
def package_runtime(key:str,payload:dict):return call(environments.save_override,key,payload)

@router.delete('/packages/{key}')
def uninstall(key:str):return call(p.uninstall,key)

@router.get('/runtimes')
def runtimes():return {'runtimes':remote.runtimes(),'aliases':call(servers.registered_profiles),'local':environments.local_default(),'overrides':environments.settings()['overrides']}

@router.put('/runtimes/local')
def local_runtime(payload:dict):return call(environments.save_local,payload)

@router.get('/runtimes/{alias}/config')
def runtime_config(alias:str):return call(servers.select_profile,alias)

@router.put('/runtimes')
def save_runtime(payload:dict):return call(remote.save_runtime,payload)

@router.post('/context')
def context(payload:dict):return call(runs.context,payload)

@router.post('/check')
def check(payload:dict):return call(runs.check,payload)

@router.post('/preview')
def preview(payload:dict):return call(runs.preview,payload)

@router.post('/analyses')
def start(payload:dict):return call(runs.start,payload)

@router.get('/analyses')
def history(project_id:str=None,attempt_id:str=None):return {'analyses':call(runs.history,project_id,attempt_id)}

@router.get('/analyses/{key}')
def analysis(key:str):return call(runs.get,key)

@router.post('/analyses/{key}/refresh')
def refresh(key:str):return call(runs.refresh,key)

@router.post('/analyses/{key}/presentation')
def presentation(key:str):return call(views.schedule,key)

@router.get('/analyses/{key}/presentation/{version}/state')
def presentation_state(key:str,version:str):return call(views.state,key,version)

@router.put('/analyses/{key}/presentation/{version}/state')
def presentation_save_state(key:str,version:str,payload:dict):return call(views.save_state,key,version,payload)

@router.get('/analyses/{key}/presentation/{version}/files/{name:path}')
def presentation_file(key:str,version:str,name:str):
    path,media=call(views.asset,key,version,name)
    return FileResponse(path,media_type=media,headers={'X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox allow-scripts allow-downloads; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'"})

@router.get('/analyses/{key}/files/{name:path}')
def output(key:str,name:str):
    path,media=call(runs.output,key,name)
    return FileResponse(path,media_type=media,filename=path.name)

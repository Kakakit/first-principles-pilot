"""File collection is separate from scientific submission."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from . import file_transfers as transfers

router=APIRouter(prefix='/api/file-transfers')


def call(function,*args):
    try:return function(*args)
    except (ValueError,OSError,KeyError,TypeError) as exc:raise HTTPException(400,str(exc))


@router.get('')
def listing(project_id:str=None):return {'jobs':call(transfers.listing,project_id)}


@router.post('')
def request(payload:dict):return call(transfers.request,payload.get('project_id'),payload.get('reference'))


@router.get('/{key}')
def get(key:str):return call(transfers.get,key)


@router.post('/{key}/{operation}')
def action(key:str,operation:str):return call(transfers.action,key,operation)


@router.get('/{key}/download')
def download(key:str):
    path,name=call(transfers.download,key)
    return FileResponse(path,filename=name)

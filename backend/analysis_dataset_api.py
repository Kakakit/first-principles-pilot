from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from filelock import Timeout
from pydantic import BaseModel, Field
from typing import Literal, Optional
from . import analysis_datasets as datasets

router = APIRouter(prefix='/api/analysis-datasets')


def call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except Timeout as error:
        raise HTTPException(409, '正在登记或保存同一数据集，请稍后重试') from error
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise HTTPException(422, str(error)) from error


class Registration(BaseModel):
    source_kind: Literal['v2', 'v1', 'local', 'plot', 'comparison']
    source_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    title: Optional[str] = Field(default=None, max_length=500)
    note: str = Field(default='', max_length=10000)


class Metadata(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    note: str = Field(default='', max_length=10000)


@router.get('')
def listing(project_id: str = None):
    return {'records': call(datasets.list_records, project_id)}


@router.get('/history')
def history(project_id: str = None):
    return {'records': call(datasets.available, project_id)}


@router.get('/status/{kind}/{key}')
def registration_status(kind: str, key: str):
    return call(datasets.registration_status, kind, key)


@router.get('/history/{kind}/{key}')
def history_detail(kind: str, key: str):
    return call(datasets.history_detail, kind, key)


@router.get('/history/{kind}/{key}/files/{name:path}')
def history_file(kind: str, key: str, name: str):
    path, media = call(datasets.history_output, kind, key, name)
    return FileResponse(path, media_type=media, filename=path.name)


@router.post('')
def register(payload: Registration):
    return call(datasets.register, payload.source_kind, payload.source_id, payload.title, payload.note)


@router.get('/{key}')
def detail(key: str):
    return call(datasets.get, key)


@router.put('/{key}')
def update(key: str, payload: Metadata):
    return call(datasets.update, key, payload.title, payload.note)


@router.get('/{key}/files/{name:path}')
def output(key: str, name: str):
    path, media = call(datasets.output, key, name)
    return FileResponse(path, media_type=media, filename=path.name)


@router.get('/{key}/presentation/{version}/files/{name:path}')
def presentation_file(key: str, version: str, name: str):
    view = call(datasets.presentation, key, version)
    if not any(asset['path'] == name for asset in view['manifest']['assets']):
        raise HTTPException(422, '文件未在展示清单中声明')
    path, media = call(datasets.output, key, 'presentation/' + name)
    return FileResponse(path, media_type=media)


@router.get('/{key}/presentation/{version}/state')
def state(key: str, version: str):
    return call(datasets.view_state, key, version)


@router.put('/{key}/presentation/{version}/state')
def save_state(key: str, version: str, payload: dict):
    return call(datasets.view_state, key, version, payload)

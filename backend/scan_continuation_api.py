from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from filelock import Timeout
from . import scan_continuations

router = APIRouter()


class CaseSelection(BaseModel):
    case_id: str = Field(min_length=1, max_length=128)
    attempt_id: str = Field(min_length=1, max_length=128)


class ContinuationPreview(BaseModel):
    project: dict
    source_batch_id: str
    selections: list[CaseSelection] = Field(min_length=1, max_length=64)
    name: str = Field(default='后续 SCF', min_length=1, max_length=200)
    incar: str = Field(default='', max_length=20000)
    grid: str = Field(default='', max_length=80)
    center: Literal['Gamma', 'Monkhorst-Pack'] = 'Gamma'
    files: Literal['structure', 'charge', 'both'] = 'structure'
    acknowledge_differences: bool = False


class ContinuationCreate(BaseModel):
    project_id: str
    definition_hash: str


@router.get('/api/scans/{batch_id}/continuation-sources')
def continuation_sources(batch_id: str, project_id: str, attempt_id: str = None):
    try:
        return scan_continuations.choices(batch_id, project_id, attempt_id)
    except (ValueError, OSError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post('/api/scan-continuations/preview')
def continuation_preview(payload: ContinuationPreview):
    try:
        return scan_continuations.preview(**payload.model_dump())
    except (ValueError, OSError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post('/api/scan-continuations/{preview_id}/create')
def continuation_create(preview_id: str, payload: ContinuationCreate):
    try:
        return scan_continuations.create(payload.project_id, preview_id, payload.definition_hash)
    except Timeout as exc:
        raise HTTPException(409, '此预览仍在创建批次，请稍后使用原预览重试') from exc
    except (ValueError, OSError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc

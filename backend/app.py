"""Local workspace API with single-attempt PBS/Slurm execution."""
import platform
from typing import Literal, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(application):
    from . import problem_records
    try:problem_records.backfill()
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Problem notebook backfill failed; execution state is unchanged')
    yield


app = FastAPI(title="First Principles Workbench", version="1.0.0",lifespan=lifespan)


@app.get('/api/project-sync/hosts')
def sync_hosts():
    from . import project_sync
    return project_sync.hosts()


@app.get('/api/project-sync/catalog')
def sync_catalog(alias: str):
    from . import project_sync
    try:return project_sync.catalog(alias)
    except (ValueError, OSError) as exc:raise HTTPException(422, str(exc)) from exc


@app.get('/api/projects/{project_id}/sync')
def sync_status(project_id: str, check: bool = False):
    from . import project_sync
    try:return project_sync.status(project_id, check)
    except (ValueError, OSError) as exc:raise HTTPException(422, str(exc)) from exc


@app.get('/api/projects/{project_id}/sync/versions')
def sync_versions(project_id: str, alias: str):
    from . import project_sync
    try:return project_sync.versions(project_id, alias)
    except (ValueError, OSError) as exc:raise HTTPException(422, str(exc)) from exc


@app.post('/api/projects/{project_id}/sync/preview')
def sync_preview(project_id: str, payload: dict):
    from . import project_sync
    try:return project_sync.preview(project_id, payload['alias'], payload['direction'], payload.get('version'))
    except (ValueError, OSError, KeyError) as exc:raise HTTPException(422, str(exc)) from exc


@app.post('/api/projects/{project_id}/sync/transfer')
def sync_transfer(project_id: str, payload: dict):
    from . import project_sync
    try:return project_sync.transfer(project_id, payload['preview_id'], payload.get('choose_server') is True)
    except (ValueError, OSError, KeyError) as exc:raise HTTPException(422, str(exc)) from exc


@app.post('/api/projects/{project_id}/sync/recover')
def sync_recover(project_id: str):
    from . import project_sync
    try:return project_sync.recover(project_id)
    except (ValueError, OSError) as exc:raise HTTPException(422, str(exc)) from exc


@app.post('/api/projects/{project_id}/sync/refresh')
def sync_refresh(project_id: str):
    from . import project_sync
    try:return project_sync.refresh(project_id)
    except (ValueError, OSError) as exc:raise HTTPException(422, str(exc)) from exc


@app.get('/api/problem-records')
def problem_list(deleted: bool = False):
    from . import problem_records
    try:return {'records':problem_records.listing(deleted=deleted)}
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/problem-records/collect')
def problem_collect(payload: dict):
    from . import problem_records
    try:return problem_records.capture(payload['source_kind'],payload['source_id'],manual=True,excerpt=payload.get('excerpt'))
    except (ValueError,OSError,KeyError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/problem-records/export')
def problem_export(payload: dict):
    from . import problem_records
    from fastapi.responses import Response
    try:
        format=payload.get('format','json');content=problem_records.export(payload['ids'],format)
        return Response(content,media_type='application/json' if format=='json' else 'text/markdown; charset=utf-8',headers={'Content-Disposition':'attachment; filename="problem-records.'+('json' if format=='json' else 'md')+'"'})
    except (ValueError,OSError,KeyError) as exc:raise HTTPException(422,str(exc)) from exc


@app.get('/api/problem-records/{key}')
def problem_detail(key: str):
    from . import problem_records
    try:return problem_records.get(key)
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.put('/api/problem-records/{key}')
def problem_save(key: str,payload: dict):
    from . import problem_records
    try:return problem_records.save(key,payload['notes'],payload.get('incar_tags',[]))
    except (ValueError,OSError,KeyError) as exc:raise HTTPException(422,str(exc)) from exc


@app.delete('/api/problem-records/{key}')
def problem_delete(key: str):
    from . import problem_records
    try:return problem_records.set_deleted(key, True)
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/problem-records/{key}/restore')
def problem_restore(key: str):
    from . import problem_records
    try:return problem_records.set_deleted(key, False)
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/kpoints/suggest')
def kpoints_suggest(payload: dict):
    from . import kpoints
    try:return kpoints.suggest(payload['lattice'],payload.get('kpr',0.04),payload.get('center','Gamma'),payload.get('gamma_only',False))
    except (ValueError,KeyError,TypeError) as exc:raise HTTPException(422,str(exc)) from exc


@app.get("/api/health")
def health():
    return {"status": "ok", "phase": "1.0", "version": "1.0.0", "platform": platform.system(), "mode": "local-workspace", "remote_execution": True}


@app.get("/api/capabilities")
def capabilities():
    return {
        "editor_document_format": "single-current-layout",
        "storage": "local-files-and-sqlite",
        "nodes": ["structure", "relax", "scf", "bands", "dos", "vasp-wannier", "wannier90", "scan"],
        "structure_import": True,
        "structure_edit": True,
        "parameter_editor": True,
        "input_generation": True,
        "potcar_library": True,
        "server_diagnostics": True,
        "pbs_preview": True,
        "slurm_preview": True,
        "job_submission": True,
        "remote_path_preview": True,
        "remote_path_schema": "fpilot.remote-attempt-path",
        "legacy_remote_paths": False,
        "job_template_import": True,
        "gpu_submission": True,
        "gpu_resource_policy": "whole_node",
        "persistent_worker": True,
        "execution_limits": True,
        "failure_snapshot": True,
        "recipe_schema": "fpilot.recipe.v1",
        "recipe_import_export": True,
        "recipe_auto_canvas": True,
        "handoff_schema": "fpilot.handoff.v1",
        "handoff_read_only_import": True,
        "dependent_vasp_chain": True,
        "wannier90_execution": True,
        "same_node_restart": True,
        "project_scoped_frozen_inputs": True,
        "recoverable_local_recycle": True,
        "permanent_remote_attempt_delete": True,
        "seekpath_band_path": True,
        "result_dataset_schema": "fpilot.web-result-dataset.v1",
        "result_curves": True,
        "submission_schedulers": ["pbs", "slurm"],
        "submission_scope": "local app; registered PBS/Slurm VASP jobs; independent scf/relax, same-node restart and explicit relax→scf, scf→dos/bands/vasp-wannier dependencies",
        "supported_desktop_platforms": ["macOS"],
        "windows_support": False,
        "windows_testing": "Windows 10/11 x64, Python 3.11; native acceptance pending",
        "node_result_center": True,
        "remote_artifact_browser": True,
        "postprocessing_extensions": True,
        "local_multi_node_postprocessing": False,
        "vasp_parameter_scans": True,
        "wannier90_window_scans": True,
        "local_pdos_fatband": True,
        "result_analysis": True,
    }


from . import recipes
from . import parameter_catalog
from . import handoffs
from . import result_datasets
from . import recycle
from . import band_path
from . import local_analyses
from . import electronic_plots
from . import wannier_execution
from . import interactive_plots
from . import pyw90_remote
from . import wannier_bands
from . import wannier_compare
from . import wannier_plot_restart
from . import remote_cleanup
from . import scans
from . import scan_results
from . import chain_runs


class ChainRequest(BaseModel):
    project: dict
    node_ids: list[str]
    reason: str = ''
    expected_definition_hash: Optional[str] = None
    source_alias: Optional[str] = None
    reuse_sources: dict[str, str] = Field(default_factory=dict)
    verification_mode: str = 'lightweight'


class ChainAuthorization(BaseModel):
    project_id: str
    plan_hash: str
    mismatch_hash: str
    config: dict
    resources: dict
    approved_previews: dict


class ChainAction(BaseModel):
    project_id: str


class ChainResources(ChainAction):
    config: dict
    resources: dict


@app.post('/api/chains/{run_id}/resources-preview')
def chain_resources_preview(run_id: str, payload: ChainResources):
    try:
        return chain_runs.resource_preview(run_id, payload.project_id, payload.config, payload.resources)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/api/chains/preview')
def chain_preview(payload: ChainRequest):
    try:
        return chain_runs.preview(payload.project, payload.node_ids, payload.source_alias,
                                  payload.reuse_sources, payload.verification_mode)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/api/chains/freeze')
def chain_freeze(payload: ChainRequest):
    try:
        return chain_runs.freeze(payload.project, payload.node_ids, payload.reason,
                                 payload.expected_definition_hash, payload.source_alias,
                                 payload.reuse_sources, payload.verification_mode)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get('/api/chains')
def chain_history(project_id: str):
    return {'runs': chain_runs.history(project_id)}


@app.get('/api/chains/{run_id}')
def chain_get(run_id: str, project_id: str):
    try:
        return chain_runs.status(run_id, project_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/api/chains/{run_id}/authorize')
def chain_authorize(run_id: str, payload: ChainAuthorization):
    try:
        return chain_runs.authorize_and_dispatch(run_id, **payload.model_dump())
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/api/chains/{run_id}/pause')
def chain_pause(run_id: str, payload: ChainAction):
    try:
        return chain_runs.pause(run_id, payload.project_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/api/chains/{run_id}/resume')
def chain_resume(run_id: str, payload: ChainAction):
    try:
        return chain_runs.resume(run_id, payload.project_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/api/chains/{run_id}/refresh')
def chain_refresh(run_id: str, payload: ChainAction):
    try:
        return chain_runs.refresh(run_id, payload.project_id)
    except (ValueError, KeyError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


class ScanRequest(BaseModel):
    project: dict
    scan_node_id: str
    base_node_id: str
    dimensions: list[dict] = []
    mode: Literal['vasp','wannier90-window'] = 'vasp'
    frozen_id: Optional[str] = None
    window_search: Optional[dict] = None
    source_draft: Optional[dict] = None
    reason: str = ''
    expected_definition_hash: Optional[str] = None


class ScanAuthorization(BaseModel):
    project_id: str
    case_table_hash: str
    config: dict
    node: str = ''
    cores: Optional[int] = None
    reason: str = ''
    approved_script: str = ''
    approved_profile: Optional[dict] = None
    candidates: Optional[list[dict]] = None
    approved_previews: Optional[dict] = None
    max_parallel: Optional[int] = None
    template_id: Optional[str] = None
    dependency_attempt_id: Optional[str] = None
    dependency_reason: str = ''


@app.post('/api/scans/preview')
def scan_preview(payload: ScanRequest):
    try: return scans.preview(payload.project,payload.scan_node_id,payload.base_node_id,payload.dimensions,payload.mode,payload.frozen_id,payload.window_search,payload.source_draft)
    except (ValueError,KeyError,TypeError,OSError) as exc: raise HTTPException(422,str(exc)) from exc


@app.post('/api/scans/freeze')
def scan_freeze(payload: ScanRequest):
    try: return scans.freeze(payload.project,payload.scan_node_id,payload.base_node_id,payload.dimensions,payload.reason,payload.expected_definition_hash,payload.mode,payload.frozen_id,payload.window_search,payload.source_draft)
    except (ValueError,KeyError,TypeError,OSError) as exc: raise HTTPException(422,str(exc)) from exc


@app.get('/api/scans')
def scan_history(project_id: str):
    return {'batches':scans.history(project_id)}


@app.get('/api/scans/{batch_id}')
def scan_get(batch_id: str, project_id: str):
    try:
        return scans.status(batch_id, project_id)
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc


@app.post('/api/scans/{batch_id}/authorize')
def scan_authorize(batch_id: str, payload: ScanAuthorization):
    try: return scans.authorize_and_dispatch(batch_id,**payload.model_dump())
    except (ValueError,KeyError,TypeError,OSError) as exc: raise HTTPException(422,str(exc)) from exc


class ScanResources(BaseModel):
    project_id: str
    config: dict
    candidates: list[dict]


@app.post('/api/scans/{batch_id}/resources-preview')
def scan_resources_preview(batch_id: str, payload: ScanResources):
    try: return scans.resource_preview(batch_id, **payload.model_dump())
    except (ValueError,KeyError,TypeError,OSError) as exc: raise HTTPException(422,str(exc)) from exc


class ScanAction(BaseModel):
    project_id: str


class ScanPromotion(ScanAction):
    reason: str


class ResultComparison(BaseModel):
    project_id: str
    attempt_ids: list[str]


@app.post('/api/scans/{batch_id}/next-round')
def window_next_round(batch_id: str, payload: ScanRequest):
    from . import wannier_scans
    try:
        previous=scans.get(batch_id)
        if previous['project_id']!=payload.project['id'] or not wannier_scans.is_window(previous):raise ValueError('请选择当前项目的能窗批次')
        search={**(payload.window_search or {}),'previous_batch_id':batch_id}
        return wannier_scans.preview(payload.project,payload.scan_node_id,payload.base_node_id,payload.frozen_id,search)
    except (ValueError,KeyError,TypeError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.get('/api/scans/{batch_id}/window-results')
def window_results(batch_id: str, project_id: str):
    from . import wannier_scans
    try:
        batch=scans.get(batch_id)
        if not wannier_scans.is_window(batch):raise ValueError('不是能窗批次')
        return wannier_scans.results(batch,project_id)
    except (ValueError,KeyError,TypeError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/scans/{batch_id}/cases/{case_id}/adopt-window')
def window_adopt(batch_id: str, case_id: str, payload: ScanAction):
    from . import wannier_scans
    try:return wannier_scans.adopt(scans.get(batch_id),payload.project_id,case_id)
    except (ValueError,KeyError,TypeError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


class WindowComparison(BaseModel):
    project_id: str
    case_ids: list[str]
    band_attempt_id: str
    dft_band_offset: int


@app.post('/api/scans/{batch_id}/window-compare')
def window_compare(batch_id: str, payload: WindowComparison):
    from . import wannier_scans
    try:return wannier_scans.compare(scans.get(batch_id),**payload.model_dump())
    except (ValueError,KeyError,TypeError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/results/compare')
def results_compare(payload: ResultComparison):
    try: return scan_results.compare(payload.project_id,payload.attempt_ids)
    except (ValueError,OSError,KeyError) as exc: raise HTTPException(422,str(exc)) from exc


@app.get('/api/results/{key}/summary')
def result_summary(key: str, project_id: str):
    from . import result_overview
    try:return result_overview.summary(project_id,key)
    except (ValueError,OSError,KeyError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/scans/{batch_id}/cases/{case_id}/promote')
def scan_promote(batch_id: str, case_id: str, payload: ScanPromotion):
    try: return scan_results.promote(batch_id,payload.project_id,case_id,payload.reason)
    except (ValueError,OSError,KeyError) as exc: raise HTTPException(422,str(exc)) from exc


@app.post('/api/scans/{batch_id}/pause')
def scan_pause(batch_id: str, payload: ScanAction):
    try: return scans.pause(batch_id,payload.project_id)
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc


@app.post('/api/scans/{batch_id}/resume')
def scan_resume(batch_id: str, payload: ScanAction):
    try: return scans.resume(batch_id,payload.project_id)
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc


@app.post('/api/scans/{batch_id}/cases/{case_id}/retry')
def scan_retry(batch_id: str, case_id: str, payload: ScanAction):
    try: return scans.retry_case(batch_id,payload.project_id,case_id)
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc


class InteractivePlotPrepare(BaseModel):
    force: bool = False
    project_id: str
    band_attempt_id: Optional[str] = None
    dos_attempt_id: Optional[str] = None


class InteractivePlotSeries(BaseModel):
    cache_id: str
    selected: list[dict[str, str]] = Field(default_factory=list)
    emin: float = -4
    emax: float = 4


class InteractivePlotSave(InteractivePlotSeries):
    project_id: str
    png_data_url: str
    svg: str
    title: str = ''
    point_size_scale: float = 1


@app.get('/api/interactive-plots/candidates')
def interactive_plot_candidates(project_id: str):
    return {'attempts': interactive_plots.candidates(project_id)}


@app.get('/api/interactive-plots/cached')
def interactive_plot_cached(project_id: str, band_attempt_id: Optional[str] = None, dos_attempt_id: Optional[str] = None):
    try:return interactive_plots.cached(project_id, band_attempt_id, dos_attempt_id)
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/interactive-plots/prepare')
def interactive_plot_prepare(payload: InteractivePlotPrepare):
    try:return interactive_plots.prepare(**payload.model_dump())
    except (ValueError, OSError, KeyError) as exc:raise HTTPException(422,str(exc)) from exc


@app.get('/api/interactive-plots/{key}/status')
def interactive_plot_status(key: str):
    try:return interactive_plots.status(key)
    except (ValueError, OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/interactive-plots/series')
def interactive_plot_series(payload: InteractivePlotSeries):
    try:return interactive_plots.series(payload.cache_id,payload.selected,payload.emin,payload.emax)
    except (ValueError, OSError, KeyError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/interactive-plots/save')
def interactive_plot_save(payload: InteractivePlotSave):
    try:return interactive_plots.save(payload.cache_id,payload.project_id,payload.selected,payload.emin,payload.emax,
                                      payload.png_data_url,payload.svg,payload.title,payload.point_size_scale)
    except (ValueError, OSError, KeyError) as exc:raise HTTPException(422,str(exc)) from exc


@app.get('/api/interactive-plots/saved')
def interactive_plot_saved(project_id: str):
    return {'records': interactive_plots.saved(project_id)}


@app.get('/api/interactive-plots/saved/{key}/files/{name}')
def interactive_plot_saved_file(key: str, name: str, project_id: str):
    from fastapi.responses import Response
    media={'figure.png':'image/png','figure.svg':'image/svg+xml','selected-curves.csv':'text/csv'}
    try:return Response(interactive_plots.saved_file(key,name,project_id),media_type=media.get(name,'application/octet-stream'))
    except (ValueError, OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.get('/api/wannier90/energy-reference')
def wannier_energy_reference(project_id: str, attempt_id: str):
    try:return interactive_plots.energy_reference(project_id,attempt_id)
    except (ValueError, OSError, KeyError) as exc:raise HTTPException(422,str(exc)) from exc


class PyW90Advice(BaseModel):
    project: dict
    node_id: str
    parent_id: str
    alias: str
    seed: str
    mode: Literal['eig', 'pre']
    emin: float = -4
    emax: float = 4
    energy_reference: dict
    dos_attempt_id: Optional[str] = None
    relative_windows: Optional[dict[str, float]] = None


@app.post('/api/wannier90/pyw90-advice')
def wannier_pyw90_advice(payload: PyW90Advice):
    try:return pyw90_remote.recommend(**payload.model_dump())
    except (ValueError, OSError, KeyError) as exc:raise HTTPException(422,str(exc)) from exc


class PyW90WindowCheck(BaseModel):
    project: dict
    node_id: str
    parent_id: str
    alias: str
    seed: str
    emin: float = -4
    emax: float = 4
    energy_reference: dict
    relative_windows: Optional[dict[str, float]] = None
    expected_source_hashes: Optional[dict[str, str]] = None


@app.post('/api/wannier90/pyw90-window-check')
def wannier_pyw90_window_check(payload: PyW90WindowCheck):
    try:return pyw90_remote.check(**payload.model_dump())
    except (ValueError, OSError, KeyError) as exc:raise HTTPException(422,str(exc)) from exc


class WannierCompareRequest(BaseModel):
    title: Optional[str] = None
    project_id: str
    wannier_attempt_id: str
    band_attempt_id: str
    emin: float = -4
    emax: float = 4


@app.get('/api/wannier90/comparison-candidates')
def wannier_comparison_candidates(project_id: str, wannier_attempt_id: str):
    try:
        record = execution.get(wannier_attempt_id)
        if record.get('project_id') != project_id:
            raise ValueError('Wannier90 attempt 不属于当前项目')
        bundle, _ = wannier_compare._w90(record)
        return {'candidates': wannier_compare.candidates(project_id, wannier_attempt_id),
                'bound_band_attempt_id': (bundle.get('band_path') or {}).get('band_attempt_id')}
    except (ValueError, OSError, KeyError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/wannier90/comparisons')
def wannier_comparison_run(payload: WannierCompareRequest):
    try:return wannier_compare.run(**payload.model_dump())
    except (ValueError, OSError, KeyError) as exc:raise HTTPException(422,str(exc)) from exc


@app.get('/api/wannier90/comparisons')
def wannier_comparison_saved(project_id: str):
    return {'records': wannier_compare.saved(project_id)}


@app.post('/api/wannier90/comparison-jobs')
def wannier_comparison_start(payload: WannierCompareRequest):
    return wannier_compare.start(**payload.model_dump())


@app.get('/api/wannier90/comparison-jobs/{key}')
def wannier_comparison_status(key: str):
    try:return wannier_compare.status(key)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/wannier90/comparisons/{identifier}/diagnostic')
def wannier_comparison_diagnostic(identifier: str, payload: ScanAction):
    try:return wannier_compare.start_diagnostic(payload.project_id, identifier)
    except (ValueError, OSError, KeyError) as exc:raise HTTPException(422,str(exc)) from exc


@app.get('/api/wannier90/comparisons/{identifier}/files/{filename}')
def wannier_comparison_file(identifier: str, filename: str, project_id: str):
    from fastapi.responses import Response
    try:return Response(wannier_compare.output(project_id, identifier, filename),
                        media_type={'comparison.png':'image/png','comparison.svg':'image/svg+xml','curves.csv':'text/csv'}[filename])
    except (ValueError, OSError, KeyError) as exc:raise HTTPException(422,str(exc)) from exc


class WannierProbe(BaseModel):
    config: dict
    force: bool = False


class WannierPreview(BaseModel):
    config: dict
    probe_id: str
    node: str
    cores: Optional[int] = None
    resource_reason: str = ''
    template_id: Optional[str] = None


class WannierInspect(BaseModel):
    force: bool = False
    project: dict
    node_id: str
    parent_id: str
    alias: str
    seed: str = 'wannier90'
    verification_mode: str = 'lightweight'


class WannierPrepare(BaseModel):
    frozen_id: Optional[str] = None
    project: dict
    node_id: str
    parent_id: str
    config: dict
    probe_id: str
    node: str
    cores: Optional[int] = None
    resource_reason: str = ''
    input_reason: str = ''
    win: str
    template_id: Optional[str] = None
    seed: str = 'wannier90'
    energy_reference: Optional[dict] = None
    relative_windows: Optional[dict[str, float]] = None
    band_path: Optional[dict] = None


class WannierBandPathRequest(BaseModel):
    project: dict
    node_id: str
    parent_id: str
    alias: str
    band_attempt_id: str


@app.post('/api/wannier90/band-path')
def wannier_band_path(payload: WannierBandPathRequest):
    try:return wannier_bands.path_proposal(**payload.model_dump())
    except (ValueError, OSError, KeyError) as exc:raise HTTPException(422,str(exc)) from exc


class WannierPlotPathRequest(BaseModel):
    project: dict
    source_attempt_id: str
    band_attempt_id: str


@app.post('/api/wannier90/plot-restart/path')
def wannier_plot_path(payload: WannierPlotPathRequest):
    try:return wannier_plot_restart.path_proposal(payload.project, payload.source_attempt_id, payload.band_attempt_id)
    except (ValueError, OSError, KeyError) as exc:raise HTTPException(422,str(exc)) from exc


class WannierPlotPrepare(BaseModel):
    project: dict
    source_id: str
    band_id: str
    proposal: dict
    probe_id: str
    node: str
    cores: Optional[int] = None
    resource_reason: str = ''
    template_id: Optional[str] = None


@app.post('/api/wannier90/plot-restart/prepare')
def wannier_plot_prepare(payload: WannierPlotPrepare):
    try:return wannier_plot_restart.prepare(**payload.model_dump())
    except (ValueError, OSError, KeyError) as exc:raise HTTPException(422,str(exc)) from exc


class WannierBandPathApply(BaseModel):
    win: str
    proposal: dict
    path_lines: list[str]
    bands_num_points: int
    enabled: bool = True


@app.post('/api/wannier90/band-path/apply')
def wannier_band_path_apply(payload: WannierBandPathApply):
    try:
        proposal = {**payload.proposal, 'path_lines': payload.path_lines,
                    'bands_num_points': payload.bands_num_points}
        win = wannier_bands.with_path(payload.win, proposal, payload.enabled)
        return {'win': win, 'parameters': wannier_execution.parameter_values(win)}
    except (ValueError, OSError, KeyError) as exc:raise HTTPException(422,str(exc)) from exc


class WannierWinEdit(BaseModel):
    win: str
    changes: dict[str, str] = {}
    remove: list[str] = []
    source_win: Optional[str] = None
    fermi_eV: Optional[float] = None


@app.get('/api/wannier90/scripts')
def wannier_scripts():
    return wannier_execution.script_choices()


@app.get('/api/wannier90/parameters')
def wannier_parameters():
    try:return wannier_execution.parameter_catalog()
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/wannier90/win-edit')
def wannier_win_edit(payload: WannierWinEdit):
    try:return wannier_execution.edit_win(payload.win, payload.changes, payload.fermi_eV, payload.remove, payload.source_win)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/wannier90/probe')
def wannier_probe(payload: WannierProbe):
    try:return servers.probe(payload.config, purpose='wannier90', force=payload.force)
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/wannier90/preview')
def wannier_preview(payload: WannierPreview):
    try:
        report = servers.preview(payload.config, payload.probe_id, payload.node,
                                 payload.cores, payload.resource_reason, purpose='wannier90')
        script, source = wannier_execution._render_script(payload.config, report['profile'], payload.template_id)
        return {**report, 'script':script, 'script_source':source}
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/wannier90/inspect')
def wannier_inspect(payload: WannierInspect):
    try:return wannier_execution.inspect(**payload.model_dump())
    except (ValueError,OSError,UnicodeError,KeyError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/wannier90/discover')
def wannier_discover(payload: WannierInspect):
    try:return wannier_execution.discover(payload.project, payload.node_id, payload.parent_id, payload.alias,force=payload.force)
    except (ValueError,OSError,KeyError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/wannier90/prepare')
def wannier_prepare(payload: WannierPrepare):
    try:return wannier_execution.prepare(**payload.model_dump())
    except (ValueError,OSError,UnicodeError,KeyError) as exc:raise HTTPException(422,str(exc)) from exc


class BandPathRequest(BaseModel):
    structure: dict


@app.post('/api/structures/band-path')
def structure_band_path(payload: BandPathRequest):
    try:return band_path.generate(payload.structure)
    except (ValueError,KeyError,TypeError) as exc:raise HTTPException(422,str(exc)) from exc


class AttemptBandPathRequest(BaseModel):
    project_id: str


@app.post('/api/execution/{key}/band-path')
def attempt_band_path(key: str, payload: AttemptBandPathRequest):
    """Generate an editable path from the completed SCF's actual final cell."""
    try:
        from . import artifacts, execution, source_contracts
        record = execution.get(key)
        if record.get('project_id') != payload.project_id or record.get('state') != 'COMPLETED':
            raise ValueError('请选择当前项目已完成的 SCF attempt')
        manifest, _ = execution.bundle(record['bundle_id'], allow_dependencies=True)
        if manifest.get('stage') != 'scf' or not source_contracts.electronic(record.get('evidence') or {}):
            raise ValueError('来源需要已确认电子收敛的 SCF')
        preview = artifacts.preview(key, 'POSCAR', 'head', 262144, True)
        if preview.get('partial') or not preview.get('structure'):
            raise ValueError('SCF 实际 POSCAR 不完整或无法解析')
        return {**band_path.generate(preview['structure']), 'source_attempt_id': key,
                'source_job_id': record.get('job_id'), 'source_host': record['config']['alias'], 'source_file': 'POSCAR'}
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc


class RecycleMove(BaseModel):
    project: Optional[dict] = None


class PolarizationAnalysisRequest(BaseModel):
    project: dict
    reference_attempt_id: str
    target_attempt_id: str


class LocalPlotRequest(BaseModel):
    project_id: str
    attempt_id: str
    mode: Literal['pdos','fatband']
    ions: str = 'all'
    orbitals: str = 'd'


@app.post('/api/local-plots')
def run_local_plot(payload: LocalPlotRequest):
    try:return electronic_plots.run(**payload.model_dump())
    except (ValueError,OSError,KeyError) as exc:raise HTTPException(422,str(exc)) from exc


@app.get('/api/local-plots')
def local_plot_history(project_id: str, attempt_id: str):
    return {'plots':electronic_plots.history(project_id,attempt_id)}


@app.get('/api/local-plots/{key}/files/{name}')
def local_plot_output(key: str, name: str):
    from fastapi.responses import Response
    try:return Response(electronic_plots.output(key,name),media_type='image/png' if name.endswith('.png') else 'application/pdf')
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/local-analyses/polarization')
def run_polarization_analysis(payload: PolarizationAnalysisRequest):
    try:return local_analyses.polarization(**payload.model_dump())
    except (ValueError,OSError,KeyError) as exc:raise HTTPException(422,str(exc)) from exc


@app.get('/api/local-analyses')
def local_analysis_history(project_id: str, node_id: Optional[str] = None):
    return {'analyses':local_analyses.history(project_id,node_id)}


@app.get('/api/local-analyses/{key}/files/{name}')
def local_analysis_output(key: str, name: str):
    from fastapi.responses import Response
    try:return Response(local_analyses.output(key,name),media_type='application/json')
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.get('/api/recycle')
def recycle_all():
    return recycle.all_items()


@app.post('/api/recycle/empty')
def recycle_empty(payload: dict):
    try:return recycle.empty(payload.get('items'),payload.get('confirm_local'))
    except (ValueError,OSError,KeyError) as exc:raise HTTPException(422,str(exc)) from exc


@app.get('/api/recycle/{kind}/{key}/impact')
def recycle_impact(kind: str,key: str):
    try:
        recycle.marker(kind,key)
        return {'references':recycle._references(kind,key),'remote_deleted':False}
    except (ValueError,OSError,KeyError) as exc:raise HTTPException(422,str(exc)) from exc


@app.get('/api/recycle/{kind}')
def recycle_list(kind: str, project_id: Optional[str]=None):
    try:return recycle.list_items(kind,project_id)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/recycle/{kind}/{key}')
def recycle_move(kind: str, key: str, payload: RecycleMove):
    try:return recycle.move(kind,key,payload.project)
    except (ValueError,OSError,KeyError) as exc:raise HTTPException(422,str(exc)) from exc


@app.delete('/api/recycle/{kind}/{key}')
def recycle_restore(kind: str, key: str):
    try:return recycle.restore(kind,key)
    except (ValueError,OSError,KeyError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/recycle/{kind}/{key}/purge')
def recycle_purge(kind: str, key: str, payload: dict):
    try:return recycle.purge(kind,key,payload.get('confirm_local'))
    except (ValueError,OSError,KeyError) as exc:raise HTTPException(422,str(exc)) from exc


class RecipeDocument(BaseModel):
    recipe: dict


@app.get('/api/parameters/catalog')
def parameter_catalog_read():
    return parameter_catalog.public_catalog()


class RecipeText(BaseModel):
    text: str = Field(min_length=1, max_length=recipes.MAX_BYTES)


class RecipeProject(BaseModel):
    project: dict


class HandoffText(BaseModel):
    text: str = Field(min_length=1, max_length=handoffs.MAX_BYTES)
    source_host: str = Field(min_length=1, max_length=64)


class ResultDatasetImport(BaseModel):
    manifest_path: str = Field(min_length=1, max_length=4096)


@app.get('/api/result-datasets')
def result_dataset_list():
    return {'schema': 'fpilot.web-result-dataset.v1', 'records': result_datasets.list_records(), 'read_only': True}


@app.post('/api/result-datasets/import')
def result_dataset_import(payload: ResultDatasetImport):
    try:
        return result_datasets.import_dataset(payload.manifest_path)
    except (result_datasets.ResultDatasetError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get('/api/result-datasets/{key}')
def result_dataset_get(key: str):
    try:
        return result_datasets.get(key)
    except (result_datasets.ResultDatasetError, OSError) as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get('/api/handoffs')
def handoff_list():
    return {'schema': handoffs.SCHEMA, 'records': handoffs.HandoffStore().list(), 'read_only': True}


@app.post('/api/handoffs/import')
def handoff_import(payload: HandoffText):
    try:
        return handoffs.HandoffStore().import_text(payload.text, payload.source_host)
    except handoffs.HandoffError as exc:
        raise HTTPException(422, {'message': str(exc), 'issues': exc.issues}) from exc
    except OSError as exc:
        raise HTTPException(503, f'交接原稿保存失败：{str(exc)[:300]}') from exc


@app.get('/api/handoffs/{key}')
def handoff_get(key: str):
    try:
        return handoffs.HandoffStore().get(key)
    except handoffs.HandoffError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get('/api/handoffs/{key}/original')
def handoff_original(key: str):
    from fastapi.responses import Response
    try:
        raw = handoffs.HandoffStore().original(key)
        return Response(raw, media_type='application/json', headers={'Content-Disposition': 'attachment; filename="fpilot-handoff-original.json"'})
    except (handoffs.HandoffError, OSError) as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get('/api/recipes/schema')
def recipe_schema():
    return recipes.schema_document()


@app.post('/api/recipes/validate')
def recipe_validate(payload: RecipeDocument):
    issues = recipes.validate(payload.recipe)
    return {'schema': recipes.SCHEMA, 'valid': not issues, 'issues': issues, 'submission_authorized': False}


@app.post('/api/recipes/materialize')
def recipe_materialize(payload: RecipeDocument):
    try:
        return {'schema': recipes.SCHEMA, 'project': recipes.materialize(payload.recipe), 'submission_authorized': False}
    except recipes.RecipeError as exc:
        raise HTTPException(422, {'message': '配方校验失败', 'issues': exc.issues}) from exc


@app.post('/api/recipes/import')
def recipe_import(payload: RecipeText):
    try:
        return recipes.import_text(payload.text)
    except recipes.RecipeError as exc:
        raise HTTPException(422, {'message': '配方导入失败', 'issues': exc.issues}) from exc
    except OSError as exc:
        raise HTTPException(503, f'配方原稿保存失败：{str(exc)[:300]}') from exc


@app.post('/api/recipes/export')
def recipe_export(payload: RecipeProject):
    try:
        return {'schema': recipes.SCHEMA, 'recipe': recipes.export_project(payload.project), 'submission_authorized': False}
    except recipes.RecipeError as exc:
        raise HTTPException(422, {'message': '当前项目不能导出为配方', 'issues': exc.issues}) from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, str(exc)[:300]) from exc


from .structures import parse_structure


class StructureImport(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    format: Literal['cif', 'poscar']
    base64: str = Field(min_length=1, max_length=349528)


@app.post('/api/structures/parse')
def import_structure(payload: StructureImport):
    try:
        return parse_structure(payload.filename, payload.format, payload.base64)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f'结构导入失败：{str(exc)[:500]}') from exc


from .structure_edit import edit_structure, edit_poscar
from .storage import WorkspaceStore, ConflictError


class StructureEdit(BaseModel):
    parent: dict
    lattice: list
    atoms: list
    coordinate_mode: Literal['fractional', 'cartesian']


@app.post('/api/structures/edit')
def edit_geometry(payload: StructureEdit):
    try:
        return edit_structure(payload.parent, payload.lattice, payload.atoms, payload.coordinate_mode)
    except Exception as exc:
        raise HTTPException(422, f'结构编辑失败：{str(exc)[:500]}') from exc


class LocalSymmetryRequest(BaseModel):
    poscar: str = Field(min_length=1, max_length=262144)
    angle_tolerance_degree: float = Field(gt=0, le=30)


class PoscarEdit(BaseModel):
    parent: dict
    text: str = Field(min_length=1, max_length=262144)
    rebuild_atom_ids: bool = False


@app.post('/api/structures/poscar-edit')
def edit_poscar_text(payload: PoscarEdit):
    try:
        return edit_poscar(payload.parent, payload.text, payload.rebuild_atom_ids)
    except (ValueError, KeyError, TypeError, IndexError, OverflowError) as exc:
        raise HTTPException(422, f'POSCAR 编辑未通过验证：{str(exc)[:500]}') from exc


@app.post('/api/structures/symmetry')
def local_structure_symmetry(payload: LocalSymmetryRequest):
    try:return postprocessing.local_structure_symmetry(payload.poscar,payload.angle_tolerance_degree)
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


class WorkspaceSave(BaseModel):
    workspace: dict
    expected_revision: int = Field(ge=0)
    reason: Literal['autosave', 'migration', 'restore'] = 'autosave'


@app.get('/api/workspace')
def read_workspace():
    try:
        return WorkspaceStore().read()
    except Exception as exc:
        raise HTTPException(503, f'本机工作区不可读：{str(exc)[:300]}') from exc


@app.put('/api/workspace')
def save_workspace(payload: WorkspaceSave):
    try:
        return WorkspaceStore().save(payload.workspace, payload.expected_revision, payload.reason)
    except ConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, str(exc)[:300]) from exc
    except Exception as exc:
        raise HTTPException(503, f'保存失败，未更新当前版本：{str(exc)[:300]}') from exc


@app.get('/api/workspace/history')
def workspace_history():
    return WorkspaceStore().history()


@app.get('/api/workspace/history/{revision}')
def workspace_revision(revision: int):
    try:
        return WorkspaceStore().read(revision)
    except (ValueError, OSError) as exc:
        raise HTTPException(404, str(exc)) from exc


from .inputs import upload_potcar, assemble_potcar, generate, freeze, digest, ROOT, history
from . import potcar_library
from . import servers
from . import execution
from . import artifacts
from . import postprocessing, extension_sdk


class ExecutionPrepare(BaseModel):
    project_id: str
    template_id: Optional[str] = None
    bundle_id: str
    config: dict
    probe_id: str
    node: str
    cores: Optional[int] = None
    reason: str = ''
    dependency_attempt_id: Optional[str] = None
    dependency_reason: str = ''
    restart_attempt_id: Optional[str] = None
    restart_mode: Optional[Literal['both', 'charge']] = None
    restart_reason: str = ''


from . import execution_control, worker

@app.get('/api/execution-control')
def execution_control_state():
    return {'settings':execution_control.settings(),'worker':worker.state()}

@app.put('/api/execution-control')
def execution_control_save(payload: dict):
    try:
        return execution_control.save_settings(payload)
    except (ValueError,OSError) as exc:
        raise HTTPException(422,str(exc)) from exc

@app.get('/api/execution/{key}/events')
def execution_events(key: str):
    try:return execution_control.events(key)
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc

@app.get('/api/execution/{key}/notes')
def execution_notes(key: str):
    try:return execution_control.notes(key)
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc

@app.put('/api/execution/{key}/notes')
def execution_notes_save(key: str,payload: dict):
    try:return execution_control.save_notes(key,payload)
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc

@app.get('/api/execution/{key}/failure-snapshot')
def execution_snapshot(key: str):
    from fastapi.responses import JSONResponse
    try:
        report=execution_control.snapshot(key)
        return JSONResponse(report,headers={'Content-Disposition':'attachment; filename="failure-snapshot.json"'})
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc

class ExecutionRetry(BaseModel):
    probe_id: str
    node: str
    cores: Optional[int] = None
    reason: str = ''


class ArtifactPreview(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    mode: Literal['head', 'tail'] = 'tail'
    max_bytes: int = Field(default=262144, ge=1, le=262144)


class ArtifactDeletePreview(BaseModel):
    project_id: str
    path: str = Field(min_length=1, max_length=4096)


class ArtifactDelete(ArtifactDeletePreview):
    token: str = Field(pattern=r'^[a-f0-9]{32}$')
    confirmed: bool = False


@app.post('/api/execution/{key}/artifact-delete-preview')
def artifact_delete_preview(key: str, payload: ArtifactDeletePreview):
    from . import artifact_delete
    try:return artifact_delete.preview(payload.project_id, key, payload.path)
    except (ValueError, OSError, KeyError) as exc:raise HTTPException(422, str(exc)) from exc


@app.post('/api/execution/{key}/artifact-delete')
def artifact_delete_confirm(key: str, payload: ArtifactDelete):
    from . import artifact_delete
    try:return artifact_delete.remove(payload.project_id, key, payload.path, payload.token, payload.confirmed)
    except (ValueError, OSError, KeyError) as exc:raise HTTPException(422, str(exc)) from exc


@app.get('/api/execution/{key}/artifact-delete/{operation_id}')
def artifact_delete_status(key: str, operation_id: str, project_id: str):
    from . import artifact_delete
    try:return artifact_delete.status(project_id, key, operation_id)
    except (ValueError, OSError, KeyError) as exc:raise HTTPException(422, str(exc)) from exc


class AnalysisRun(BaseModel):
    extension_id: str = Field(min_length=1, max_length=128)
    confirmed: bool = False
    options: Optional[dict] = None


@app.get('/api/projects/{project_id}/nodes/{node_id}/attempts')
def node_attempts(project_id: str, node_id: str):
    return {'attempts': artifacts.attempts(project_id, node_id), 'path_schema': 'fpilot.remote-attempt-path'}


@app.get('/api/projects/{project_id}/node-editability')
def node_editability(project_id: str):
    from .node_editability import status
    import sqlite3
    store = WorkspaceStore()
    project = next((p for p in (store.read()['workspace'] or {'projects': []})['projects'] if p['id'] == project_id), None)
    if project is None: raise HTTPException(404, '项目尚未保存或不存在')
    try: return status(store.root, project)
    except (ValueError, OSError, sqlite3.Error) as exc: raise HTTPException(422, '节点历史核查失败：' + str(exc)) from exc


@app.post('/api/execution/{key}/artifacts/refresh')
def artifact_refresh(key: str):
    try:return artifacts.refresh(key)
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.get('/api/execution/{key}/artifacts')
def artifact_listing(key: str):
    try:return artifacts.listing(key)
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/execution/{key}/artifact-preview')
def artifact_preview(key: str, payload: ArtifactPreview):
    try:return artifacts.preview(key,payload.path,payload.mode,payload.max_bytes)
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.get('/api/execution/{key}/artifact-download')
def artifact_download(key: str, path: str):
    from fastapi.responses import Response
    try:
        raw,name=artifacts.pull(key,path)
        safe=re.sub(r'[^A-Za-z0-9._-]+','_',name) or 'artifact'
        return Response(raw,media_type='application/octet-stream',headers={'Content-Disposition':f'attachment; filename="{safe}"'})
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.get('/api/execution/{key}/technical-evidence')
def technical_evidence(key: str):
    from fastapi.responses import JSONResponse
    try:return JSONResponse(artifacts.technical_evidence(key),headers={'Content-Disposition':'attachment; filename="fpilot-technical-evidence.json"'})
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.get('/api/extensions')
def extension_catalog(attempt_id: Optional[str]=None):
    try:
        listing = artifacts.listing(attempt_id) if attempt_id else None
        return {'extensions': postprocessing.catalog(attempt_id, listing), 'listing': listing}
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/execution/{key}/verify-source-files')
def verify_source_files(key: str):
    """Optional read-only full verification; never changes a prepared contract."""
    try:
        record = execution.get(key)
        snapshot = record.get('chain_source_snapshot') or record.get('restart_snapshot') or record.get('dependency_snapshot') or record.get('source_snapshot')
        if not snapshot or not snapshot.get('files'):
            raise ValueError('该 attempt 没有绑定来源文件')
        from .source_files import snapshot as verify
        files = verify(record['config']['alias'], snapshot['source_path'], sorted(snapshot['files']), execution.ssh, 'full')
        changed = [name for name, old in snapshot['files'].items()
                   if old.get('size_bytes') != files[name]['size_bytes'] or
                   old.get('mtime') and old['mtime'] != files[name]['mtime'] or
                   old.get('sha256') and old['sha256'] != files[name]['sha256']]
        return {'verification_mode': 'full', 'files': files, 'changed': changed,
                'content_comparison': '仅有历史 SHA 的文件可确认内容一致；其余文件本次建立完整内容回执', 'checked_at': execution.now()}
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/api/extensions/{extension_id}/install')
def extension_install(extension_id: str):
    try:return extension_sdk.install(extension_id)
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


class ExtensionEnable(BaseModel):
    enabled: bool


@app.put('/api/extensions/{extension_id}/enabled')
def extension_enabled(extension_id: str, payload: ExtensionEnable):
    try:return extension_sdk.enable(extension_id,payload.enabled)
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.get('/api/execution/{key}/analyses')
def analysis_history(key: str):
    return {'analyses':postprocessing.history(key)}


@app.get('/api/execution/{key}/analyses/{analysis_id}/files/{name}')
def analysis_output_file(key: str, analysis_id: str, name: str):
    from fastapi.responses import Response
    try:
        raw = postprocessing.output_file(key, analysis_id, name)
        return Response(raw, media_type='application/octet-stream',
                        headers={'Content-Disposition': f'attachment; filename="{name}"'})
    except (ValueError, OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/execution/{key}/analyses/applicability')
def analysis_applicability(key: str, payload: AnalysisRun):
    try:
        result = postprocessing.check_applicability(key, payload.extension_id, payload.options)
        result.pop('source_bytes', None)
        return result
    except (ValueError, OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/execution/{key}/analyses')
def analysis_run(key: str,payload: AnalysisRun):
    try:return postprocessing.run(key,payload.extension_id,payload.confirmed,payload.options)
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc

@app.post('/api/execution/{key}/retry')
def retry_execution(key: str,payload: ExecutionRetry):
    try:return execution.retry(key,**payload.model_dump())
    except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


class PathPreview(BaseModel):
    bundle_id: str
    alias: str
    remote_workspace_root: str = '$HOME/230269265lht'


@app.post('/api/execution/path-preview')
def preview_execution_path(payload: PathPreview):
    try:
        return execution.path_preview(**payload.model_dump())
    except (ValueError,OSError) as exc:
        raise HTTPException(422,str(exc)) from exc


@app.get('/api/execution')
def execution_history(project_id: Optional[str]=None):
    return execution.history(project_id)


class PermanentAttemptDelete(BaseModel):
    project_id: str
    token: str
    confirm_text: str


@app.get('/api/execution/{key}/delete-preview')
def execution_delete_preview(key: str, project_id: str):
    try:return remote_cleanup.preview(project_id, key)
    except (ValueError, OSError, KeyError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/execution/{key}/permanent-delete')
def execution_permanent_delete(key: str, payload: PermanentAttemptDelete):
    try:return remote_cleanup.remove(payload.project_id, key, payload.token, payload.confirm_text)
    except (ValueError, OSError, KeyError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/execution/prepare')
def prepare_execution(payload: ExecutionPrepare):
    try:
        manifest,_=execution.bundle(payload.bundle_id,allow_dependencies=True)
        if not manifest.get('source_contract') and (manifest.get('dependencies') or manifest.get('restart_required') or payload.restart_attempt_id or payload.dependency_attempt_id):
            raise ValueError('旧冻结输入缺少来源合同，请在计算配置中重新审查并冻结')
        return execution.prepare(**payload.model_dump())
    except (ValueError,OSError) as exc:
        raise HTTPException(422,str(exc)) from exc


@app.post('/api/execution/{key}/submit')
def submit_execution(key: str):
    try:
        return execution.submit(key)
    except (ValueError,OSError) as exc:
        raise HTTPException(422,str(exc)) from exc


@app.post('/api/execution/{key}/refresh')
def refresh_execution(key: str):
    try:
        return execution.refresh(key)
    except (ValueError,OSError) as exc:
        raise HTTPException(422,str(exc)) from exc


class ServerConfig(BaseModel):
    config: dict
    force: bool = False


class ServerPreview(ServerConfig):
    probe_id: str
    node: str
    cores: Optional[int] = None
    reason: str = ''


@app.get('/api/server')
def server_config():
    return servers.read_config()


@app.put('/api/server')
def save_server(payload: ServerConfig):
    try:
        return servers.save_config(payload.config)
    except (ValueError,OSError) as exc:
        raise HTTPException(422,str(exc)) from exc


@app.post('/api/server/probe')
def probe_server(payload: ServerConfig):
    try:
        return servers.probe(payload.config, force=payload.force)
    except (ValueError,OSError) as exc:
        raise HTTPException(422,str(exc)) from exc


@app.post('/api/server/preview')
def preview_server(payload: ServerPreview):
    try:
        return servers.preview(payload.config,payload.probe_id,payload.node,payload.cores,payload.reason)
    except (ValueError,OSError) as exc:
        raise HTTPException(422,str(exc)) from exc
from fastapi.responses import Response
import re


class PotcarUpload(BaseModel):
    base64: str = Field(min_length=1, max_length=44739244)


class InputRequest(BaseModel):
    project: dict
    node_id: str
    reason: str = ''


class LibraryConfig(BaseModel):
    path: str


class LibraryAssembly(BaseModel):
    species: list[str]
    selections: dict[str, str]
    functional: str


@app.get('/api/potcar-library')
def read_library():
    try:
        return potcar_library.catalog()
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.put('/api/potcar-library')
def configure_library(payload: LibraryConfig):
    try:
        return potcar_library.configure(payload.path)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/api/potcar-library/assemble')
def assemble_library(payload: LibraryAssembly):
    try:
        return potcar_library.assemble(payload.species, payload.selections, payload.functional)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


class PotcarAssembly(BaseModel):
    files: list[str] = Field(max_length=118)
    species: list[str] = Field(max_length=1500)


@app.post('/api/potcars/assemble')
def assemble_potcars(payload: PotcarAssembly):
    try:
        if sum(len(s) for s in payload.files) > 44739244:
            raise ValueError('总文件上限 32 MiB')
        return assemble_potcar(payload.files, payload.species)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get('/api/inputs/history')
def input_history(project_id: str):
    return history(project_id)


@app.post('/api/potcars')
def import_potcar(payload: PotcarUpload):
    try:
        return upload_potcar(payload.base64)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/api/inputs/preview')
def preview_inputs(payload: InputRequest):
    try:
        return generate(payload.project, payload.node_id)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/api/inputs/freeze')
def freeze_inputs(payload: InputRequest):
    try:
        return freeze(payload.project, payload.node_id, payload.reason)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get('/api/inputs/{key}/download')
def download_inputs(key: str):
    if not re.fullmatch('[a-f0-9]{64}', key):
        raise HTTPException(404, '输入包不存在')
    try:
        raw = (ROOT / 'inputs' / (key + '.zip')).read_bytes()
        if digest(raw) != key:
            raise ValueError('输入包校验失败')
        return Response(raw, media_type='application/zip', headers={'Content-Disposition': f'attachment; filename="fpilot-{key[:16]}.zip"'})
    except (OSError, ValueError) as exc:
        raise HTTPException(404, str(exc)) from exc

# GUI and clients share the same import, provenance and preview validators.
from . import job_templates

class TemplateImport(BaseModel):
    script: str = Field(min_length=1, max_length=65536)
    name: str
    scheduler: Literal['pbs', 'slurm']
    alias: str
    program: Literal['vasp_std', 'vasp_ncl', 'gpu_vasp', 'wannier90', 'other']
    source: str
    prepared: bool = False
    parent_id: Optional[str] = None

class TemplatePreview(BaseModel):
    overrides: dict = Field(default_factory=dict)
    alias: Optional[str] = None
    config: Optional[dict] = None

@app.get('/api/job-templates')
def template_list():
    return job_templates.list_templates()

@app.post('/api/job-templates')
def template_import(payload: TemplateImport):
    try:
        return job_templates.save_template(**payload.model_dump())
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc

@app.get('/api/job-templates/verified')
def verified_template_list():
    from .verified_templates import catalog
    return catalog()

@app.post('/api/job-templates/verified/{key}/import')
def verified_template_import(key: str):
    from .verified_templates import import_verified
    try:
        return import_verified(key)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc

@app.get('/api/job-templates/{key}')
def template_get(key: str):
    try:
        return job_templates.get_template(key)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc

@app.post('/api/job-templates/{key}/preview')
def template_preview(key: str, payload: TemplatePreview):
    try:
        return job_templates.render_template(key, payload.overrides, payload.alias, payload.config)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc

# New optional extensions use one stable file protocol.
from .extension_api import router as extension_router
app.include_router(extension_router)
from .analysis_dataset_api import router as analysis_dataset_router
app.include_router(analysis_dataset_router)

# Configuration and execution ownership are separate from editable workspace data.
class SourceRequest(BaseModel):
    project: dict
    node_id: str
    execution_scope: Optional[str] = None

@app.post('/api/calculation-sources/resolve')
def calculation_sources(payload: SourceRequest):
    from . import source_contracts
    try:return source_contracts.resolve(payload.project,payload.node_id,payload.execution_scope)
    except (ValueError,KeyError,OSError) as exc:raise HTTPException(422,str(exc)) from exc

class SourceRefreshRequest(SourceRequest):
    attempt_id: str


@app.post('/api/calculation-sources/refresh')
def calculation_source_refresh(payload: SourceRefreshRequest):
    from . import source_contracts, artifacts
    try:
        selection = source_contracts.resolve(payload.project, payload.node_id, payload.execution_scope)
        if payload.attempt_id not in {c['id'] for c in selection['candidates']}:
            raise ValueError('所选运行不属于本节点可审核的来源')
        artifacts.refresh(payload.attempt_id)
        return source_contracts.resolve(payload.project, payload.node_id, payload.execution_scope)
    except (ValueError, KeyError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


class ChainDraftRequest(BaseModel):
    project: dict


@app.post('/api/chains/{key}/draft-differences')
def chain_draft_differences(key: str, payload: ChainDraftRequest):
    from . import source_contracts, chain_runs
    try:
        run = chain_runs.get(key)
        if run['project_id'] != payload.project['id']:
            raise ValueError('计算链不属于当前项目')
        effective, _ = chain_runs._workflow_project(payload.project, run['node_ids'])
        differences = []
        for step in run['steps']:
            if not any(n['id'] == step['node_id'] for n in effective['nodes']):
                differences.append({'node_id': step['node_id'], 'label': step['label'], 'field': '节点', 'frozen': '存在', 'draft': '已移除'})
                continue
            result = source_contracts.draft_differences(effective, step['node_id'], step['bundle_id'])
            differences.extend({**item, 'node_id': step['node_id'], 'label': step['label']} for item in result['differences'])
        return {'run_id': key, 'differences': differences}
    except (ValueError, KeyError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/api/inputs/{key}/draft-differences')
def input_draft_differences(key: str, payload: SourceRequest):
    from . import source_contracts
    try:
        return source_contracts.draft_differences(payload.project, payload.node_id, key)
    except (ValueError, KeyError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/api/branch-binding')
def branch_binding(payload: SourceRequest):
    from . import branch_bindings
    try:return branch_bindings.inspect(payload.project['id'],branch_bindings.scopes(payload.project,payload.node_id))
    except (ValueError,KeyError,OSError) as exc:raise HTTPException(422,str(exc)) from exc

@app.get('/api/workbench-settings')
def workbench_settings_read():
    from . import workbench_settings
    return workbench_settings.read()

@app.put('/api/workbench-settings')
def workbench_settings_save(payload: dict):
    from . import workbench_settings
    try:return workbench_settings.save(payload)
    except (ValueError,KeyError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


class WannierFreeze(BaseModel):
    project: dict
    node_id: str
    parent_id: str
    alias: str
    input_reason: str
    win: str
    seed: str = 'wannier90'
    energy_reference: Optional[dict] = None
    relative_windows: Optional[dict] = None
    band_path: Optional[dict] = None

@app.post('/api/wannier90/configurations/freeze')
def wannier_configuration_freeze(payload: WannierFreeze):
    try:return wannier_execution.freeze_configuration(**payload.model_dump())
    except (ValueError,KeyError,OSError) as exc:raise HTTPException(422,str(exc)) from exc

@app.get('/api/wannier90/configurations')
def wannier_configurations(project_id:str,node_id:str):
    return wannier_execution.configurations(project_id,node_id)


class LegacyBranchRequest(SourceRequest):
    alias: str

@app.post('/api/branch-binding/resolve-legacy')
def branch_resolve_legacy(payload: LegacyBranchRequest):
    from . import branch_bindings
    try:return branch_bindings.resolve_legacy(payload.project,payload.node_id,payload.alias)
    except (ValueError,KeyError,OSError) as exc:raise HTTPException(422,str(exc)) from exc


@app.middleware('http')
async def remote_action_timing(request,call_next):
    import time
    from . import remote_transport,remote_performance
    if request.url.path.startswith('/api/remote-performance'):return await call_next(request)
    start=time.monotonic()
    with remote_transport.operation(request.method+' '+request.url.path) as scope:
        response=await call_next(request)
        if request.method!='GET' or scope['events']:
            remote_performance.record(scope['label'],time.monotonic()-start,list(scope['events']))
        return response


@app.get('/api/remote-performance')
def remote_performance_read():
    from . import remote_performance
    return remote_performance.read()


@app.post('/api/remote-performance/clear-cache')
def remote_performance_clear():
    from . import remote_performance
    try:return remote_performance.clear_cache()
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc


@app.get('/api/local-setup')
def local_setup_read():
    from . import local_setup
    return local_setup.read()


@app.put('/api/local-setup')
def local_setup_save(payload:dict):
    from . import local_setup
    try:return local_setup.save(payload)
    except (OSError,ValueError) as exc:raise HTTPException(422,str(exc)) from exc


@app.get('/api/sites')
def site_records():
    from . import site_registration
    return {'hosts':site_registration.records(),'ssh_aliases':site_registration.ssh_aliases()}


@app.post('/api/sites')
def site_register(payload:dict):
    from . import site_registration
    try:return site_registration.register(payload)
    except (OSError,ValueError) as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/sites/check-ssh')
def site_check_ssh(payload:dict):
    from . import site_registration
    try:return site_registration.check_ssh(payload.get('alias'))
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc


@app.post('/api/sites/test-connection')
def site_test_connection(payload:dict):
    from . import site_registration
    try:return site_registration.test_connection(payload.get('alias'),payload.get('python',''),payload.get('remote_workspace_root'))
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc


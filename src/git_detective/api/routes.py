"""The REST endpoints.

These are plain ``def`` functions, so FastAPI runs them in a thread pool: other
requests are still served while a slow analysis runs. Only ``POST /analyses``
touches Git; reading the history uses nothing but the database.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from git_detective import __version__
from git_detective.api.config import RepositoryId
from git_detective.api.errors import ApiError
from git_detective.api.registry import RepositoryRegistry
from git_detective.api.runner import AnalysisRunner
from git_detective.api.schemas import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    AnalysisDetail,
    AnalysisList,
    AnalysisRequest,
    AnalysisSummary,
    AnalysisType,
    AuthorsSection,
    CouplingSection,
    ErrorResponse,
    HealthResponse,
    HotspotsSection,
    RepositoryList,
    RepositorySummary,
)
from git_detective.reporting import (
    AUTHORS_DISCLAIMER,
    COUPLING_DISCLAIMER,
    SCORE_DISCLAIMER,
)
from git_detective.storage import AnalysisRecord, AnalysisStore, StoredAnalysis

API_PREFIX = "/api/v1"

router = APIRouter(prefix=API_PREFIX)


def _store(request: Request) -> AnalysisStore:
    return request.app.state.store


def _registry(request: Request) -> RepositoryRegistry:
    return request.app.state.registry


def _runner(request: Request) -> AnalysisRunner:
    return request.app.state.runner


Store = Annotated[AnalysisStore, Depends(_store)]
Registry = Annotated[RepositoryRegistry, Depends(_registry)]
Runner = Annotated[AnalysisRunner, Depends(_runner)]


def _errors(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """Document error responses in the OpenAPI schema."""
    return {code: {"model": ErrorResponse} for code in status_codes}


@router.get("/health", responses=_errors(503), tags=["system"])
def health(store: Store) -> HealthResponse:
    """Check that the server is running and its database can be queried."""
    store.check()
    return HealthResponse(status="ok", version=__version__, database="ok")


@router.get("/repositories", tags=["repositories"])
def list_repositories(registry: Registry) -> RepositoryList:
    """List the repositories that can be analyzed (ids only, never paths)."""
    items = [
        RepositorySummary(
            id=repository_id, available=registry.is_available(repository_id)
        )
        for repository_id in registry.ids()
    ]
    return RepositoryList(items=items, total=len(items))


@router.post(
    "/analyses",
    status_code=status.HTTP_201_CREATED,
    responses=_errors(404, 409, 422, 429, 503),
    tags=["analyses"],
)
def create_analysis(
    body: AnalysisRequest, response: Response, runner: Runner
) -> AnalysisDetail:
    """Analyze an allowed repository now and store the result.

    The analysis runs inside this request and can take a while: about one Git
    process per commit. The response always contains the finished report.
    """
    analysis = runner.execute(
        repository_id=body.repository,
        analysis_type=body.type,
        config=body.parameters.to_config(),
        parameters=body.parameters.model_dump(mode="json"),
    )
    response.headers["Location"] = f"{API_PREFIX}/analyses/{analysis.record.id}"
    return _detail(analysis)


@router.get("/analyses", responses=_errors(422, 503), tags=["analyses"])
def list_analyses(
    store: Store,
    repository: RepositoryId | None = None,
    analysis_type: Annotated[AnalysisType | None, Query(alias="type")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AnalysisList:
    """List stored analyses, newest first, without their reports."""
    records, total = store.list_analyses(
        repository_id=repository,
        analysis_type=analysis_type,
        limit=limit,
        offset=offset,
    )
    return AnalysisList(
        items=[_summary(record) for record in records],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/analyses/{analysis_id}", responses=_errors(404, 422, 503), tags=["analyses"]
)
def get_analysis(analysis_id: UUID, store: Store) -> AnalysisDetail:
    """Return a stored analysis with its full report. Git is not used."""
    return _detail(_get_stored(store, analysis_id))


@router.get(
    "/analyses/{analysis_id}/hotspots",
    responses=_errors(404, 409, 422, 503),
    tags=["analyses"],
)
def get_hotspots(analysis_id: UUID, store: Store) -> HotspotsSection:
    """Return the ranked files of a stored ``hotspots`` analysis."""
    analysis = _get_stored(store, analysis_id, expected_type="hotspots")
    return HotspotsSection(
        analysis=_summary(analysis.record),
        summary=analysis.result["summary"],
        disclaimer=SCORE_DISCLAIMER,
        files=analysis.result["files"],
    )


@router.get(
    "/analyses/{analysis_id}/authors",
    responses=_errors(404, 409, 422, 503),
    tags=["analyses"],
)
def get_authors(analysis_id: UUID, store: Store) -> AuthorsSection:
    """Return the contributors of a stored ``authors`` analysis."""
    analysis = _get_stored(store, analysis_id, expected_type="authors")
    return AuthorsSection(
        analysis=_summary(analysis.record),
        summary=analysis.result["summary"],
        disclaimer=AUTHORS_DISCLAIMER,
        authors=analysis.result["authors"],
    )


@router.get(
    "/analyses/{analysis_id}/coupling",
    responses=_errors(404, 409, 422, 503),
    tags=["analyses"],
)
def get_coupling(analysis_id: UUID, store: Store) -> CouplingSection:
    """Return the file pairs and graph of a stored ``coupling`` analysis."""
    analysis = _get_stored(store, analysis_id, expected_type="coupling")
    return CouplingSection(
        analysis=_summary(analysis.record),
        summary=analysis.result["summary"],
        disclaimer=COUPLING_DISCLAIMER,
        pairs=analysis.result["pairs"],
        graph=analysis.result["graph"],
    )


@router.delete(
    "/analyses/{analysis_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_errors(404, 422, 503),
    tags=["analyses"],
)
def delete_analysis(analysis_id: UUID, store: Store) -> None:
    """Delete a stored analysis. The repository itself is not touched."""
    if not store.delete(str(analysis_id)):
        raise _analysis_not_found()


def _get_stored(
    store: AnalysisStore, analysis_id: UUID, *, expected_type: str | None = None
) -> StoredAnalysis:
    analysis = store.get(str(analysis_id))
    if analysis is None:
        raise _analysis_not_found()
    if expected_type is not None and analysis.record.analysis_type != expected_type:
        raise ApiError(
            409,
            "analysis_type_mismatch",
            f"This is a '{analysis.record.analysis_type}' analysis, "
            f"not '{expected_type}'.",
        )
    return analysis


def _analysis_not_found() -> ApiError:
    return ApiError(404, "analysis_not_found", "There is no analysis with this id.")


def _summary(record: AnalysisRecord) -> AnalysisSummary:
    return AnalysisSummary(
        id=UUID(record.id),
        repository=record.repository_id,
        type=record.analysis_type,
        parameters=record.parameters,
        executed_at=record.executed_at,
        head_sha=record.head_sha,
        commits_analyzed=record.commits_analyzed,
    )


def _detail(analysis: StoredAnalysis) -> AnalysisDetail:
    summary = _summary(analysis.record)
    return AnalysisDetail(**summary.model_dump(), report=analysis.result)

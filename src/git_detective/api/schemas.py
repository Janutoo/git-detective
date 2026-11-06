"""Request and response bodies of the REST API.

The limits below are part of the API contract and appear in the OpenAPI schema.
They bound the cost of one request: reading history starts one Git process per
commit, and coupling is quadratic in the number of files per commit.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from git_detective.api.config import RepositoryId
from git_detective.core import (
    DEFAULT_COMMIT_LIMIT,
    DEFAULT_MAX_FILES_PER_COMMIT,
    DEFAULT_MIN_COCHANGES,
    DEFAULT_RECENT_DAYS,
    AuthorSort,
    PathFilter,
)
from git_detective.exceptions import IgnorePatternError
from git_detective.service import (
    DEFAULT_COUPLING_TOP,
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_TOP,
    AuthorsConfig,
    CouplingConfig,
    HotspotsConfig,
)

MAX_COMMITS = 5000
MAX_TOP = 1000
MAX_EXCLUDE_PATTERNS = 100
MAX_EXCLUDE_PATTERN_LENGTH = 256
MAX_RECENT_DAYS = 3650
MAX_MIN_COCHANGES = 10_000
MAX_FILES_PER_COMMIT = 200
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20

AnalysisType = Literal["hotspots", "authors", "coupling"]

CommitLimit = Annotated[int, Field(strict=True, ge=1, le=MAX_COMMITS)]
Top = Annotated[int, Field(strict=True, ge=1, le=MAX_TOP)]


def _check_exclude_patterns(patterns: list[str]) -> list[str]:
    if any(not pattern.isprintable() for pattern in patterns):
        raise ValueError("Exclude patterns must not contain control characters")
    try:
        PathFilter(patterns)
    except IgnorePatternError as exc:
        raise ValueError(str(exc)) from exc
    return patterns


ExcludePatterns = Annotated[
    list[
        Annotated[
            str,
            StringConstraints(min_length=1, max_length=MAX_EXCLUDE_PATTERN_LENGTH),
        ]
    ],
    Field(max_length=MAX_EXCLUDE_PATTERNS),
    AfterValidator(_check_exclude_patterns),
]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HotspotsParameters(_Request):
    limit: CommitLimit = DEFAULT_COMMIT_LIMIT
    top: Top = DEFAULT_TOP
    exclude: ExcludePatterns = []
    recent_days: Annotated[int, Field(strict=True, ge=1, le=MAX_RECENT_DAYS)] = (
        DEFAULT_RECENT_DAYS
    )

    def to_config(self) -> HotspotsConfig:
        return HotspotsConfig(
            limit=self.limit,
            top=self.top,
            exclude=tuple(self.exclude),
            recent_days=self.recent_days,
        )


class AuthorsParameters(_Request):
    """E-mail addresses are deliberately not available through the API."""

    limit: CommitLimit = DEFAULT_HISTORY_LIMIT
    top: Top = DEFAULT_TOP
    exclude: ExcludePatterns = []
    sort: AuthorSort = AuthorSort.COMMITS

    def to_config(self) -> AuthorsConfig:
        return AuthorsConfig(
            limit=self.limit,
            top=self.top,
            exclude=tuple(self.exclude),
            sort=self.sort,
        )


class CouplingParameters(_Request):
    limit: CommitLimit = DEFAULT_HISTORY_LIMIT
    top: Top = DEFAULT_COUPLING_TOP
    exclude: ExcludePatterns = []
    min_cochanges: Annotated[int, Field(strict=True, ge=1, le=MAX_MIN_COCHANGES)] = (
        DEFAULT_MIN_COCHANGES
    )
    max_files_per_commit: Annotated[
        int, Field(strict=True, ge=2, le=MAX_FILES_PER_COMMIT)
    ] = DEFAULT_MAX_FILES_PER_COMMIT

    def to_config(self) -> CouplingConfig:
        return CouplingConfig(
            limit=self.limit,
            top=self.top,
            exclude=tuple(self.exclude),
            min_cochanges=self.min_cochanges,
            max_files_per_commit=self.max_files_per_commit,
        )


class HotspotsRequest(_Request):
    repository: RepositoryId
    type: Literal["hotspots"]
    parameters: HotspotsParameters = HotspotsParameters()


class AuthorsRequest(_Request):
    repository: RepositoryId
    type: Literal["authors"]
    parameters: AuthorsParameters = AuthorsParameters()


class CouplingRequest(_Request):
    repository: RepositoryId
    type: Literal["coupling"]
    parameters: CouplingParameters = CouplingParameters()


AnalysisRequest = Annotated[
    HotspotsRequest | AuthorsRequest | CouplingRequest,
    Field(discriminator="type"),
]


class ErrorDetail(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] = []


class ErrorResponse(BaseModel):
    """The body of every error response."""

    error: ErrorBody


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    database: Literal["ok"]


class RepositorySummary(BaseModel):
    id: str
    available: bool


class RepositoryList(BaseModel):
    items: list[RepositorySummary]
    total: int


class AnalysisSummary(BaseModel):
    id: UUID
    repository: str
    type: str
    parameters: dict[str, Any]
    executed_at: datetime
    head_sha: str | None
    commits_analyzed: int


class AnalysisDetail(AnalysisSummary):
    report: dict[str, Any]


class AnalysisList(BaseModel):
    items: list[AnalysisSummary]
    total: int
    limit: int
    offset: int


class ScoreComponents(BaseModel):
    frequency: float
    churn: float
    recency: float


class HotspotFile(BaseModel):
    rank: int
    path: str
    commits: int
    churn: int
    recent_commits: int
    score: float
    components: ScoreComponents


class AuthorFile(BaseModel):
    path: str
    commits: int
    churn: int


class AuthorActivity(BaseModel):
    rank: int
    name: str
    commits: int
    churn: int
    added: int
    deleted: int
    files_touched: int
    active_days: int
    first_commit: datetime
    last_commit: datetime
    top_files: list[AuthorFile]
    activity_by_month: dict[str, int]


class CoupledPair(BaseModel):
    rank: int
    file_a: str
    file_b: str
    cochanges: int
    commits_a: int
    commits_b: int
    jaccard: float


class GraphNode(BaseModel):
    id: str
    commits: int


class GraphEdge(BaseModel):
    source: str
    target: str
    cochanges: int
    jaccard: float


class CouplingGraph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class _SectionResponse(BaseModel):
    analysis: AnalysisSummary
    summary: dict[str, Any]
    disclaimer: str


class HotspotsSection(_SectionResponse):
    files: list[HotspotFile]


class AuthorsSection(_SectionResponse):
    authors: list[AuthorActivity]


class CouplingSection(_SectionResponse):
    pairs: list[CoupledPair]
    graph: CouplingGraph

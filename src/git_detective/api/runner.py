"""Run one analysis to completion and store its result.

Analyses run synchronously, inside the request: reading history starts one Git
process per commit, so a request can take a while and the number of analyses
running at once is limited. Nothing here depends on HTTP, so a background
worker could later call :meth:`AnalysisRunner.execute` in the same way.
"""

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from git_detective.api.registry import RepositoryRegistry
from git_detective.exceptions import GitDetectiveError
from git_detective.service import AnalysisConfig, run_analysis
from git_detective.storage import AnalysisRecord, AnalysisStore, StoredAnalysis

MAX_RESULT_BYTES = 5 * 1024 * 1024


class AnalysisBusyError(GitDetectiveError):
    """The maximum number of analyses is already running."""


class AnalysisTooLargeError(GitDetectiveError):
    """The result of an analysis is too large to store."""


class AnalysisRunner:
    def __init__(
        self,
        registry: RepositoryRegistry,
        store: AnalysisStore,
        *,
        max_concurrent: int,
        max_result_bytes: int = MAX_RESULT_BYTES,
    ) -> None:
        self._registry = registry
        self._store = store
        self._slots = threading.BoundedSemaphore(max_concurrent)
        self._max_result_bytes = max_result_bytes

    def execute(
        self,
        *,
        repository_id: str,
        analysis_type: str,
        config: AnalysisConfig,
        parameters: dict[str, Any],
    ) -> StoredAnalysis:
        """Analyze an allowed repository and save the result.

        ``parameters`` is the JSON form of ``config``, stored with the result.

        Raises:
            RepositoryNotFoundError: If the id is not on the allowlist.
            AnalysisBusyError: If no analysis slot is free.
            AnalysisTooLargeError: If the result exceeds the size limit.
            GitDetectiveError: If the repository cannot be read or the result
                cannot be stored.
        """
        path = self._registry.resolve(repository_id)
        if not self._slots.acquire(blocking=False):
            raise AnalysisBusyError("All analysis slots are in use")
        try:
            executed_at = datetime.now(UTC)
            result = run_analysis(
                path,
                config,
                now=executed_at,
                label=PurePosixPath(repository_id),
                strict=True,
            )
        finally:
            self._slots.release()

        report = _replace_surrogates(result.report)
        size = len(json.dumps(report, ensure_ascii=False).encode("utf-8"))
        if size > self._max_result_bytes:
            raise AnalysisTooLargeError(
                f"Result of {size} bytes exceeds {self._max_result_bytes} bytes"
            )

        analysis = StoredAnalysis(
            record=AnalysisRecord(
                id=str(uuid.uuid4()),
                repository_id=repository_id,
                analysis_type=analysis_type,
                parameters=parameters,
                executed_at=executed_at,
                head_sha=result.head_sha,
                commits_analyzed=result.commits_analyzed,
            ),
            result=report,
        )
        self._store.save(analysis)
        return analysis


def _replace_surrogates(value: Any) -> Any:
    """Make every string in a report encodable as UTF-8.

    Git file names are bytes. Names that are not valid UTF-8 reach Python with
    lone surrogates, which can be neither stored as text nor sent as JSON, so
    they are written out as visible escapes such as ``caf\\udce9.txt``.
    """
    if isinstance(value, str):
        return value.encode("utf-8", "backslashreplace").decode("utf-8")
    if isinstance(value, dict):
        return {
            _replace_surrogates(key): _replace_surrogates(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_surrogates(item) for item in value]
    return value

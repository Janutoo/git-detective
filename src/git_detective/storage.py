"""Keep finished analyses in a SQLite database.

Stored reports can be read back without touching the analyzed repository.

Every operation opens its own short-lived connection, so one store can be used
from many threads. The schema version lives in ``PRAGMA user_version``; to
change the schema, append a list of statements to :data:`MIGRATIONS`.
"""

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from git_detective.exceptions import StorageError

BUSY_TIMEOUT_SECONDS = 5.0

# Applied in order; never edit a released entry, only append new ones.
MIGRATIONS: list[list[str]] = [
    [
        """
        CREATE TABLE analyses (
            id               TEXT PRIMARY KEY,
            repository_id    TEXT NOT NULL,
            analysis_type    TEXT NOT NULL,
            parameters       TEXT NOT NULL CHECK (json_valid(parameters)),
            executed_at      TEXT NOT NULL,
            head_sha         TEXT,
            commits_analyzed INTEGER NOT NULL CHECK (commits_analyzed >= 0),
            result           TEXT NOT NULL CHECK (json_valid(result))
        )
        """,
        "CREATE INDEX idx_analyses_executed ON analyses (executed_at DESC, id)",
        """
        CREATE INDEX idx_analyses_repo
            ON analyses (repository_id, executed_at DESC, id)
        """,
    ],
]

_RECORD_COLUMNS = (
    "id, repository_id, analysis_type, parameters, executed_at, head_sha, "
    "commits_analyzed"
)


@dataclass(frozen=True, slots=True)
class AnalysisRecord:
    """What is known about a stored analysis, without its (large) result."""

    id: str
    repository_id: str
    analysis_type: str
    parameters: dict[str, Any]
    executed_at: datetime
    head_sha: str | None
    commits_analyzed: int


@dataclass(frozen=True, slots=True)
class StoredAnalysis:
    record: AnalysisRecord
    result: dict[str, Any]


class AnalysisStore:
    """Saves, finds and deletes analyses in one SQLite database file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def initialize(self) -> None:
        """Create the database if needed and bring its schema up to date.

        All pending migrations run in one transaction, so a failure leaves the
        database as it was.

        Raises:
            StorageError: If the database cannot be opened or was created by
                a newer version of Git Detective.
        """
        self._create_private_file()
        with self._transaction() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > len(MIGRATIONS):
                raise StorageError(
                    f"Database schema version {version} is newer than the "
                    f"supported version {len(MIGRATIONS)}"
                )
            for number, statements in enumerate(
                MIGRATIONS[version:], start=version + 1
            ):
                for statement in statements:
                    connection.execute(statement)
                # PRAGMA takes no bound parameters; `number` is a trusted int.
                connection.execute(f"PRAGMA user_version = {number:d}")

    def check(self) -> None:
        """Raise :class:`StorageError` if the database cannot be queried."""
        with self._connect() as connection:
            connection.execute("SELECT 1 FROM analyses LIMIT 1").fetchone()

    def save(self, analysis: StoredAnalysis) -> None:
        record = analysis.record
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO analyses (
                    id, repository_id, analysis_type, parameters, executed_at,
                    head_sha, commits_analyzed, result
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.repository_id,
                    record.analysis_type,
                    json.dumps(record.parameters, ensure_ascii=False),
                    _timestamp(record.executed_at),
                    record.head_sha,
                    record.commits_analyzed,
                    json.dumps(analysis.result, ensure_ascii=False),
                ),
            )

    def get(self, analysis_id: str) -> StoredAnalysis | None:
        """Return the analysis with this id, or None if there is none."""
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {_RECORD_COLUMNS}, result FROM analyses WHERE id = ?",
                (analysis_id,),
            ).fetchone()
        if row is None:
            return None
        return StoredAnalysis(record=_to_record(row), result=_load_json(row["result"]))

    def list_analyses(
        self,
        *,
        repository_id: str | None = None,
        analysis_type: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[AnalysisRecord], int]:
        """Return one page of analyses, newest first, and the total match count."""
        # Only these fixed clauses ever become SQL; values are bound parameters.
        clauses: list[str] = []
        values: list[str] = []
        if repository_id is not None:
            clauses.append("repository_id = ?")
            values.append(repository_id)
        if analysis_type is not None:
            clauses.append("analysis_type = ?")
            values.append(analysis_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with self._connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM analyses {where}", values
            ).fetchone()[0]
            rows = connection.execute(
                f"SELECT {_RECORD_COLUMNS} FROM analyses {where} "
                "ORDER BY executed_at DESC, id LIMIT ? OFFSET ?",
                [*values, limit, offset],
            ).fetchall()
        return [_to_record(row) for row in rows], total

    def delete(self, analysis_id: str) -> bool:
        """Delete an analysis; return False if it did not exist."""
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM analyses WHERE id = ?", (analysis_id,)
            )
            return cursor.rowcount > 0

    def _create_private_file(self) -> None:
        """Create the file readable only by its owner: reports name real people."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.close(os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600))
        except OSError as exc:
            raise StorageError(f"Cannot create database: {exc}") from exc

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection, turning any SQLite failure into StorageError."""
        connection = None
        try:
            # isolation_level=None: transactions are started explicitly, and
            # only by _transaction().
            connection = sqlite3.connect(
                self._path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None
            )
            connection.row_factory = sqlite3.Row
            yield connection
        except sqlite3.Error as exc:
            raise StorageError(f"Database error: {exc}") from exc
        finally:
            if connection is not None:
                connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """Commit on success and roll back on any exception."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")


def _timestamp(value: datetime) -> str:
    """UTC with a fixed format, so timestamps sort correctly as text."""
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _load_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise StorageError(f"Stored JSON is invalid: {exc}") from exc


def _to_record(row: sqlite3.Row) -> AnalysisRecord:
    return AnalysisRecord(
        id=row["id"],
        repository_id=row["repository_id"],
        analysis_type=row["analysis_type"],
        parameters=_load_json(row["parameters"]),
        executed_at=datetime.fromisoformat(row["executed_at"]),
        head_sha=row["head_sha"],
        commits_analyzed=row["commits_analyzed"],
    )

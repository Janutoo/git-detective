"""The SQLite analysis store, always on a temporary database."""

import sqlite3
import stat
import threading
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from git_detective import storage
from git_detective.exceptions import StorageError
from git_detective.storage import AnalysisRecord, AnalysisStore, StoredAnalysis

BASE_TIME = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)


def make_analysis(
    *,
    repository_id: str = "demo",
    analysis_type: str = "hotspots",
    executed_at: datetime = BASE_TIME,
    head_sha: str | None = "a" * 40,
    result: dict | None = None,
) -> StoredAnalysis:
    return StoredAnalysis(
        record=AnalysisRecord(
            id=str(uuid.uuid4()),
            repository_id=repository_id,
            analysis_type=analysis_type,
            parameters={"limit": 10, "exclude": ["*.lock"]},
            executed_at=executed_at,
            head_sha=head_sha,
            commits_analyzed=7,
        ),
        result=result if result is not None else {"files": [{"path": "zażółć.py"}]},
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "nested" / "analyses.db"


@pytest.fixture
def store(db_path: Path) -> AnalysisStore:
    analysis_store = AnalysisStore(db_path)
    analysis_store.initialize()
    return analysis_store


def user_version(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        return connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        connection.close()


class TestSaveAndGet:
    def test_round_trip(self, store: AnalysisStore) -> None:
        analysis = make_analysis()

        store.save(analysis)

        assert store.get(analysis.record.id) == analysis

    def test_missing_head_sha(self, store: AnalysisStore) -> None:
        analysis = make_analysis(head_sha=None)

        store.save(analysis)

        assert store.get(analysis.record.id).record.head_sha is None

    def test_missing_analysis(self, store: AnalysisStore) -> None:
        assert store.get(str(uuid.uuid4())) is None

    def test_timestamps_are_normalized_to_utc(self, store: AnalysisStore) -> None:
        offset = datetime(2024, 1, 1, 14, 0, tzinfo=timezone(timedelta(hours=2)))
        analysis = make_analysis(executed_at=offset)

        store.save(analysis)

        stored = store.get(analysis.record.id).record.executed_at
        assert stored == offset
        assert stored.utcoffset() == timedelta(0)

    def test_duplicate_id_fails_and_keeps_the_original(
        self, store: AnalysisStore
    ) -> None:
        analysis = make_analysis()
        store.save(analysis)
        duplicate = StoredAnalysis(record=analysis.record, result={"other": True})

        with pytest.raises(StorageError):
            store.save(duplicate)

        assert store.get(analysis.record.id) == analysis


class TestListing:
    @pytest.fixture
    def stored(self, store: AnalysisStore) -> list[StoredAnalysis]:
        analyses = [
            make_analysis(
                repository_id="one" if i % 2 else "two",
                analysis_type="authors" if i < 2 else "coupling",
                executed_at=BASE_TIME + timedelta(minutes=i),
            )
            for i in range(5)
        ]
        for analysis in analyses:
            store.save(analysis)
        return analyses

    def test_newest_first(
        self, store: AnalysisStore, stored: list[StoredAnalysis]
    ) -> None:
        records, total = store.list_analyses()

        assert total == 5
        assert [r.id for r in records] == [a.record.id for a in reversed(stored)]

    def test_pagination(
        self, store: AnalysisStore, stored: list[StoredAnalysis]
    ) -> None:
        records, total = store.list_analyses(limit=2, offset=1)

        assert total == 5
        assert [r.id for r in records] == [stored[3].record.id, stored[2].record.id]

    def test_offset_past_the_end(
        self, store: AnalysisStore, stored: list[StoredAnalysis]
    ) -> None:
        assert store.list_analyses(offset=100) == ([], 5)

    @pytest.mark.usefixtures("stored")
    def test_filter_by_repository(self, store: AnalysisStore) -> None:
        records, total = store.list_analyses(repository_id="one")

        assert total == 2
        assert {r.repository_id for r in records} == {"one"}

    @pytest.mark.usefixtures("stored")
    def test_filter_by_type(self, store: AnalysisStore) -> None:
        records, total = store.list_analyses(analysis_type="authors")

        assert total == 2
        assert {r.analysis_type for r in records} == {"authors"}

    @pytest.mark.usefixtures("stored")
    def test_filters_combine(self, store: AnalysisStore) -> None:
        records, total = store.list_analyses(
            repository_id="two", analysis_type="coupling"
        )

        assert total == 2
        assert {(r.repository_id, r.analysis_type) for r in records} == {
            ("two", "coupling")
        }

    @pytest.mark.usefixtures("stored")
    @pytest.mark.parametrize(
        "attack", ["x' OR '1'='1", "'; DROP TABLE analyses; --", "%", "one' --"]
    )
    def test_filter_values_are_never_sql(
        self, store: AnalysisStore, attack: str
    ) -> None:
        assert store.list_analyses(repository_id=attack) == ([], 0)
        assert store.list_analyses(analysis_type=attack) == ([], 0)
        assert store.list_analyses()[1] == 5


class TestDelete:
    def test_removes_only_the_given_analysis(self, store: AnalysisStore) -> None:
        kept, removed = make_analysis(), make_analysis()
        store.save(kept)
        store.save(removed)

        assert store.delete(removed.record.id) is True

        assert store.get(removed.record.id) is None
        assert store.get(kept.record.id) == kept

    def test_missing_analysis(self, store: AnalysisStore) -> None:
        assert store.delete(str(uuid.uuid4())) is False


class TestSchema:
    def test_creates_current_version(self, store: AnalysisStore, db_path: Path) -> None:
        assert user_version(db_path) == len(storage.MIGRATIONS)

    def test_initialize_is_idempotent(self, db_path: Path) -> None:
        store = AnalysisStore(db_path)
        store.initialize()
        analysis = make_analysis()
        store.save(analysis)

        store.initialize()

        assert store.get(analysis.record.id) == analysis

    def test_applies_new_migrations_and_keeps_data(
        self,
        store: AnalysisStore,
        db_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        analysis = make_analysis()
        store.save(analysis)
        migrations = [
            *storage.MIGRATIONS,
            ["ALTER TABLE analyses ADD COLUMN status TEXT DEFAULT 'done'"],
        ]
        monkeypatch.setattr(storage, "MIGRATIONS", migrations)

        store.initialize()

        assert user_version(db_path) == len(migrations)
        assert store.get(analysis.record.id) == analysis

    def test_failed_migration_rolls_back_completely(
        self,
        store: AnalysisStore,
        db_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        version = user_version(db_path)
        monkeypatch.setattr(
            storage,
            "MIGRATIONS",
            [*storage.MIGRATIONS, ["CREATE TABLE extra (x)", "NOT VALID SQL"]],
        )

        with pytest.raises(StorageError):
            store.initialize()

        assert user_version(db_path) == version
        connection = sqlite3.connect(db_path)
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'extra'"
        ).fetchall()
        connection.close()
        assert tables == []

    def test_refuses_a_database_from_a_newer_version(self, db_path: Path) -> None:
        store = AnalysisStore(db_path)
        store.initialize()
        connection = sqlite3.connect(db_path)
        connection.execute("PRAGMA user_version = 999")
        connection.close()

        with pytest.raises(StorageError, match="newer"):
            store.initialize()

        assert user_version(db_path) == 999

    def test_invalid_json_is_rejected_by_the_database(
        self, store: AnalysisStore, db_path: Path
    ) -> None:
        connection = sqlite3.connect(db_path)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO analyses "
                "VALUES ('x', 'r', 't', 'not json', 'd', NULL, 0, '{}')"
            )
        connection.close()


class TestFailures:
    def test_file_that_is_not_a_database(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.db"
        path.write_bytes(b"this is not a database at all" * 100)

        with pytest.raises(StorageError):
            AnalysisStore(path).initialize()

    def test_unusable_location(self, tmp_path: Path) -> None:
        blocker = tmp_path / "file"
        blocker.write_text("", encoding="utf-8")

        with pytest.raises(StorageError, match="Cannot create"):
            AnalysisStore(blocker / "analyses.db").initialize()

    def test_database_replaced_by_a_directory(
        self, store: AnalysisStore, db_path: Path
    ) -> None:
        db_path.unlink()
        db_path.mkdir()

        with pytest.raises(StorageError):
            store.check()
        with pytest.raises(StorageError):
            store.save(make_analysis())

    def test_check_passes_on_a_working_database(self, store: AnalysisStore) -> None:
        store.check()


class TestPermissions:
    def test_database_is_private(self, store: AnalysisStore, db_path: Path) -> None:
        assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(db_path.parent.stat().st_mode) == 0o700


class TestConcurrency:
    def test_parallel_writers(self, store: AnalysisStore) -> None:
        errors: list[Exception] = []

        def write_some() -> None:
            try:
                for _ in range(10):
                    store.save(make_analysis())
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=write_some) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        assert store.list_analyses()[1] == 80

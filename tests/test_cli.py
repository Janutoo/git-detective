import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from git import Repo
from typer.testing import CliRunner

from git_detective import __version__
from git_detective.cli import app

CommitFn = Callable[..., str]
runner = CliRunner()


def test_scan_shows_commit_count_and_files(repo: Repo, make_commit: CommitFn) -> None:
    make_commit({"hot.py": "1\n", "cold.py": "1\n"})
    make_commit({"hot.py": "2\n"})

    result = runner.invoke(app, ["scan", repo.working_tree_dir])

    assert result.exit_code == 0, result.output
    assert "Analyzed 2 commits" in result.output
    assert "hot.py" in result.output
    assert "cold.py" in result.output


def test_scan_top_limits_rows(repo: Repo, make_commit: CommitFn) -> None:
    make_commit({"hot.py": "1\n", "cold.py": "1\n"})
    make_commit({"hot.py": "2\n"})

    result = runner.invoke(app, ["scan", repo.working_tree_dir, "--top", "1"])

    assert result.exit_code == 0, result.output
    assert "hot.py" in result.output
    assert "cold.py" not in result.output


def test_scan_limit_option(repo: Repo, make_commit: CommitFn) -> None:
    for i in range(3):
        make_commit({"a.txt": f"{i}\n"})

    result = runner.invoke(app, ["scan", repo.working_tree_dir, "--limit", "2"])

    assert result.exit_code == 0, result.output
    assert "Analyzed 2 commits" in result.output


def test_scan_empty_repository(repo: Repo) -> None:
    result = runner.invoke(app, ["scan", repo.working_tree_dir])

    assert result.exit_code == 0, result.output
    assert "No commits found" in result.output


def test_scan_invalid_path_exits_with_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["scan", str(tmp_path / "missing")])

    assert result.exit_code == 1
    assert "does not exist" in result.output


def test_scan_rejects_zero_limit(repo: Repo) -> None:
    result = runner.invoke(app, ["scan", repo.working_tree_dir, "--limit", "0"])

    assert result.exit_code == 2


NOW = datetime(2024, 7, 1, 12, 0, tzinfo=UTC)
DAY = 24 * 60 * 60


def ts(days_ago: int) -> int:
    """Unix timestamp ``days_ago`` days before the frozen ``NOW``."""
    return int(NOW.timestamp()) - days_ago * DAY


@pytest.fixture
def frozen_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    monkeypatch.setattr("git_detective.cli._utc_now", lambda: NOW)
    return NOW


def table_row(output: str, path: str) -> list[str]:
    """Return the cells of the table row that mentions ``path``."""
    line = next(line for line in output.splitlines() if f" {path} " in line)
    return [cell.strip() for cell in re.split(r"[│┃|]", line) if cell.strip()]


@pytest.fixture
def hotspot_repo(repo: Repo, make_commit: CommitFn) -> Repo:
    make_commit({"hot.py": "1\n", "cold.py": "1\n"}, timestamp=ts(45))
    make_commit({"hot.py": "2\n3\n"}, timestamp=ts(10))
    make_commit({"hot.py": "4\n"}, timestamp=ts(1))
    return repo


@pytest.mark.usefixtures("frozen_now")
class TestHotspotsCommand:
    def test_shows_ranked_table(self, hotspot_repo: Repo) -> None:
        result = runner.invoke(app, ["hotspots", hotspot_repo.working_tree_dir])

        assert result.exit_code == 0, result.output
        assert "Analyzed 3 commits" in result.output
        assert "Recent window: last 30 days (since 2024-06-01)" in result.output
        for header in ("File", "Commits", "Churn", "Recent", "Hotspot Score"):
            assert header in result.output
        assert result.output.index("hot.py") < result.output.index("cold.py")
        # rank, path, commits, churn, recent, score
        assert table_row(result.output, "hot.py") == [
            "1",
            "hot.py",
            "3",
            "7",
            "2",
            "100.0",
        ]
        assert table_row(result.output, "cold.py")[1:5] == ["cold.py", "1", "1", "0"]

    def test_top_limits_rows(self, hotspot_repo: Repo) -> None:
        result = runner.invoke(
            app, ["hotspots", hotspot_repo.working_tree_dir, "--top", "1"]
        )

        assert result.exit_code == 0, result.output
        assert "hot.py" in result.output
        assert "cold.py" not in result.output

    def test_recent_days_widens_window(self, hotspot_repo: Repo) -> None:
        result = runner.invoke(
            app, ["hotspots", hotspot_repo.working_tree_dir, "--recent-days", "60"]
        )

        assert result.exit_code == 0, result.output
        assert table_row(result.output, "hot.py")[4] == "3"
        assert table_row(result.output, "cold.py")[4] == "1"

    def test_limit_restricts_commits(self, hotspot_repo: Repo) -> None:
        result = runner.invoke(
            app, ["hotspots", hotspot_repo.working_tree_dir, "--limit", "2"]
        )

        assert result.exit_code == 0, result.output
        assert "Analyzed 2 commits" in result.output
        assert "cold.py" not in result.output

    def test_empty_repository(self, repo: Repo) -> None:
        result = runner.invoke(app, ["hotspots", repo.working_tree_dir])

        assert result.exit_code == 0, result.output
        assert "No commits found" in result.output

    def test_invalid_path(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["hotspots", str(tmp_path / "missing")])

        assert result.exit_code == 1
        assert "does not exist" in result.output

    def test_rejects_zero_recent_days(self, hotspot_repo: Repo) -> None:
        result = runner.invoke(
            app, ["hotspots", hotspot_repo.working_tree_dir, "--recent-days", "0"]
        )

        assert result.exit_code == 2


def test_version_option() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output

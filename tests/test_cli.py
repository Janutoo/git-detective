from collections.abc import Callable
from pathlib import Path

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


def test_version_option() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output

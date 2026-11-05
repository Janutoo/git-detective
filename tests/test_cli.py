import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from git import Actor, Repo
from typer.testing import CliRunner

from git_detective import __version__, cli
from git_detective.cli import app

CommitFn = Callable[..., str]
runner = CliRunner()

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    """Strip ANSI styles.

    Rich colors Typer's usage errors when it detects CI (e.g. GitHub Actions),
    which can split words such as ``--output`` into separately styled parts.
    """
    return ANSI_ESCAPE.sub("", text)


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


# --- Filtering ---------------------------------------------------------------

BOX_CHARS = set("│┃┏┓┗┛━─┡┩╇╋")


@pytest.fixture
def noisy_repo(repo: Repo, make_commit: CommitFn) -> Repo:
    """Repository with source files plus typical noise: lockfile and build."""
    make_commit(
        {
            "src/app.py": "a\n",
            "poetry.lock": "x\n" * 500,
            "dist/bundle.js": "y\n" * 300,
            "web/dist/keep.js": "z\n",
        },
        timestamp=ts(5),
    )
    make_commit({"src/app.py": "b\n", "poetry.lock": "w\n" * 400}, timestamp=ts(2))
    return repo


def scan_paths(repo: Repo, *args: str) -> list[str]:
    """Run ``scan --format json`` and return the reported paths."""
    result = runner.invoke(
        app, ["scan", repo.working_tree_dir, "--format", "json", *args]
    )
    assert result.exit_code == 0, result.output
    return [f["path"] for f in json.loads(result.stdout)["files"]]


def write_ignore_file(repo: Repo, content: str) -> None:
    (Path(repo.working_tree_dir) / ".gitdetectiveignore").write_text(
        content, encoding="utf-8"
    )


class TestExclude:
    def test_scan_without_exclude_reports_everything(self, noisy_repo: Repo) -> None:
        assert set(scan_paths(noisy_repo)) == {
            "src/app.py",
            "poetry.lock",
            "dist/bundle.js",
            "web/dist/keep.js",
        }

    def test_scan_exclude_by_extension(self, noisy_repo: Repo) -> None:
        assert "poetry.lock" not in scan_paths(noisy_repo, "--exclude", "*.lock")

    def test_scan_exclude_anchored_directory(self, noisy_repo: Repo) -> None:
        paths = scan_paths(noisy_repo, "-e", "dist/**")

        assert "dist/bundle.js" not in paths
        assert "web/dist/keep.js" in paths

    def test_multiple_excludes(self, noisy_repo: Repo) -> None:
        paths = scan_paths(noisy_repo, "-e", "*.lock", "-e", "dist/")

        assert paths == ["src/app.py"]

    def test_scan_table_shows_active_patterns(self, noisy_repo: Repo) -> None:
        result = runner.invoke(
            app, ["scan", noisy_repo.working_tree_dir, "-e", "*.lock"]
        )

        assert result.exit_code == 0, result.output
        assert "Excluding: *.lock" in result.output
        assert "poetry.lock" not in result.output.split("Excluding")[1]

    @pytest.mark.usefixtures("frozen_now")
    def test_hotspots_exclude(self, noisy_repo: Repo) -> None:
        result = runner.invoke(
            app,
            ["hotspots", noisy_repo.working_tree_dir, "-e", "*.lock", "-e", "dist/"],
        )

        assert result.exit_code == 0, result.output
        assert table_row(result.output, "src/app.py")[-1] == "100.0"
        assert "poetry.lock" not in result.output
        assert "bundle.js" not in result.output

    def test_invalid_pattern_exits_with_error(self, noisy_repo: Repo) -> None:
        result = runner.invoke(app, ["scan", noisy_repo.working_tree_dir, "-e", "x\\"])

        assert result.exit_code == 1
        assert "Invalid exclude pattern" in result.stderr


class TestIgnoreFile:
    def test_ignore_file_is_applied(self, noisy_repo: Repo) -> None:
        write_ignore_file(noisy_repo, "# noise\n*.lock\n\n/dist/\n")

        assert scan_paths(noisy_repo) == ["src/app.py", "web/dist/keep.js"]

    def test_ignore_file_is_combined_with_exclude(self, noisy_repo: Repo) -> None:
        write_ignore_file(noisy_repo, "*.lock\n")

        assert scan_paths(noisy_repo, "-e", "dist/") == ["src/app.py"]

    def test_exclude_can_negate_ignore_file(self, noisy_repo: Repo) -> None:
        write_ignore_file(noisy_repo, "*.lock\n")

        assert "poetry.lock" in scan_paths(noisy_repo, "-e", "!poetry.lock")

    def test_ignore_file_is_read_from_repo_root(self, noisy_repo: Repo) -> None:
        write_ignore_file(noisy_repo, "*.lock\n")
        subdir = Path(noisy_repo.working_tree_dir) / "src"

        result = runner.invoke(app, ["scan", str(subdir), "-f", "json"])

        paths = [f["path"] for f in json.loads(result.stdout)["files"]]
        assert "poetry.lock" not in paths

    def test_patterns_are_recorded_in_report(self, noisy_repo: Repo) -> None:
        write_ignore_file(noisy_repo, "# comment\n*.lock\n")

        result = runner.invoke(
            app, ["scan", noisy_repo.working_tree_dir, "-f", "json", "-e", "dist/"]
        )

        parameters = json.loads(result.stdout)["parameters"]
        assert parameters["exclude"] == ["*.lock", "dist/"]
        assert parameters["ignore_file"] == ".gitdetectiveignore"

    def test_missing_ignore_file_is_fine(self, noisy_repo: Repo) -> None:
        result = runner.invoke(app, ["scan", noisy_repo.working_tree_dir, "-f", "json"])

        assert result.exit_code == 0, result.output
        parameters = json.loads(result.stdout)["parameters"]
        assert parameters["ignore_file"] is None
        assert parameters["exclude"] == []


# --- Report export -----------------------------------------------------------


@pytest.mark.usefixtures("frozen_now")
class TestJsonFormat:
    def test_hotspots_json_on_stdout(self, hotspot_repo: Repo) -> None:
        result = runner.invoke(
            app, ["hotspots", hotspot_repo.working_tree_dir, "--format", "json"]
        )

        assert result.exit_code == 0, result.output
        assert not BOX_CHARS & set(result.stdout)
        assert "\x1b[" not in result.stdout  # no ANSI colors
        data = json.loads(result.stdout)
        assert data["report"] == "hotspots"
        assert data["generated_at"] == "2024-07-01T12:00:00+00:00"
        assert data["parameters"]["recent_days"] == 30
        assert data["parameters"]["reference_time"] == "2024-07-01T12:00:00+00:00"
        assert data["parameters"]["weights"] == {
            "frequency": 0.45,
            "churn": 0.35,
            "recency": 0.2,
        }
        assert (
            data["repository"]["path"] == Path(hotspot_repo.working_tree_dir).as_posix()
        )
        assert [f["path"] for f in data["files"]] == ["hot.py", "cold.py"]
        assert data["files"][0]["components"]["frequency"] == 1.0

    def test_scan_json_respects_top_and_limit(self, hotspot_repo: Repo) -> None:
        result = runner.invoke(
            app,
            ["scan", hotspot_repo.working_tree_dir, "-f", "json", "--top", "1"],
        )

        data = json.loads(result.stdout)
        assert data["report"] == "scan"
        assert data["parameters"]["top"] == 1
        assert data["summary"]["commits_analyzed"] == 3
        assert len(data["files"]) == 1

    def test_format_is_case_insensitive(self, hotspot_repo: Repo) -> None:
        result = runner.invoke(
            app, ["scan", hotspot_repo.working_tree_dir, "-f", "JSON"]
        )

        assert result.exit_code == 0, result.output
        json.loads(result.stdout)

    def test_empty_repository_gives_valid_json(self, repo: Repo) -> None:
        result = runner.invoke(app, ["hotspots", repo.working_tree_dir, "-f", "json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["files"] == []
        assert data["summary"]["period"] is None


@pytest.mark.usefixtures("frozen_now")
class TestMarkdownFormat:
    def test_hotspots_markdown_on_stdout(self, hotspot_repo: Repo) -> None:
        result = runner.invoke(
            app, ["hotspots", hotspot_repo.working_tree_dir, "-f", "markdown"]
        )

        assert result.exit_code == 0, result.output
        assert result.stdout.startswith("# Git Detective — Change hotspots\n")
        assert "| 1 | hot.py | 3 | 7 | 2 | 100.0 |" in result.stdout

    def test_scan_markdown_on_stdout(self, hotspot_repo: Repo) -> None:
        result = runner.invoke(
            app, ["scan", hotspot_repo.working_tree_dir, "-f", "markdown"]
        )

        assert result.exit_code == 0, result.output
        assert "| # | File | Commits | Churn | Added | Deleted |" in result.stdout


@pytest.mark.usefixtures("frozen_now")
class TestOutputFile:
    @pytest.mark.parametrize("fmt", ["json", "markdown"])
    def test_writes_report_to_file(
        self, hotspot_repo: Repo, tmp_path: Path, fmt: str
    ) -> None:
        target = tmp_path / f"report.{fmt}"

        result = runner.invoke(
            app,
            ["hotspots", hotspot_repo.working_tree_dir, "-f", fmt, "-o", str(target)],
        )

        assert result.exit_code == 0, result.output
        assert result.stdout == ""
        assert "Report written to" in result.stderr
        content = target.read_text(encoding="utf-8")
        assert "hot.py" in content
        if fmt == "json":
            assert json.loads(content)["report"] == "hotspots"

    def test_overwrites_existing_file(self, hotspot_repo: Repo, tmp_path: Path) -> None:
        target = tmp_path / "report.json"
        target.write_text("old", encoding="utf-8")

        runner.invoke(
            app,
            ["scan", hotspot_repo.working_tree_dir, "-f", "json", "-o", str(target)],
        )

        assert json.loads(target.read_text(encoding="utf-8"))["report"] == "scan"

    def test_missing_output_directory(self, hotspot_repo: Repo, tmp_path: Path) -> None:
        target = tmp_path / "missing" / "report.json"

        result = runner.invoke(
            app,
            ["scan", hotspot_repo.working_tree_dir, "-f", "json", "-o", str(target)],
        )

        assert result.exit_code == 1
        assert "Cannot write report" in result.stderr
        assert result.stdout == ""

    def test_output_is_a_directory(self, hotspot_repo: Repo, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["scan", hotspot_repo.working_tree_dir, "-f", "json", "-o", str(tmp_path)],
        )

        assert result.exit_code == 1
        assert "Cannot write report" in result.stderr

    def test_output_requires_export_format(
        self, hotspot_repo: Repo, tmp_path: Path
    ) -> None:
        target = tmp_path / "report.txt"

        result = runner.invoke(
            app, ["scan", hotspot_repo.working_tree_dir, "-o", str(target)]
        )

        assert result.exit_code == 2
        assert "--output" in plain(result.output)
        assert not target.exists()


@pytest.mark.parametrize("command", ["scan", "hotspots"])
def test_unknown_format_is_rejected(hotspot_repo: Repo, command: str) -> None:
    result = runner.invoke(
        app, [command, hotspot_repo.working_tree_dir, "--format", "xml"]
    )

    assert result.exit_code == 2
    assert "xml" in plain(result.output)


# --- authors -----------------------------------------------------------------

ALICE = Actor("Alice Smith", "alice@work.com")
ALICE_HOME = Actor("alice", "alice@home.org")
BOB = Actor("Bob", "bob@example.com")


@pytest.fixture
def team_repo(repo: Repo, make_commit: CommitFn) -> Repo:
    """Alice: 3 commits (one under an alias), Bob: 1 commit with large churn."""
    make_commit({"src/app.py": "1\n"}, author=ALICE, timestamp=ts(40))
    make_commit({"src/app.py": "2\n"}, author=ALICE, timestamp=ts(20))
    make_commit({"docs/notes.md": "x\n"}, author=ALICE_HOME, timestamp=ts(3))
    make_commit(
        {"vendor/big.js": "v\n" * 200, "poetry.lock": "l\n" * 50},
        author=BOB,
        timestamp=ts(2),
    )
    return repo


def authors_json(repo: Repo, *args: str) -> dict:
    result = runner.invoke(app, ["authors", repo.working_tree_dir, "-f", "json", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def by_author(data: dict) -> dict[str, dict]:
    return {a["name"]: a for a in data["authors"]}


@pytest.fixture
def wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render wide tables (e.g. authors) without wrapping cells or headers."""
    monkeypatch.setattr(cli.console, "width", 250)


@pytest.mark.usefixtures("wide_console")
class TestAuthorsCommand:
    def test_table(self, team_repo: Repo) -> None:
        result = runner.invoke(app, ["authors", team_repo.working_tree_dir])

        assert result.exit_code == 0, result.output
        assert "Analyzed 4 commits" in result.output
        assert "(3 authors)" in result.output
        for header in ("Author", "Commits", "Churn", "Files", "Active days"):
            assert header in result.output
        assert table_row(result.output, "Alice Smith")[2] == "2"
        assert "@" not in result.output  # e-mails are hidden by default

    def test_json(self, team_repo: Repo) -> None:
        data = authors_json(team_repo)

        assert data["report"] == "authors"
        assert data["parameters"]["limit"] == 500
        assert data["parameters"]["show_emails"] is False
        assert data["parameters"]["mailmap"] is None
        assert [a["name"] for a in data["authors"]] == ["Alice Smith", "Bob", "alice"]
        assert "@" not in json.dumps(data["authors"])

    def test_sort_by_churn(self, team_repo: Repo) -> None:
        data = authors_json(team_repo, "--sort", "churn")

        assert data["authors"][0]["name"] == "Bob"
        assert data["parameters"]["sort"] == "churn"

    def test_top(self, team_repo: Repo) -> None:
        data = authors_json(team_repo, "--top", "1")

        assert len(data["authors"]) == 1
        assert data["summary"]["authors_total"] == 3

    def test_show_emails(self, team_repo: Repo) -> None:
        data = authors_json(team_repo, "--show-emails")
        result = runner.invoke(
            app, ["authors", team_repo.working_tree_dir, "--show-emails"]
        )

        assert data["authors"][0]["emails"] == ["alice@work.com"]
        assert "alice@work.com" in result.output

    def test_mailmap_merges_aliases(self, team_repo: Repo) -> None:
        (Path(team_repo.working_tree_dir) / ".mailmap").write_text(
            "Alice Smith <alice@work.com> <alice@home.org>\n", encoding="utf-8"
        )

        data = authors_json(team_repo, "--show-emails")
        table = runner.invoke(app, ["authors", team_repo.working_tree_dir])

        assert [a["name"] for a in data["authors"]] == ["Alice Smith", "Bob"]
        assert data["authors"][0]["commits"] == 3
        assert data["authors"][0]["emails"] == ["alice@work.com"]
        assert data["parameters"]["mailmap"] == ".mailmap"
        assert "Aliases resolved with .mailmap" in table.output

    def test_exclude_changes_churn_not_commits(self, team_repo: Repo) -> None:
        before = by_author(authors_json(team_repo))["Bob"]
        after = by_author(authors_json(team_repo, "-e", "vendor/", "-e", "*.lock"))[
            "Bob"
        ]

        assert before["commits"] == after["commits"] == 1
        assert (before["churn"], after["churn"]) == (250, 0)
        assert after["files_touched"] == 0

    def test_markdown(self, team_repo: Repo) -> None:
        result = runner.invoke(
            app, ["authors", team_repo.working_tree_dir, "-f", "markdown"]
        )

        assert result.exit_code == 0, result.output
        assert result.stdout.startswith("# Git Detective — Contributors\n")
        assert "| 1 | Alice Smith | 2 |" in result.stdout

    def test_empty_repository(self, repo: Repo) -> None:
        table = runner.invoke(app, ["authors", repo.working_tree_dir])
        data = authors_json(repo)

        assert "No commits found" in table.output
        assert data["authors"] == []

    def test_invalid_path(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["authors", str(tmp_path / "missing")])

        assert result.exit_code == 1
        assert "does not exist" in result.stderr

    def test_unknown_sort(self, team_repo: Repo) -> None:
        result = runner.invoke(
            app, ["authors", team_repo.working_tree_dir, "--sort", "lines"]
        )

        assert result.exit_code == 2


# --- coupling ----------------------------------------------------------------


@pytest.fixture
def coupled_repo(repo: Repo, make_commit: CommitFn) -> Repo:
    """api.py + client.py change together 3x; one bulk commit touches 6 files."""
    for i in range(3):
        make_commit({"src/api.py": f"{i}\n", "src/client.py": f"{i}\n"})
    make_commit({"src/api.py": "x\n", "src/api.lock": "x\n"})
    make_commit({"src/api.py": "y\n", "src/api.lock": "y\n"})
    make_commit({"src/api.py": "z\n", "src/api.lock": "z\n"})
    make_commit({f"bulk/{i}.txt": "b\n" for i in range(6)})
    return repo


def coupling_json(repo: Repo, *args: str) -> dict:
    result = runner.invoke(
        app, ["coupling", repo.working_tree_dir, "-f", "json", *args]
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def pair_names(data: dict) -> list[tuple[str, str]]:
    return [(p["file_a"], p["file_b"]) for p in data["pairs"]]


@pytest.mark.usefixtures("wide_console")
class TestCouplingCommand:
    def test_table(self, coupled_repo: Repo) -> None:
        result = runner.invoke(app, ["coupling", coupled_repo.working_tree_dir])

        assert result.exit_code == 0, result.output
        assert "Analyzed 7 commits" in result.output
        assert "skipped 0 with more than 50 files" in result.output
        assert table_row(result.output, "src/client.py") == [
            "2",
            "src/api.py",
            "src/client.py",
            "3",
            "0.50",
        ]
        # The caption wraps to the table width.
        assert "not proof of a dependency" in " ".join(result.output.split())

    def test_json_defaults(self, coupled_repo: Repo) -> None:
        data = coupling_json(coupled_repo)

        assert data["report"] == "coupling"
        assert data["parameters"] == {
            "limit": 500,
            "top": 20,
            "min_cochanges": 3,
            "max_files_per_commit": 50,
            "exclude": [],
            "ignore_file": None,
        }
        assert pair_names(data)[:2] == [
            ("src/api.lock", "src/api.py"),
            ("src/api.py", "src/client.py"),
        ]
        assert {"nodes", "edges"} == set(data["graph"])

    def test_max_files_per_commit_skips_bulk_commit(self, coupled_repo: Repo) -> None:
        # --min-cochanges 1 so the single bulk commit could create pairs at all.
        default = coupling_json(coupled_repo, "--min-cochanges", "1")
        strict = coupling_json(
            coupled_repo, "--min-cochanges", "1", "--max-files-per-commit", "5"
        )

        def bulk_pairs(data: dict) -> int:
            return sum(a.startswith("bulk/") for a, _ in pair_names(data))

        assert default["summary"]["commits_skipped"] == 0
        assert bulk_pairs(default) == 15  # 6 files -> 15 artificial pairs
        assert strict["summary"]["commits_skipped"] == 1
        assert strict["summary"]["commits_considered"] == 6
        assert bulk_pairs(strict) == 0

    def test_min_cochanges(self, coupled_repo: Repo) -> None:
        loose = coupling_json(coupled_repo, "--min-cochanges", "1")
        strict = coupling_json(coupled_repo, "--min-cochanges", "4")

        assert ("bulk/0.txt", "bulk/1.txt") in pair_names(loose)
        assert strict["pairs"] == []

    def test_exclude(self, coupled_repo: Repo) -> None:
        data = coupling_json(coupled_repo, "-e", "*.lock")

        assert pair_names(data) == [("src/api.py", "src/client.py")]
        assert data["parameters"]["exclude"] == ["*.lock"]

    def test_ignore_file(self, coupled_repo: Repo) -> None:
        write_ignore_file(coupled_repo, "*.lock\n")

        data = coupling_json(coupled_repo)

        assert pair_names(data) == [("src/api.py", "src/client.py")]
        assert data["parameters"]["ignore_file"] == ".gitdetectiveignore"

    def test_top(self, coupled_repo: Repo) -> None:
        data = coupling_json(coupled_repo, "--top", "1")

        assert len(data["pairs"]) == 1
        assert data["summary"]["pairs_total"] == 2

    def test_markdown_to_file(self, coupled_repo: Repo, tmp_path: Path) -> None:
        target = tmp_path / "coupling.md"

        result = runner.invoke(
            app,
            [
                "coupling",
                coupled_repo.working_tree_dir,
                "-f",
                "markdown",
                "-o",
                str(target),
            ],
        )

        assert result.exit_code == 0, result.output
        assert result.stdout == ""
        content = target.read_text(encoding="utf-8")
        assert content.startswith("# Git Detective — File coupling\n")
        assert "| src/api.py | src/client.py | 3 |" in content

    def test_empty_repository(self, repo: Repo) -> None:
        table = runner.invoke(app, ["coupling", repo.working_tree_dir])
        data = coupling_json(repo)

        assert "No commits found" in table.output
        assert data["pairs"] == []
        assert data["graph"] == {"nodes": [], "edges": []}

    @pytest.mark.parametrize(
        "args", [["--min-cochanges", "0"], ["--max-files-per-commit", "1"]]
    )
    def test_invalid_options(self, coupled_repo: Repo, args: list[str]) -> None:
        result = runner.invoke(app, ["coupling", coupled_repo.working_tree_dir, *args])

        assert result.exit_code == 2

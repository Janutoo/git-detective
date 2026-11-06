"""The analysis service, on real temporary repositories."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import pytest
from git import Actor, Repo
from typer.testing import CliRunner

from git_detective.cli import app
from git_detective.core import (
    AuthorSort,
    GitReader,
    Mailmap,
    compute_author_stats,
    compute_coupling,
    compute_hotspots,
)
from git_detective.exceptions import (
    IgnorePatternError,
    InvalidRepositoryError,
    MailmapError,
)
from git_detective.reporting import to_json, to_markdown
from git_detective.service import (
    AuthorsConfig,
    CouplingConfig,
    HotspotsConfig,
    ScanConfig,
    run_analysis,
    run_authors,
    run_coupling,
    run_hotspots,
    run_scan,
)

NOW = datetime(2024, 1, 2, 12, 0, tzinfo=UTC)


@pytest.mark.usefixtures("seeded_repo")
class TestAnalyses:
    def test_scan(self, repo_path: Path) -> None:
        result = run_scan(repo_path, ScanConfig(top=1), now=NOW)

        assert {s.path for s in result.data} == {"src/a.py", "src/b.py", "c.lock"}
        assert result.report["report"] == "scan"
        assert len(result.report["files"]) == 1
        assert result.commits_analyzed == 3

    def test_hotspots_match_the_algorithm(self, repo_path: Path) -> None:
        result = run_hotspots(repo_path, HotspotsConfig(recent_days=7), now=NOW)

        with GitReader(repo_path) as reader:
            commits = reader.read_commits()
        assert result.data == compute_hotspots(
            commits, reference_time=NOW, recent_days=7
        )
        assert result.report["parameters"]["recent_days"] == 7

    def test_authors_match_the_algorithm(self, repo_path: Path) -> None:
        config = AuthorsConfig(sort=AuthorSort.CHURN)
        result = run_authors(repo_path, config, now=NOW)

        with GitReader(repo_path) as reader:
            commits = reader.read_commits()
        assert result.data == compute_author_stats(
            commits, mailmap=Mailmap(), sort_by=AuthorSort.CHURN
        )
        assert not result.mailmap_used

    def test_coupling_match_the_algorithm(self, repo_path: Path) -> None:
        config = CouplingConfig(min_cochanges=2, max_files_per_commit=10)
        result = run_coupling(repo_path, config, now=NOW)

        with GitReader(repo_path) as reader:
            commits = reader.read_commits()
        assert result.data == compute_coupling(
            commits, min_cochanges=2, max_files_per_commit=10
        )
        assert result.data.pairs[0].file_a == "src/a.py"

    def test_limit_is_applied(self, repo_path: Path) -> None:
        result = run_scan(repo_path, ScanConfig(limit=1), now=NOW)

        assert result.commits_analyzed == 1

    def test_head_sha_is_reported(self, repo_path: Path, seeded_repo: str) -> None:
        result = run_scan(repo_path, ScanConfig(), now=NOW)

        assert result.head_sha == seeded_repo


class TestDispatch:
    @pytest.mark.usefixtures("seeded_repo")
    @pytest.mark.parametrize(
        ("config", "kind"),
        [
            (ScanConfig(), "scan"),
            (HotspotsConfig(), "hotspots"),
            (AuthorsConfig(), "authors"),
            (CouplingConfig(), "coupling"),
        ],
    )
    def test_config_type_selects_the_analysis(
        self, repo_path: Path, config: object, kind: str
    ) -> None:
        assert run_analysis(repo_path, config, now=NOW).report["report"] == kind

    def test_unknown_config_is_rejected(self, repo_path: Path) -> None:
        with pytest.raises(TypeError, match="Unknown analysis config"):
            run_analysis(repo_path, object(), now=NOW)


class TestFilters:
    @pytest.mark.usefixtures("seeded_repo")
    def test_exclude_patterns(self, repo_path: Path) -> None:
        config = ScanConfig(exclude=("*.lock",))
        result = run_scan(repo_path, config, now=NOW)

        assert {s.path for s in result.data} == {"src/a.py", "src/b.py"}
        assert result.exclude_patterns == ("*.lock",)
        assert result.report["parameters"]["exclude"] == ["*.lock"]
        assert result.report["parameters"]["ignore_file"] is None

    @pytest.mark.usefixtures("seeded_repo")
    def test_ignore_file_comes_before_request_patterns(self, repo_path: Path) -> None:
        (repo_path / ".gitdetectiveignore").write_text("src/**\n", encoding="utf-8")
        config = ScanConfig(exclude=("!src/a.py",))
        result = run_scan(repo_path, config, now=NOW)

        assert {s.path for s in result.data} == {"src/a.py", "c.lock"}
        assert result.report["parameters"]["ignore_file"] == ".gitdetectiveignore"

    @pytest.mark.usefixtures("seeded_repo")
    def test_mailmap_is_applied(self, repo_path: Path) -> None:
        (repo_path / ".mailmap").write_text(
            "Robert Builder <bob@secret.example.com>\n", encoding="utf-8"
        )
        result = run_authors(repo_path, AuthorsConfig(), now=NOW)

        assert result.mailmap_used
        assert {a.name for a in result.data} == {"Alice Example", "Robert Builder"}
        assert result.report["parameters"]["mailmap"] == ".mailmap"

    @pytest.mark.usefixtures("seeded_repo")
    def test_symlinked_ignore_file_fails(self, repo_path: Path, tmp_path: Path) -> None:
        secret = tmp_path / "secret"
        secret.write_text("secret-value\n", encoding="utf-8")
        (repo_path / ".gitdetectiveignore").symlink_to(secret)

        with pytest.raises(IgnorePatternError):
            run_scan(repo_path, ScanConfig(), now=NOW)

    @pytest.mark.usefixtures("seeded_repo")
    def test_symlinked_mailmap_fails(self, repo_path: Path, tmp_path: Path) -> None:
        secret = tmp_path / "secret"
        secret.write_text("x <y@z>\n", encoding="utf-8")
        (repo_path / ".mailmap").symlink_to(secret)

        with pytest.raises(MailmapError):
            run_authors(repo_path, AuthorsConfig(), now=NOW)


class TestEmptyRepository:
    @pytest.mark.parametrize(
        "config", [ScanConfig(), HotspotsConfig(), AuthorsConfig(), CouplingConfig()]
    )
    def test_has_no_head_and_no_results(self, repo_path: Path, config: object) -> None:
        result = run_analysis(repo_path, config, now=NOW)

        assert result.head_sha is None
        assert result.commits_analyzed == 0
        assert result.report["summary"]["commits_analyzed"] == 0


class TestHeadHash:
    def test_merge_commit_at_head(
        self, repo: Repo, repo_path: Path, make_commit: Callable[..., str]
    ) -> None:
        first = make_commit({"a.txt": "1\n"})
        second = make_commit({"b.txt": "1\n"})
        author = Actor("Alice Example", "alice@example.com")
        merge = repo.index.commit(
            "Merge",
            parent_commits=[repo.commit(first), repo.commit(second)],
            author=author,
            committer=author,
        ).hexsha

        result = run_scan(repo_path, ScanConfig(), now=NOW)

        assert result.head_sha == merge
        assert result.commits_analyzed == 2  # merge commits are skipped


class TestLabel:
    @pytest.mark.usefixtures("seeded_repo")
    def test_default_shows_the_repository_path(self, repo_path: Path) -> None:
        result = run_scan(repo_path, ScanConfig(), now=NOW)

        assert result.report["repository"]["path"] == repo_path.as_posix()

    @pytest.mark.usefixtures("seeded_repo")
    def test_label_replaces_the_path(self, repo_path: Path) -> None:
        result = run_scan(repo_path, ScanConfig(), now=NOW, label=PurePosixPath("demo"))

        assert result.report["repository"]["path"] == "demo"
        assert str(repo_path) not in json.dumps(result.report)
        assert result.root == repo_path  # still available to the caller


class TestStrict:
    @pytest.mark.usefixtures("seeded_repo")
    def test_subdirectory_is_allowed_by_default(self, repo_path: Path) -> None:
        result = run_scan(repo_path / "src", ScanConfig(), now=NOW)

        assert result.commits_analyzed == 3

    @pytest.mark.usefixtures("seeded_repo")
    def test_strict_refuses_a_subdirectory(self, repo_path: Path) -> None:
        with pytest.raises(InvalidRepositoryError, match="Not a repository root"):
            run_scan(repo_path / "src", ScanConfig(), now=NOW, strict=True)

    @pytest.mark.usefixtures("seeded_repo")
    def test_strict_accepts_the_root(self, repo_path: Path) -> None:
        result = run_scan(repo_path, ScanConfig(), now=NOW, strict=True)

        assert result.commits_analyzed == 3


@pytest.mark.usefixtures("seeded_repo")
class TestCliCompatibility:
    """The CLI must print exactly what the service produces."""

    @pytest.fixture(autouse=True)
    def frozen_now(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("git_detective.cli._utc_now", lambda: NOW)

    @pytest.mark.parametrize(
        ("args", "run", "config"),
        [
            (["scan"], run_scan, ScanConfig()),
            (["hotspots"], run_hotspots, HotspotsConfig()),
            (["authors"], run_authors, AuthorsConfig()),
            (["coupling"], run_coupling, CouplingConfig()),
            (
                ["authors", "--show-emails"],
                run_authors,
                AuthorsConfig(show_emails=True),
            ),
            (
                ["coupling", "--min-cochanges", "2", "-e", "*.lock", "-t", "1"],
                run_coupling,
                CouplingConfig(min_cochanges=2, exclude=("*.lock",), top=1),
            ),
        ],
    )
    @pytest.mark.parametrize(
        ("output_format", "render"), [("json", to_json), ("markdown", to_markdown)]
    )
    def test_output_equals_service_report(
        self, repo_path: Path, args, run, config, output_format, render
    ) -> None:
        cli_result = CliRunner().invoke(
            app, [args[0], str(repo_path), *args[1:], "-f", output_format]
        )

        assert cli_result.exit_code == 0, cli_result.output
        assert cli_result.stdout == render(run(repo_path, config, now=NOW).report)

    def test_report_parameters_keep_their_order(self, repo_path: Path) -> None:
        cli_result = CliRunner().invoke(app, ["hotspots", str(repo_path), "-f", "json"])

        assert list(json.loads(cli_result.stdout)["parameters"]) == [
            "limit",
            "top",
            "recent_days",
            "reference_time",
            "weights",
            "exclude",
            "ignore_file",
        ]

    def test_emails_stay_opt_in(self, repo_path: Path) -> None:
        hidden = CliRunner().invoke(app, ["authors", str(repo_path), "-f", "json"])
        shown = CliRunner().invoke(
            app, ["authors", str(repo_path), "-f", "json", "--show-emails"]
        )

        assert "example.com" not in hidden.stdout
        assert "bob@secret.example.com" in shown.stdout

    def test_cli_reports_symlinked_config_files_as_errors(
        self, repo_path: Path, tmp_path: Path
    ) -> None:
        secret = tmp_path / "secret"
        secret.write_text("secret-value\n", encoding="utf-8")
        (repo_path / ".gitdetectiveignore").symlink_to(secret)

        result = CliRunner().invoke(app, ["scan", str(repo_path), "-f", "json"])

        assert result.exit_code == 1
        assert "secret-value" not in result.output

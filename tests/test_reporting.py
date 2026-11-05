"""Tests for report building and rendering. No Git or CLI involved."""

import json
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from git_detective import __version__
from git_detective.core.analyzer import aggregate_file_stats
from git_detective.core.authors import compute_author_stats
from git_detective.core.hotspots import compute_hotspots
from git_detective.models import CommitInfo, FileChange
from git_detective.reporting import (
    SCHEMA_VERSION,
    ReportContext,
    build_authors_report,
    build_hotspots_report,
    build_scan_report,
    escape_markdown,
    to_json,
    to_markdown,
)

GENERATED = datetime(2024, 7, 1, 12, 0, tzinfo=UTC)
WARSAW = timezone(timedelta(hours=2))


def commit(when: datetime, *changes: tuple[str, int, int], sha: str) -> CommitInfo:
    return CommitInfo(
        hexsha=sha * 40,
        author_name="Alice",
        author_email="alice@example.com",
        authored_at=when,
        message="msg",
        files=tuple(
            FileChange(path, added, deleted) for path, added, deleted in changes
        ),
    )


@pytest.fixture
def commits() -> list[CommitInfo]:
    return [
        commit(
            datetime(2024, 6, 29, 9, 30, tzinfo=WARSAW),
            ("src/app.py", 10, 2),
            ("docs/zażółć.md", 3, 0),
            sha="b",
        ),
        commit(datetime(2024, 1, 15, 8, 0, tzinfo=UTC), ("src/app.py", 4, 4), sha="a"),
    ]


def context(commits: list[CommitInfo], **parameters: object) -> ReportContext:
    return ReportContext(
        repository=Path("/work/my repo"),
        generated_at=GENERATED,
        commits=commits,
        parameters={"limit": 100, "top": 10, "exclude": ["*.lock"], **parameters},
    )


def scan_report(commits: list[CommitInfo], top: int = 10) -> dict:
    stats = aggregate_file_stats(commits)
    return build_scan_report(stats, context(commits), top=top)


def hotspots_report(commits: list[CommitInfo], top: int = 10) -> dict:
    ranked = compute_hotspots(commits, reference_time=GENERATED)
    ctx = context(commits, recent_days=30, reference_time=GENERATED)
    return build_hotspots_report(ranked, ctx, top=top)


class TestJsonReport:
    def test_top_level_structure(self, commits: list[CommitInfo]) -> None:
        data = json.loads(to_json(scan_report(commits)))

        assert list(data) == [
            "schema_version",
            "report",
            "tool",
            "generated_at",
            "repository",
            "parameters",
            "summary",
            "files",
        ]
        assert data["schema_version"] == SCHEMA_VERSION
        assert data["report"] == "scan"
        assert data["tool"] == {"name": "git-detective", "version": __version__}
        assert data["repository"] == {"path": "/work/my repo"}
        assert data["parameters"] == {"limit": 100, "top": 10, "exclude": ["*.lock"]}

    def test_scan_files(self, commits: list[CommitInfo]) -> None:
        data = json.loads(to_json(scan_report(commits)))

        assert data["summary"]["commits_analyzed"] == 2
        assert data["summary"]["files_changed"] == 2
        assert data["files"] == [
            {
                "rank": 1,
                "path": "src/app.py",
                "commits": 2,
                "churn": 20,
                "added": 14,
                "deleted": 6,
            },
            {
                "rank": 2,
                "path": "docs/zażółć.md",
                "commits": 1,
                "churn": 3,
                "added": 3,
                "deleted": 0,
            },
        ]

    def test_hotspots_files_include_score_components(
        self, commits: list[CommitInfo]
    ) -> None:
        data = json.loads(to_json(hotspots_report(commits)))

        first = data["files"][0]
        assert data["report"] == "hotspots"
        assert first["path"] == "src/app.py"
        assert first["recent_commits"] == 1
        assert first["score"] == 100.0
        assert first["components"] == {"frequency": 1.0, "churn": 1.0, "recency": 1.0}
        assert set(data["files"][1]) == {
            "rank",
            "path",
            "commits",
            "churn",
            "recent_commits",
            "score",
            "components",
        }

    def test_numbers_are_rounded(self, commits: list[CommitInfo]) -> None:
        second = hotspots_report(commits)["files"][1]

        assert second["score"] == round(second["score"], 2)
        for value in second["components"].values():
            assert value == round(value, 4)

    def test_top_limits_files_but_not_summary(self, commits: list[CommitInfo]) -> None:
        report = scan_report(commits, top=1)

        assert [f["path"] for f in report["files"]] == ["src/app.py"]
        assert report["summary"]["files_changed"] == 2
        assert report["summary"]["files_reported"] == 1

    def test_unicode_is_kept_readable(self, commits: list[CommitInfo]) -> None:
        text = to_json(scan_report(commits))

        assert "zażółć" in text
        assert "\\u" not in text
        assert text.endswith("}\n")

    def test_empty_report(self) -> None:
        data = json.loads(to_json(scan_report([])))

        assert data["files"] == []
        assert data["summary"] == {
            "commits_analyzed": 0,
            "files_changed": 0,
            "files_reported": 0,
            "period": None,
        }

    def test_report_is_json_ready_without_custom_encoder(
        self, commits: list[CommitInfo]
    ) -> None:
        report = hotspots_report(commits)

        assert json.loads(json.dumps(report)) == report


class TestDateSerialization:
    def test_dates_are_iso_8601_with_offset(self, commits: list[CommitInfo]) -> None:
        data = json.loads(to_json(hotspots_report(commits)))

        assert data["generated_at"] == "2024-07-01T12:00:00+00:00"
        assert data["parameters"]["reference_time"] == "2024-07-01T12:00:00+00:00"
        assert data["summary"]["period"] == {
            "from": "2024-01-15T08:00:00+00:00",
            "to": "2024-06-29T09:30:00+02:00",
        }

    def test_dates_round_trip(self, commits: list[CommitInfo]) -> None:
        period = scan_report(commits)["summary"]["period"]

        assert datetime.fromisoformat(period["to"]) == commits[0].authored_at
        assert datetime.fromisoformat(period["to"]).utcoffset() == timedelta(hours=2)


class TestMarkdownReport:
    def test_scan_markdown(self, commits: list[CommitInfo]) -> None:
        text = to_markdown(scan_report(commits))
        lines = text.splitlines()

        assert lines[0] == "# Git Detective — Most frequently changed files"
        assert "- **Repository:** /work/my repo" in lines
        assert "- **Generated:** 2024-07-01T12:00:00+00:00" in lines
        # Rendered: "limit=100, top=10, exclude=[*.lock]"
        assert "- **Parameters:** limit=100, top=10, exclude=\\[\\*.lock\\]" in lines
        assert "| # | File | Commits | Churn | Added | Deleted |" in lines
        assert "|--:|:--|--:|--:|--:|--:|" in lines
        assert "| 1 | src/app.py | 2 | 20 | 14 | 6 |" in lines
        assert "| 2 | docs/zażółć.md | 1 | 3 | 3 | 0 |" in lines

    def test_hotspots_markdown(self, commits: list[CommitInfo]) -> None:
        text = to_markdown(hotspots_report(commits))

        assert text.startswith("# Git Detective — Change hotspots\n")
        assert (
            "| # | File | Commits | Churn | Recent | Hotspot Score | F | C | R |"
            in text
        )
        assert "| 1 | src/app.py | 2 | 20 | 1 | 100.0 | 1.00 | 1.00 | 1.00 |" in text
        assert "not a probability of bugs" in text

    def test_empty_markdown(self) -> None:
        text = to_markdown(scan_report([]))

        assert "_No files to report._" in text
        assert "| # |" not in text

    def test_special_characters_cannot_break_table(self) -> None:
        nasty = "src/a|b_*c*`<script>`[x](y).py"
        report = scan_report(
            [commit(GENERATED, (nasty, 1, 0), ("ok.py", 1, 0), sha="c")]
        )

        rows = [line for line in to_markdown(report).splitlines() if "a\\|b" in line]

        assert len(rows) == 1
        unescaped_pipes = re.findall(r"(?<!\\)\|", rows[0])
        assert len(unescaped_pipes) == 7  # 6 columns -> 7 separators
        assert "<script>" not in rows[0]


class TestEscapeMarkdown:
    @pytest.mark.parametrize(
        ("raw", "escaped"),
        [
            ("plain/path.py", "plain/path.py"),
            ("a|b", "a\\|b"),
            ("__init__.py", "\\_\\_init\\_\\_.py"),
            ("*bold*", "\\*bold\\*"),
            ("`code`", "\\`code\\`"),
            ("<script>", "\\<script\\>"),
            ("[x](http://y)", "\\[x\\](http://y)"),
            ("a&amp;b", "a\\&amp;b"),
            ("~~strike~~", "\\~\\~strike\\~\\~"),
            ("$x$", "\\$x\\$"),
            ("back\\slash", "back\\\\slash"),
            ("line1\nline2\r\nline3", "line1 line2 line3"),
        ],
    )
    def test_escaping(self, raw: str, escaped: str) -> None:
        assert escape_markdown(raw) == escaped


# --- Authors report ----------------------------------------------------------


def authored(
    name: str, email: str, when: datetime, *changes: tuple[str, int, int]
) -> CommitInfo:
    return CommitInfo(
        hexsha="f" * 40,
        author_name=name,
        author_email=email,
        authored_at=when,
        message="msg",
        files=tuple(FileChange(p, a, d) for p, a, d in changes),
    )


@pytest.fixture
def team_commits() -> list[CommitInfo]:
    return [
        authored(
            "Alice",
            "alice@example.com",
            datetime(2024, 5, 3, 10, 0, tzinfo=WARSAW),
            ("src/app.py", 10, 2),
            ("README.md", 1, 0),
        ),
        authored(
            "Alice",
            "alice@example.com",
            datetime(2024, 6, 20, 9, 0, tzinfo=UTC),
            ("src/app.py", 4, 4),
        ),
        authored(
            "Bob | <script>",
            "bob@example.com",
            datetime(2024, 6, 1, 8, 0, tzinfo=UTC),
            ("docs/guide.md", 30, 0),
        ),
    ]


def authors_report(
    commits: list[CommitInfo], *, top: int = 10, show_emails: bool = False
) -> dict:
    ctx = ReportContext(
        repository=Path("/work/repo"),
        generated_at=GENERATED,
        commits=commits,
        parameters={"limit": 500, "top": top, "show_emails": show_emails},
    )
    return build_authors_report(
        compute_author_stats(commits), ctx, top=top, show_emails=show_emails
    )


class TestAuthorsReport:
    def test_json_structure(self, team_commits: list[CommitInfo]) -> None:
        data = json.loads(to_json(authors_report(team_commits)))

        assert data["report"] == "authors"
        assert list(data)[-1] == "authors"
        assert data["summary"] == {
            "commits_analyzed": 3,
            "authors_total": 2,
            "authors_reported": 2,
            "period": {
                "from": "2024-05-03T10:00:00+02:00",
                "to": "2024-06-20T09:00:00+00:00",
            },
        }
        alice = data["authors"][0]
        assert alice == {
            "rank": 1,
            "name": "Alice",
            "commits": 2,
            "churn": 21,
            "added": 15,
            "deleted": 6,
            "files_touched": 2,
            "active_days": 2,
            "first_commit": "2024-05-03T10:00:00+02:00",
            "last_commit": "2024-06-20T09:00:00+00:00",
            "top_files": [
                {"path": "src/app.py", "commits": 2, "churn": 20},
                {"path": "README.md", "commits": 1, "churn": 1},
            ],
            "activity_by_month": {"2024-05": 1, "2024-06": 1},
        }

    def test_emails_are_hidden_by_default(self, team_commits: list[CommitInfo]) -> None:
        text = to_json(authors_report(team_commits))

        assert "@example.com" not in text
        assert all("emails" not in a for a in json.loads(text)["authors"])

    def test_emails_can_be_included(self, team_commits: list[CommitInfo]) -> None:
        data = json.loads(to_json(authors_report(team_commits, show_emails=True)))

        assert data["authors"][0]["emails"] == ["alice@example.com"]
        assert list(data["authors"][0])[:3] == ["rank", "name", "emails"]

    def test_top_limits_authors(self, team_commits: list[CommitInfo]) -> None:
        report = authors_report(team_commits, top=1)

        assert [a["name"] for a in report["authors"]] == ["Alice"]
        assert report["summary"]["authors_total"] == 2

    def test_markdown(self, team_commits: list[CommitInfo]) -> None:
        text = to_markdown(authors_report(team_commits))
        lines = text.splitlines()

        assert lines[0] == "# Git Detective — Contributors"
        assert "- **Authors:** 2 (showing 2)" in lines
        assert "Emails" not in text
        assert "@example.com" not in text
        assert (
            "| 1 | Alice | 2 | 21 | 2 | 2 | 2024-05-03 | 2024-06-20 "
            "| src/app.py, README.md | 2024-05: 1, 2024-06: 1 |"
        ) in lines
        assert "not productivity" in text

    def test_markdown_escapes_author_names(
        self, team_commits: list[CommitInfo]
    ) -> None:
        text = to_markdown(authors_report(team_commits))
        row = next(line for line in text.splitlines() if "Bob" in line)

        assert "Bob \\| \\<script\\>" in row
        assert "<script>" not in text
        assert len(re.findall(r"(?<!\\)\|", row)) == 11  # 10 columns

    def test_markdown_with_emails(self, team_commits: list[CommitInfo]) -> None:
        text = to_markdown(authors_report(team_commits, show_emails=True))

        assert "| # | Author | Emails | Commits |" in text
        assert "alice@example.com" in text

    def test_empty_report(self) -> None:
        report = authors_report([])

        assert report["authors"] == []
        assert "_No authors to report._" in to_markdown(report)

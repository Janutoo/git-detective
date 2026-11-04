"""Tests for report building and rendering. No Git or CLI involved."""

import json
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from git_detective import __version__
from git_detective.core.analyzer import aggregate_file_stats
from git_detective.core.hotspots import compute_hotspots
from git_detective.models import CommitInfo, FileChange
from git_detective.reporting import (
    SCHEMA_VERSION,
    ReportContext,
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

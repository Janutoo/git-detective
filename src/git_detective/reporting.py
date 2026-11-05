"""Build analysis reports and render them as JSON or Markdown.

A report is first built as a plain, JSON-ready ``dict`` (only str, int, float,
bool, None, lists and dicts). That dict is the single source of truth for
every output format, so JSON and Markdown can never disagree.

This module knows nothing about Git or the CLI; it only consumes models.
"""

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import PurePath
from typing import Any

from git_detective import __version__
from git_detective.models import AuthorStats, CommitInfo, FileStats, Hotspot

SCHEMA_VERSION = 1
SCORE_DISCLAIMER = (
    "Hotspot Score (0-100) is a relative change-activity indicator, "
    "not a probability of bugs."
)
AUTHORS_DISCLAIMER = (
    "Commits and churn measure activity, not productivity or the value of "
    "anyone's work."
)

Report = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ReportContext:
    """Information about one analysis run that every report includes."""

    repository: PurePath
    generated_at: datetime
    commits: Sequence[CommitInfo]
    parameters: Mapping[str, Any]


def build_scan_report(
    stats: Sequence[FileStats], context: ReportContext, *, top: int
) -> Report:
    """Report for ``scan``: the most frequently changed files."""
    files = [
        {
            "rank": rank,
            "path": item.path,
            "commits": item.commits,
            "churn": item.churn,
            "added": item.added,
            "deleted": item.deleted,
        }
        for rank, item in enumerate(stats[:top], start=1)
    ]
    return _build_report(
        "scan",
        context,
        summary={"files_changed": len(stats), "files_reported": len(files)},
        sections={"files": files},
    )


def build_hotspots_report(
    hotspots: Sequence[Hotspot], context: ReportContext, *, top: int
) -> Report:
    """Report for ``hotspots``: files ranked by Hotspot Score with components."""
    files = [
        {
            "rank": rank,
            "path": item.path,
            "commits": item.commits,
            "churn": item.churn,
            "recent_commits": item.recent_commits,
            "score": round(item.score, 2),
            "components": {
                "frequency": round(item.frequency_norm, 4),
                "churn": round(item.churn_norm, 4),
                "recency": round(item.recency_norm, 4),
            },
        }
        for rank, item in enumerate(hotspots[:top], start=1)
    ]
    return _build_report(
        "hotspots",
        context,
        summary={"files_changed": len(hotspots), "files_reported": len(files)},
        sections={"files": files},
    )


def build_authors_report(
    authors: Sequence[AuthorStats],
    context: ReportContext,
    *,
    top: int,
    show_emails: bool = False,
) -> Report:
    """Report for ``authors``: contributor activity.

    E-mail addresses are personal data and are only included when
    ``show_emails`` is True; otherwise the ``emails`` key is absent.
    """
    rows = []
    for rank, author in enumerate(authors[:top], start=1):
        row: dict[str, Any] = {"rank": rank, "name": author.name}
        if show_emails:
            row["emails"] = list(author.emails)
        row |= {
            "commits": author.commits,
            "churn": author.churn,
            "added": author.added,
            "deleted": author.deleted,
            "files_touched": author.files_touched,
            "active_days": author.active_days,
            "first_commit": author.first_commit.isoformat(),
            "last_commit": author.last_commit.isoformat(),
            "top_files": [
                {"path": f.path, "commits": f.commits, "churn": f.churn}
                for f in author.top_files
            ],
            "activity_by_month": dict(author.activity_by_month),
        }
        rows.append(row)
    return _build_report(
        "authors",
        context,
        summary={"authors_total": len(authors), "authors_reported": len(rows)},
        sections={"authors": rows},
    )


def to_json(report: Mapping[str, Any]) -> str:
    """Render a report as pretty-printed UTF-8 JSON (ends with a newline)."""
    return json.dumps(report, indent=2, ensure_ascii=False) + "\n"


def to_markdown(report: Mapping[str, Any]) -> str:
    """Render a report as Markdown with a GitHub-flavored table."""
    spec = _MARKDOWN[report["report"]]
    summary = report["summary"]
    lines = [
        f"# Git Detective — {spec.title}",
        "",
        f"- **Repository:** {escape_markdown(report['repository']['path'])}",
        f"- **Generated:** {escape_markdown(report['generated_at'])}",
        f"- **Commits analyzed:** {summary['commits_analyzed']}"
        + _format_period(summary["period"]),
        *(f"- {line}" for line in spec.summary_lines(summary)),
        f"- **Parameters:** {_format_parameters(report['parameters'])}",
        "",
    ]

    rows = report[spec.rows_key]
    columns = [c for c in spec.columns if c.visible(report)]
    if rows:
        lines.append("| " + " | ".join(c.header for c in columns) + " |")
        lines.append("|" + "|".join(c.align for c in columns) + "|")
        for row in rows:
            cells = [escape_markdown(c.cell(row)) for c in columns]
            lines.append("| " + " | ".join(cells) + " |")
    else:
        lines.append(f"_{spec.empty_message}_")

    for note in spec.footer:
        lines += ["", note]
    return "\n".join(lines) + "\n"


# Characters that can trigger Markdown/GFM formatting, links, HTML, entities,
# math or break a table cell. Backslash-escaping any ASCII punctuation is
# valid CommonMark, so escaping these is always safe.
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]<>|~&$])")
_LINE_BREAKS = re.compile(r"[\r\n]+")


def escape_markdown(text: str) -> str:
    """Escape text for use inline or in a table cell; newlines become spaces."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", _LINE_BREAKS.sub(" ", text))


def _build_report(
    kind: str,
    context: ReportContext,
    *,
    summary: Mapping[str, Any],
    sections: Mapping[str, Any],
) -> Report:
    """Wrap report-specific summary fields and sections in the common envelope."""
    return {
        "schema_version": SCHEMA_VERSION,
        "report": kind,
        "tool": {"name": "git-detective", "version": __version__},
        "generated_at": _to_primitive(context.generated_at),
        "repository": {"path": _to_primitive(context.repository)},
        "parameters": _to_primitive(context.parameters),
        "summary": {
            "commits_analyzed": len(context.commits),
            **summary,
            "period": _period(context.commits),
        },
        **sections,
    }


def _period(commits: Sequence[CommitInfo]) -> dict[str, str] | None:
    if not commits:
        return None
    dates = [commit.authored_at for commit in commits]
    return {"from": min(dates).isoformat(), "to": max(dates).isoformat()}


def _to_primitive(value: Any) -> Any:
    """Convert values to JSON-ready types (datetimes become ISO 8601 strings)."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, PurePath):
        return value.as_posix()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _to_primitive(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_to_primitive(item) for item in value]
    return value


def _format_period(period: Mapping[str, str] | None) -> str:
    if period is None:
        return ""
    return f" ({escape_markdown(period['from'])} → {escape_markdown(period['to'])})"


def _format_parameters(parameters: Mapping[str, Any]) -> str:
    return ", ".join(
        f"{escape_markdown(key)}={escape_markdown(_format_value(value))}"
        for key, value in parameters.items()
    )


def _format_value(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, list):
        return "[" + ", ".join(_format_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return (
            "{" + ", ".join(f"{k}={_format_value(v)}" for k, v in value.items()) + "}"
        )
    return str(value)


_RIGHT = "--:"
_LEFT = ":--"


def _always(_report: Mapping[str, Any]) -> bool:
    return True


@dataclass(frozen=True, slots=True)
class _Column:
    header: str
    cell: Callable[[Mapping[str, Any]], str]
    align: str = _RIGHT
    visible: Callable[[Mapping[str, Any]], bool] = _always


@dataclass(frozen=True, slots=True)
class _MarkdownSpec:
    title: str
    rows_key: str
    columns: list[_Column]
    summary_lines: Callable[[Mapping[str, Any]], list[str]]
    empty_message: str
    footer: tuple[str, ...] = ()


def _files_summary(summary: Mapping[str, Any]) -> list[str]:
    return [
        f"**Files changed:** {summary['files_changed']}"
        f" (showing {summary['files_reported']})"
    ]


def _authors_summary(summary: Mapping[str, Any]) -> list[str]:
    return [
        f"**Authors:** {summary['authors_total']}"
        f" (showing {summary['authors_reported']})"
    ]


def _shows_emails(report: Mapping[str, Any]) -> bool:
    return bool(report["parameters"].get("show_emails"))


def _monthly_activity(row: Mapping[str, Any]) -> str:
    return ", ".join(f"{m}: {n}" for m, n in row["activity_by_month"].items())


_RANK = _Column("#", lambda r: str(r["rank"]))

_MARKDOWN: dict[str, _MarkdownSpec] = {
    "scan": _MarkdownSpec(
        title="Most frequently changed files",
        rows_key="files",
        columns=[
            _RANK,
            _Column("File", lambda r: r["path"], _LEFT),
            _Column("Commits", lambda r: str(r["commits"])),
            _Column("Churn", lambda r: str(r["churn"])),
            _Column("Added", lambda r: str(r["added"])),
            _Column("Deleted", lambda r: str(r["deleted"])),
        ],
        summary_lines=_files_summary,
        empty_message="No files to report.",
    ),
    "hotspots": _MarkdownSpec(
        title="Change hotspots",
        rows_key="files",
        columns=[
            _RANK,
            _Column("File", lambda r: r["path"], _LEFT),
            _Column("Commits", lambda r: str(r["commits"])),
            _Column("Churn", lambda r: str(r["churn"])),
            _Column("Recent", lambda r: str(r["recent_commits"])),
            _Column("Hotspot Score", lambda r: f"{r['score']:.1f}"),
            _Column("F", lambda r: f"{r['components']['frequency']:.2f}"),
            _Column("C", lambda r: f"{r['components']['churn']:.2f}"),
            _Column("R", lambda r: f"{r['components']['recency']:.2f}"),
        ],
        summary_lines=_files_summary,
        empty_message="No files to report.",
        footer=(
            "F, C and R are the normalized (0-1) commit frequency, churn and "
            "recent activity used to compute the score.",
            f"> {SCORE_DISCLAIMER}",
        ),
    ),
    "authors": _MarkdownSpec(
        title="Contributors",
        rows_key="authors",
        columns=[
            _RANK,
            _Column("Author", lambda r: r["name"], _LEFT),
            _Column("Emails", lambda r: ", ".join(r["emails"]), _LEFT, _shows_emails),
            _Column("Commits", lambda r: str(r["commits"])),
            _Column("Churn", lambda r: str(r["churn"])),
            _Column("Files", lambda r: str(r["files_touched"])),
            _Column("Active days", lambda r: str(r["active_days"])),
            _Column("First", lambda r: r["first_commit"][:10]),
            _Column("Last", lambda r: r["last_commit"][:10]),
            _Column(
                "Top files",
                lambda r: ", ".join(f["path"] for f in r["top_files"]),
                _LEFT,
            ),
            _Column("Commits per month (UTC)", _monthly_activity, _LEFT),
        ],
        summary_lines=_authors_summary,
        empty_message="No authors to report.",
        footer=(f"> {AUTHORS_DISCLAIMER}",),
    ),
}

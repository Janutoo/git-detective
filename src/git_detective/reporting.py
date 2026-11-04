"""Build analysis reports and render them as JSON or Markdown.

A report is first built as a plain, JSON-ready ``dict`` (only str, int, float,
bool, None, lists and dicts). That dict is the single source of truth for
every output format, so JSON and Markdown can never disagree.

This module knows nothing about Git or the CLI; it only consumes models.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import PurePath
from typing import Any

from git_detective import __version__
from git_detective.models import CommitInfo, FileStats, Hotspot

SCHEMA_VERSION = 1
SCORE_DISCLAIMER = (
    "Hotspot Score (0-100) is a relative change-activity indicator, "
    "not a probability of bugs."
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
    return _build_report("scan", context, files_changed=len(stats), files=files)


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
    return _build_report("hotspots", context, files_changed=len(hotspots), files=files)


def to_json(report: Mapping[str, Any]) -> str:
    """Render a report as pretty-printed UTF-8 JSON (ends with a newline)."""
    return json.dumps(report, indent=2, ensure_ascii=False) + "\n"


def to_markdown(report: Mapping[str, Any]) -> str:
    """Render a report as Markdown with a GitHub-flavored table."""
    kind = report["report"]
    summary = report["summary"]
    lines = [
        f"# Git Detective — {_TITLES[kind]}",
        "",
        f"- **Repository:** {escape_markdown(report['repository']['path'])}",
        f"- **Generated:** {escape_markdown(report['generated_at'])}",
        f"- **Commits analyzed:** {summary['commits_analyzed']}"
        + _format_period(summary["period"]),
        f"- **Files changed:** {summary['files_changed']}"
        f" (showing {summary['files_reported']})",
        f"- **Parameters:** {_format_parameters(report['parameters'])}",
        "",
    ]

    columns = _COLUMNS[kind]
    if report["files"]:
        lines.append("| " + " | ".join(header for header, _, _ in columns) + " |")
        lines.append("|" + "|".join(align for _, _, align in columns) + "|")
        for row in report["files"]:
            cells = [escape_markdown(fmt(row)) for _, fmt, _ in columns]
            lines.append("| " + " | ".join(cells) + " |")
    else:
        lines.append("_No files to report._")

    if kind == "hotspots":
        lines += [
            "",
            "F, C and R are the normalized (0-1) commit frequency, churn and "
            "recent activity used to compute the score.",
            "",
            f"> {SCORE_DISCLAIMER}",
        ]
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
    files_changed: int,
    files: list[dict[str, Any]],
) -> Report:
    return {
        "schema_version": SCHEMA_VERSION,
        "report": kind,
        "tool": {"name": "git-detective", "version": __version__},
        "generated_at": _to_primitive(context.generated_at),
        "repository": {"path": _to_primitive(context.repository)},
        "parameters": _to_primitive(context.parameters),
        "summary": {
            "commits_analyzed": len(context.commits),
            "files_changed": files_changed,
            "files_reported": len(files),
            "period": _period(context.commits),
        },
        "files": files,
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


_TITLES = {
    "scan": "Most frequently changed files",
    "hotspots": "Change hotspots",
}

_RIGHT = "--:"
_LEFT = ":--"

# (header, cell formatter, alignment) per report type.
_COLUMNS = {
    "scan": [
        ("#", lambda r: str(r["rank"]), _RIGHT),
        ("File", lambda r: r["path"], _LEFT),
        ("Commits", lambda r: str(r["commits"]), _RIGHT),
        ("Churn", lambda r: str(r["churn"]), _RIGHT),
        ("Added", lambda r: str(r["added"]), _RIGHT),
        ("Deleted", lambda r: str(r["deleted"]), _RIGHT),
    ],
    "hotspots": [
        ("#", lambda r: str(r["rank"]), _RIGHT),
        ("File", lambda r: r["path"], _LEFT),
        ("Commits", lambda r: str(r["commits"]), _RIGHT),
        ("Churn", lambda r: str(r["churn"]), _RIGHT),
        ("Recent", lambda r: str(r["recent_commits"]), _RIGHT),
        ("Hotspot Score", lambda r: f"{r['score']:.1f}", _RIGHT),
        ("F", lambda r: f"{r['components']['frequency']:.2f}", _RIGHT),
        ("C", lambda r: f"{r['components']['churn']:.2f}", _RIGHT),
        ("R", lambda r: f"{r['components']['recency']:.2f}", _RIGHT),
    ],
}

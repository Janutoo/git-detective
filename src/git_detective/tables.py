"""Rich tables for the terminal output of each command.

Every value that comes from the analyzed repository (paths, author names) is
passed through :func:`rich.markup.escape`, so text such as ``[bold]`` in a file
name is shown literally instead of being interpreted as Rich markup.
"""

from collections.abc import Sequence

from rich.markup import escape
from rich.table import Table

from git_detective.models import AuthorStats, FileStats, Hotspot


def build_scan_table(stats: Sequence[FileStats]) -> Table:
    table = Table(title="Most frequently changed files", title_justify="left")
    table.add_column("#", justify="right", style="dim")
    table.add_column("File", overflow="fold")
    table.add_column("Commits", justify="right", style="bold")
    table.add_column("Churn", justify="right")
    table.add_column("+", justify="right", style="green")
    table.add_column("-", justify="right", style="red")

    for rank, item in enumerate(stats, start=1):
        table.add_row(
            str(rank),
            escape(item.path),
            str(item.commits),
            str(item.churn),
            str(item.added),
            str(item.deleted),
        )
    return table


def build_hotspot_table(ranked: Sequence[Hotspot]) -> Table:
    table = Table(
        title="Change hotspots",
        title_justify="left",
        caption="Hotspot Score (0-100) = relative change activity, "
        "not a probability of bugs.",
        caption_justify="left",
    )
    table.add_column("#", justify="right", style="dim")
    table.add_column("File", overflow="fold")
    table.add_column("Commits", justify="right")
    table.add_column("Churn", justify="right")
    table.add_column("Recent", justify="right")
    table.add_column("Hotspot Score", justify="right", style="bold")

    for rank, item in enumerate(ranked, start=1):
        table.add_row(
            str(rank),
            escape(item.path),
            str(item.commits),
            str(item.churn),
            str(item.recent_commits),
            f"[{_score_style(item.score)}]{item.score:.1f}[/]",
        )
    return table


def build_authors_table(
    authors: Sequence[AuthorStats], *, show_emails: bool = False
) -> Table:
    table = Table(
        title="Contributors",
        title_justify="left",
        caption="Commits and churn measure activity, not productivity or value.",
        caption_justify="left",
    )
    table.add_column("#", justify="right", style="dim")
    table.add_column("Author", overflow="fold", style="bold")
    if show_emails:
        table.add_column("Emails", overflow="fold")
    table.add_column("Commits", justify="right")
    table.add_column("Churn", justify="right")
    table.add_column("Files", justify="right")
    table.add_column("Active days", justify="right")
    table.add_column("First", justify="right")
    table.add_column("Last", justify="right")
    table.add_column("Top files", overflow="fold")

    for rank, author in enumerate(authors, start=1):
        row = [str(rank), escape(author.name)]
        if show_emails:
            row.append(escape(", ".join(author.emails)))
        row += [
            str(author.commits),
            str(author.churn),
            str(author.files_touched),
            str(author.active_days),
            author.first_commit.date().isoformat(),
            author.last_commit.date().isoformat(),
            escape(", ".join(f.path for f in author.top_files)),
        ]
        table.add_row(*row)
    return table


def _score_style(score: float) -> str:
    if score >= 70:
        return "red"
    if score >= 40:
        return "yellow"
    return "green"

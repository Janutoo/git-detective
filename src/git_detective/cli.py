"""Command-line interface for Git Detective.

This is the presentation layer: it parses arguments, calls the core logic and
renders results with Rich. It contains no Git or analysis logic of its own.
"""

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from git_detective import __version__
from git_detective.core import DEFAULT_COMMIT_LIMIT, GitReader, aggregate_file_stats
from git_detective.exceptions import GitDetectiveError
from git_detective.models import FileStats

DEFAULT_TOP = 10

app = typer.Typer(
    help="Analyze Git history to find code hotspots and contributor activity.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
error_console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"git-detective {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Git Detective - investigate your repository history."""


@app.command()
def scan(
    path: Path = typer.Argument(
        Path("."), help="Path to a local Git repository (or any directory in it)."
    ),
    limit: int = typer.Option(
        DEFAULT_COMMIT_LIMIT, "--limit", "-l", min=1, help="Max commits to analyze."
    ),
    top: int = typer.Option(
        DEFAULT_TOP, "--top", "-t", min=1, help="Number of files to display."
    ),
) -> None:
    """Show the most frequently changed files in recent history."""
    try:
        with GitReader(path) as reader:
            commits = reader.read_commits(limit=limit)
            repo_root = reader.root
    except GitDetectiveError as exc:
        error_console.print(f"[bold red]Error:[/] {exc}")
        raise typer.Exit(code=1) from exc

    if not commits:
        console.print(f"[yellow]No commits found in[/] {repo_root}")
        return

    stats = aggregate_file_stats(commits)
    console.print(
        f"Analyzed [bold cyan]{len(commits)}[/] commits in [bold]{repo_root}[/] "
        f"([bold]{len(stats)}[/] files changed)"
    )
    console.print(_build_table(stats[:top]))


def _build_table(stats: list[FileStats]) -> Table:
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
            item.path,
            str(item.commits),
            str(item.churn),
            str(item.added),
            str(item.deleted),
        )
    return table

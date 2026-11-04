# Git Detective

Git Detective analyzes the history of a local Git repository to show where
the code changes most often. It is the starting point for finding code
hotspots and understanding contributor activity.

> **Status:** early development (stage 1). The `scan` command lists the most
> frequently changed files together with their code churn.

## Features

- Reads the most recent commits (100 by default, configurable).
- Extracts hash, author, date, message and changed files for each commit.
- Counts added and deleted lines per file (binary files count as 0).
- Aggregates commit count and churn (`added + deleted`) per file.
- Read-only: Git Detective never checks out files, runs hooks, or executes
  code from the analyzed repository.

Merge commits are skipped, because their changes are already counted in the
commits being merged.

## Requirements

- Python 3.12+
- Git installed and available on `PATH`

## Installation

```bash
git clone https://github.com/KamilJanusz/git-detective.git
cd git-detective
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

```bash
# Scan the repository in the current directory
git-detective scan .

# Analyze the last 500 commits and show the top 20 files
git-detective scan path/to/repo --limit 500 --top 20
```

| Option        | Default | Description                    |
| ------------- | ------- | ------------------------------ |
| `--limit, -l` | 100     | Maximum commits to analyze     |
| `--top, -t`   | 10      | Number of files to display     |

Example output:

```
Analyzed 100 commits in /home/user/project (42 files changed)
Most frequently changed files
┏━━━┳━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━┳━━━━━┳━━━━━┓
┃ # ┃ File               ┃ Commits ┃ Churn ┃   + ┃   - ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━╇━━━━━╇━━━━━┩
│ 1 │ src/app/main.py    │      27 │   812 │ 530 │ 282 │
│ 2 │ README.md          │      14 │   120 │  95 │  25 │
└───┴────────────────────┴─────────┴───────┴─────┴─────┘
```

## Project structure

```
src/git_detective/
├── cli.py              # Typer CLI + Rich output (presentation layer)
├── exceptions.py       # Project-specific errors
├── core/
│   ├── git_reader.py   # Reads commits and per-file line changes from Git
│   └── analyzer.py     # Aggregates commits into per-file statistics
└── models/
    ├── commit.py       # CommitInfo, FileChange
    └── file_stats.py   # FileStats
```

## Development

```bash
pytest              # run the test suite
ruff check .        # lint
ruff format .       # format
```

Tests create temporary Git repositories, so they never depend on your own
repositories or global Git configuration.

## Roadmap

- Contributor statistics (who changes what, and how often)
- Filters such as `--since` and path patterns
- JSON / Markdown report export
- Hotspot scoring

# Git Detective

Git Detective analyzes the history of a local Git repository to show where
the code changes most often. It is the starting point for finding code
hotspots and understanding contributor activity.

> **Status:** early development (stage 2). `scan` lists the most frequently
> changed files; `hotspots` ranks files by a Hotspot Score.

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

## Hotspots

```bash
git-detective hotspots . --limit 100 --top 10 --recent-days 30
```

`hotspots` accepts `--limit` and `--top` like `scan`, plus `--recent-days`
(default 30) for the size of the recent activity window.

Every file gets three metrics:

| Metric | Meaning                                                    |
| ------ | ---------------------------------------------------------- |
| **F**  | commit frequency - commits that touched the file           |
| **C**  | code churn - added + deleted lines                         |
| **R**  | recent activity - commits in the last `--recent-days` days |

Each metric is scaled to 0-1 with `log1p(x) / log1p(max)` and combined:

```
Hotspot Score = 100 × (0.45·F + 0.35·C + 0.20·R)
```

Log scaling keeps a single huge file (a lockfile, generated code) from
pushing every other file's churn to almost zero. Files are sorted by score,
with ties broken by commits, churn, recent commits and path.

**The Hotspot Score is a relative activity indicator, not a probability of
bugs.** It shows where change is concentrated, which is a good place to start
a code review or refactoring discussion.

Weights can be changed from Python (they must be non-negative and sum to 1):

```python
from datetime import UTC, datetime
from git_detective.core import GitReader, HotspotWeights, compute_hotspots

with GitReader(".") as reader:
    commits = reader.read_commits(limit=500)

weights = HotspotWeights(frequency=0.6, churn=0.2, recency=0.2)
for h in compute_hotspots(commits, reference_time=datetime.now(UTC), weights=weights)[:10]:
    print(f"{h.score:5.1f}  {h.path}")
```

### Limitations

- **Relative:** scores are normalized against the most active file in the
  analyzed commits, so they are not comparable across repositories or runs
  with a different `--limit`. The top file is always close to 100.
- **Compressed differences:** log scaling also narrows gaps between files
  with similar activity, so look at the ranking more than at exact numbers.
- **Churn is not complexity:** file size and code complexity are ignored.
- **Noise:** lockfiles, generated or vendored code and mass reformatting
  inflate churn.
- **Renames** split a file's history between the old and the new path.
- **Dates** are author dates; rebases and wrong clocks can shift commits
  in or out of the recent window.
- Merge commits are skipped and binary files have zero churn.

## Project structure

```
src/git_detective/
├── cli.py              # Typer CLI + Rich output (presentation layer)
├── exceptions.py       # Project-specific errors
├── core/
│   ├── git_reader.py   # Reads commits and per-file line changes from Git
│   ├── analyzer.py     # Aggregates commits into per-file statistics
│   └── hotspots.py     # Hotspot Score: normalization, weights, ranking
└── models/
    ├── commit.py       # CommitInfo, FileChange
    ├── file_stats.py   # FileStats
    └── hotspot.py      # Hotspot
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
- Combining hotspots with code complexity

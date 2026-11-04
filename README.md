# Git Detective

[![CI](https://github.com/Janutoo/git-detective/actions/workflows/ci.yml/badge.svg)](https://github.com/Janutoo/git-detective/actions/workflows/ci.yml)

Git Detective analyzes the history of a local Git repository to show where
the code changes most often. It is the starting point for finding code
hotspots and understanding contributor activity.

> **Status:** early development (stage 3). `scan` lists the most frequently
> changed files, `hotspots` ranks files by a Hotspot Score. Both commands can
> exclude files and export JSON or Markdown reports.

## Features

- Reads the most recent commits (100 by default, configurable).
- Extracts hash, author, date, message and changed files for each commit.
- Counts added and deleted lines per file (binary files count as 0).
- Aggregates commit count and churn (`added + deleted`) per file.
- Ranks files by a Hotspot Score (frequency, churn, recent activity).
- Excludes files with `.gitignore`-style patterns.
- Exports reports as JSON or Markdown.
- Read-only: Git Detective never checks out files, runs hooks, or executes
  code from the analyzed repository.

Merge commits are skipped, because their changes are already counted in the
commits being merged.

## Requirements

- Python 3.12+
- Git installed and available on `PATH`

## Installation

```bash
git clone https://github.com/Janutoo/git-detective.git
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

# Rank files by Hotspot Score
git-detective hotspots . --recent-days 30
```

Options shared by `scan` and `hotspots`:

| Option              | Default | Description                                         |
| ------------------- | ------- | --------------------------------------------------- |
| `--limit, -l`       | 100     | Maximum commits to analyze                          |
| `--top, -t`         | 10      | Number of files to display                          |
| `--exclude, -e`     | –       | Skip files matching a pattern (repeatable)          |
| `--format, -f`      | `table` | `table`, `json` or `markdown`                       |
| `--output, -o`      | stdout  | Write the report to a file (`json`/`markdown` only) |
| `--recent-days, -r` | 30      | `hotspots` only: size of the recent activity window |

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
ranked = compute_hotspots(commits, reference_time=datetime.now(UTC), weights=weights)
for h in ranked[:10]:
    print(f"{h.score:5.1f}  {h.path}")
```

### Limitations

- **Relative:** scores are normalized against the most active file in the
  analyzed commits, so they are not comparable across repositories or runs
  with a different `--limit` or different exclude patterns. The top file is
  always close to 100.
- **Compressed differences:** log scaling also narrows gaps between files
  with similar activity, so look at the ranking more than at exact numbers.
- **Churn is not complexity:** file size and code complexity are ignored.
- **Noise:** lockfiles, generated or vendored code and mass reformatting
  inflate churn - exclude them (see below).
- **Renames** split a file's history between the old and the new path.
- **Dates** are author dates; rebases and wrong clocks can shift commits
  in or out of the recent window.
- Merge commits are skipped and binary files have zero churn.

## Excluding files

```bash
git-detective hotspots . --exclude "*.lock" --exclude "dist/**"
git-detective scan . -e "node_modules/" -e "*.min.js"
```

Patterns use **`.gitignore` syntax** and are matched against
repository-relative paths with forward slashes (as Git reports them, e.g.
`src/app/main.py`):

| Pattern             | Matches                                                 |
| ------------------- | ------------------------------------------------------- |
| `*.lock`            | any `.lock` file at any depth (no `/` = file name only) |
| `dist/**`, `/dist/` | everything under the top-level `dist` directory         |
| `node_modules/`     | every `node_modules` directory, at any depth            |
| `src/*.py`          | `.py` files directly in `src` (`*` does not cross `/`)  |
| `src/**/*.py`       | `.py` files anywhere under `src` (`**` does)            |
| `!keep.lock`        | re-includes a file excluded by an earlier pattern       |

You can also commit a **`.gitdetectiveignore`** file to the repository root.
It is read automatically when present (a missing file is fine); blank lines
and `#` comments are ignored:

```gitignore
# Dependency lockfiles
*.lock
package-lock.json

# Build output and vendored code
dist/
vendor/
```

Patterns from `.gitdetectiveignore` are applied first, then `--exclude`, so
`--exclude "!poetry.lock"` can bring back a file the ignore file hides.

Filtering happens **before** metrics are aggregated and normalized, so
excluding a huge lockfile changes the scores of the remaining files.
Excluded files are dropped, but commits are kept, so `--limit` still means
"the last N commits".

**Git Detective has no built-in exclusions.** Nothing is hidden unless you
ask for it; active patterns are shown above the table and listed in reports.

## Report export

```bash
# JSON to stdout (pipe-friendly, no colors or table borders)
git-detective hotspots . --format json | jq '.files[0]'

# JSON or Markdown to a file
git-detective hotspots . --format json --output hotspots.json
git-detective scan . -f markdown -o scan.md
```

`--output` writes UTF-8 and overwrites an existing file; the confirmation
message goes to stderr, so stdout stays clean. It requires `--format json`
or `--format markdown`.

### JSON format

```json
{
  "schema_version": 1,
  "report": "hotspots",
  "tool": { "name": "git-detective", "version": "0.1.0" },
  "generated_at": "2026-11-04T12:00:00+00:00",
  "repository": { "path": "/home/user/project" },
  "parameters": {
    "limit": 100,
    "top": 10,
    "recent_days": 30,
    "reference_time": "2026-11-04T12:00:00+00:00",
    "weights": { "frequency": 0.45, "churn": 0.35, "recency": 0.2 },
    "exclude": ["*.lock"],
    "ignore_file": ".gitdetectiveignore"
  },
  "summary": {
    "commits_analyzed": 100,
    "files_changed": 42,
    "files_reported": 10,
    "period": {
      "from": "2026-08-12T09:14:03+02:00",
      "to": "2026-11-03T18:40:11+01:00"
    }
  },
  "files": [
    {
      "rank": 1,
      "path": "src/app/main.py",
      "commits": 27,
      "churn": 812,
      "recent_commits": 9,
      "score": 97.12,
      "components": { "frequency": 1.0, "churn": 0.9604, "recency": 0.9358 }
    }
  ]
}
```

| Field              | Description                                                                   |
| ------------------ | ----------------------------------------------------------------------------- |
| `schema_version`   | Incremented on breaking changes to the structure                              |
| `report`           | `scan` or `hotspots`                                                          |
| `generated_at`     | When the report was created (ISO 8601 with UTC offset)                        |
| `parameters`       | Options used for the run, incl. effective exclude patterns                    |
| `summary.period`   | Author dates of the oldest and newest analyzed commit (`null` if none)        |
| `files`            | Top files; `scan` has `added`/`deleted`, `hotspots` has `score`, `components` |
| `files.components` | Normalized metrics F, C, R (0-1) behind the score                             |

All timestamps are ISO 8601 with their original UTC offset. Scores are
rounded to 2 decimals, components to 4.

### Markdown format

A heading, a list of run metadata and a GitHub-flavored table:

```markdown
# Git Detective — Change hotspots

- **Repository:** /home/user/project
- **Generated:** 2026-11-04T12:00:00+00:00
- **Commits analyzed:** 100 (2026-08-12T09:14:03+02:00 → 2026-11-03T18:40:11+01:00)
- **Files changed:** 42 (showing 10)
- **Parameters:** limit=100, top=10, recent\_days=30, ...

| # | File | Commits | Churn | Recent | Hotspot Score | F | C | R |
|--:|:--|--:|--:|--:|--:|--:|--:|--:|
| 1 | src/app/main.py | 27 | 812 | 9 | 97.1 | 1.00 | 0.96 | 0.94 |
```

File names and other values are escaped, so characters such as `|`, `_`,
`*`, `<` or `[` cannot break the table or inject formatting, links or HTML.

## Project structure

```
src/git_detective/
├── cli.py              # Typer CLI + Rich output (presentation layer)
├── reporting.py        # JSON / Markdown reports (no Git or CLI code)
├── exceptions.py       # Project-specific errors
├── core/
│   ├── git_reader.py   # Reads commits and per-file line changes from Git
│   ├── filters.py      # --exclude / .gitdetectiveignore path filtering
│   ├── analyzer.py     # Aggregates commits into per-file statistics
│   └── hotspots.py     # Hotspot Score: normalization, weights, ranking
└── models/
    ├── commit.py       # CommitInfo, FileChange
    ├── file_stats.py   # FileStats
    └── hotspot.py      # Hotspot
```

## Development

```bash
pytest                  # run the test suite
pytest tests/test_hotspots.py -k weights   # run a subset
ruff check .            # lint
ruff format .           # format (Python files and Python blocks in Markdown)
ruff format --check .   # verify formatting without changing files
```

Tests create temporary Git repositories, so they never depend on your own
repositories or global Git configuration.

### Continuous integration

GitHub Actions ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs
on every push to `main` and on every pull request, on Python 3.12 and 3.13:

1. `pip install -e ".[dev]"`
2. `ruff check .`
3. `ruff format --check .`
4. `pytest`

Any failing step fails the build. To reproduce CI locally, run the same three
commands from the list above.

## Roadmap

- Contributor statistics (who changes what, and how often)
- Time filters such as `--since` / `--until`
- Combining hotspots with code complexity

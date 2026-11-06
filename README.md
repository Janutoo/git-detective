# Git Detective

[![CI](https://github.com/Janutoo/git-detective/actions/workflows/ci.yml/badge.svg)](https://github.com/Janutoo/git-detective/actions/workflows/ci.yml)

Git Detective analyzes the history of a local Git repository to show where
the code changes most often. It is the starting point for finding code
hotspots and understanding contributor activity.

> **Status:** early development (stage 5). `scan` lists the most frequently
> changed files, `hotspots` ranks files by a Hotspot Score, `authors`
> summarizes contributor activity and `coupling` finds files that change
> together. Every command can exclude files and export JSON or Markdown
> reports. A local [REST API](#rest-api) runs the same analyses and keeps a
> history of their reports.

## Features

- Reads the most recent commits (100 by default, configurable).
- Extracts hash, author, date, message and changed files for each commit.
- Counts added and deleted lines per file (binary files count as 0).
- Aggregates commit count and churn (`added + deleted`) per file.
- Ranks files by a Hotspot Score (frequency, churn, recent activity).
- Summarizes contributor activity, with `.mailmap` alias support and
  e-mail addresses hidden by default.
- Detects file coupling: pairs of files that change in the same commits.
- Excludes files with `.gitignore`-style patterns.
- Exports reports as JSON or Markdown.
- Offers a local REST API that stores every report in SQLite, so old reports
  can be read without analyzing the repository again.
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

# Contributor activity over the last 500 commits
git-detective authors . --limit 500 --top 10

# Files that change together
git-detective coupling . --limit 500 --top 20
```

Options shared by all commands:

| Option          | Default                              | Description                                         |
| --------------- | ------------------------------------ | --------------------------------------------------- |
| `--limit, -l`   | 100 (`authors`, `coupling`: 500)     | Maximum commits to analyze                          |
| `--top, -t`     | 10 (`coupling`: 20)                  | Number of rows to display                           |
| `--exclude, -e` | –                                    | Skip files matching a pattern (repeatable)          |
| `--format, -f`  | `table`                              | `table`, `json` or `markdown`                       |
| `--output, -o`  | stdout                               | Write the report to a file (`json`/`markdown` only) |

Command-specific options:

| Option                   | Command    | Default   | Description                                  |
| ------------------------ | ---------- | --------- | -------------------------------------------- |
| `--recent-days, -r`      | `hotspots` | 30        | Size of the recent activity window           |
| `--sort, -s`             | `authors`  | `commits` | Rank by `commits` or `churn`                 |
| `--show-emails`          | `authors`  | off       | Include e-mail addresses in the output       |
| `--min-cochanges`        | `coupling` | 3         | Ignore pairs changed together less often     |
| `--max-files-per-commit` | `coupling` | 50        | Skip larger commits (bulk changes)           |

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

## Contributors

```bash
git-detective authors . --limit 500 --top 10
git-detective authors . --sort churn -f json -o authors.json
```

For every author: commits, churn (added + deleted lines), number of files
touched, the three most frequently changed files, number of active days,
first and last commit, and commits per month (in JSON and Markdown).
Authors are ranked by commits (or churn with `--sort churn`); ties are broken
by the other metric and then by name.

**Identity.** Authors are grouped by name, like `git shortlog`, so commits
made from several e-mail addresses under one name belong to one author.
A `.mailmap` file in the repository root is applied first to merge aliases
(different spellings, old addresses, CI identities):

```
Alice Smith <alice@work.com> <alice@home.org>
Alice Smith <alice@work.com> asmith <ci@build.local>
```

All four Git `.mailmap` line formats are supported; matching ignores case.
The `mailmap.file` / `mailmap.blob` Git settings are not read.

**Privacy.** E-mail addresses are personal data, so they are **not shown**
in tables or reports unless you pass `--show-emails` (JSON then gets an
`emails` field per author). Names are always shown - keep that in mind
before sharing a report. The contents of `.mailmap` are never printed.

**Interpretation.** Commits and churn measure *activity*, not productivity
or the value of anyone's work: a single careful fix can matter more than a
thousand generated lines. Day and month buckets use UTC. Exclude patterns
remove files, not commits, so they change an author's churn and files but
not their commit count.

## File coupling

```bash
git-detective coupling . --limit 500 --top 20
git-detective coupling . --min-cochanges 5 --max-files-per-commit 30 -e "*.lock"
```

Lists pairs of files that were changed in the same commits:

| Metric        | Meaning                                                                    |
| ------------- | -------------------------------------------------------------------------- |
| `cochanges`   | commits that changed both files                                            |
| `commits_a/b` | commits that changed each file                                             |
| `jaccard`     | `cochanges / (commits_a + commits_b - cochanges)`; 1.0 = always together   |

Pairs are ranked by Jaccard similarity, then by co-changes, then by path.

**Noise control.** Large commits (mass refactors, reformatting, dependency
bumps, vendored code) would make every file look coupled to every other one.
Git Detective therefore:

1. **skips commits with more than `--max-files-per-commit` files entirely** -
   they count neither towards pairs nor towards per-file commit counts, so
   Jaccard is always computed over one consistent set of commits (the number
   of skipped commits is shown and reported);
2. drops pairs changed together fewer than `--min-cochanges` times, where a
   high Jaccard would be a coincidence;
3. applies your exclude patterns first, so lockfiles and generated files can
   be removed (which can also bring a large commit under the size limit).

The size limit also keeps the computation fast: a commit with *n* files
yields *n·(n-1)/2* pairs, so the work is bounded by
`limit × max_files_per_commit² / 2`.

**Interpretation.** Co-change does **not** prove that two modules depend on
each other. A strong pair is a hint worth a closer look: a hidden
dependency, duplicated logic, a missing abstraction - or simply code and its
tests, which is expected.

The JSON report also contains a `graph` with `nodes` (files, with commit
counts) and `edges` (pairs, with co-changes and Jaccard) for the reported
pairs, ready to be drawn by a graph library.

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

`.gitdetectiveignore` and `.mailmap` must be regular files of at most 1 MiB.
A symlink is refused with an error, because its target could be a private file
whose contents would end up in a report.

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
| `report`           | `scan`, `hotspots`, `authors` or `coupling`                                   |
| `generated_at`     | When the report was created (ISO 8601 with UTC offset)                        |
| `parameters`       | Options used for the run, incl. effective exclude patterns                    |
| `summary.period`   | Author dates of the oldest and newest analyzed commit (`null` if none)        |
| `files`            | Top files; `scan` has `added`/`deleted`, `hotspots` has `score`, `components` |
| `files.components` | Normalized metrics F, C, R (0-1) behind the score                             |

All timestamps are ISO 8601 with their original UTC offset. Scores are
rounded to 2 decimals, components to 4.

The `authors` report shares the envelope (`schema_version` … `summary`) and
lists contributors under `authors` instead of `files`:

```json
{
  "rank": 1,
  "name": "Alice Smith",
  "commits": 42,
  "churn": 3120,
  "added": 2400,
  "deleted": 720,
  "files_touched": 37,
  "active_days": 18,
  "first_commit": "2026-06-02T09:15:00+02:00",
  "last_commit": "2026-11-03T17:40:00+01:00",
  "top_files": [{ "path": "src/app/main.py", "commits": 12, "churn": 640 }],
  "activity_by_month": { "2026-06": 5, "2026-07": 9 }
}
```

Its summary has `authors_total` / `authors_reported`, and `parameters`
records `sort`, `show_emails` and whether `.mailmap` was used. The `emails`
list appears only with `--show-emails`.

The `coupling` report lists `pairs` and a `graph` built from them:

```json
{
  "summary": {
    "commits_analyzed": 500,
    "commits_considered": 488,
    "commits_skipped": 12,
    "pairs_total": 143,
    "pairs_reported": 20,
    "period": { "from": "2026-03-01T10:00:00+01:00", "to": "2026-11-03T18:40:11+01:00" }
  },
  "pairs": [
    {
      "rank": 1,
      "file_a": "src/api.py",
      "file_b": "src/client.py",
      "cochanges": 9,
      "commits_a": 12,
      "commits_b": 10,
      "jaccard": 0.6923
    }
  ],
  "graph": {
    "nodes": [
      { "id": "src/api.py", "commits": 12 },
      { "id": "src/client.py", "commits": 10 }
    ],
    "edges": [
      { "source": "src/api.py", "target": "src/client.py", "cochanges": 9, "jaccard": 0.6923 }
    ]
  }
}
```

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

## REST API

The API runs the same analyses as the CLI and stores every report, so past
results stay available even after the repository changes or disappears. It is
meant for your own machine: **it has no authentication** and listens on
`127.0.0.1` by default.

### Running the server

```bash
pip install -e ".[api]"     # or ".[dev]", which includes it
```

List the repositories the API may read in a configuration file (start from
[`git-detective-api.example.toml`](git-detective-api.example.toml)):

```toml
# git-detective-api.toml
[repositories]
my-project = "~/code/my-project"
```

```bash
git-detective serve --config git-detective-api.toml
```

Swagger UI is then available at <http://127.0.0.1:8000/docs>, and the OpenAPI
schema at `/openapi.json`. The `/docs` page loads its scripts from a CDN, so it
needs internet access; the API itself does not.

### Example requests

```bash
API=http://127.0.0.1:8000/api/v1

curl "$API/health"
curl "$API/repositories"

# Run an analysis and keep its id. The report is complete when this returns.
curl -X POST "$API/analyses" -H "Content-Type: application/json" -d '{
  "repository": "my-project",
  "type": "hotspots",
  "parameters": {"limit": 500, "top": 20, "recent_days": 90, "exclude": ["*.lock"]}
}'

curl -X POST "$API/analyses" -H "Content-Type: application/json" \
  -d '{"repository": "my-project", "type": "coupling",
       "parameters": {"min_cochanges": 5, "max_files_per_commit": 30}}'

# Browse the history, newest first
curl "$API/analyses?repository=my-project&type=hotspots&limit=10&offset=0"

# Read a stored report; Git is not involved
curl "$API/analyses/$ID"
curl "$API/analyses/$ID/hotspots"

curl -X DELETE "$API/analyses/$ID"
```

### Endpoints

| Endpoint                          | Result                                       |
| --------------------------------- | -------------------------------------------- |
| `GET /api/v1/health`              | Server and database status                   |
| `GET /api/v1/repositories`        | Allowed repository ids and their availability |
| `POST /api/v1/analyses`           | `201`: runs an analysis and stores the report |
| `GET /api/v1/analyses`            | Stored analyses, without reports             |
| `GET /api/v1/analyses/{id}`       | One analysis with its full report            |
| `GET /api/v1/analyses/{id}/hotspots` | Ranked files of a `hotspots` analysis     |
| `GET /api/v1/analyses/{id}/authors`  | Contributors of an `authors` analysis     |
| `GET /api/v1/analyses/{id}/coupling` | Pairs and graph of a `coupling` analysis  |
| `DELETE /api/v1/analyses/{id}`    | `204`: deletes the stored analysis           |

Parameters of `POST /api/v1/analyses` (all optional, unknown fields are rejected):

| Parameter              | Analyses | Default                    | Range                  |
| ---------------------- | -------- | -------------------------- | ---------------------- |
| `limit`                | all      | 100 (hotspots), 500 (others) | 1-5000               |
| `top`                  | all      | 10, 20 (coupling)          | 1-1000                 |
| `exclude`              | all      | none                       | up to 100 patterns of up to 256 characters |
| `recent_days`          | hotspots | 30                         | 1-3650                 |
| `sort`                 | authors  | `commits`                  | `commits`, `churn`     |
| `min_cochanges`        | coupling | 3                          | 1-10000                |
| `max_files_per_commit` | coupling | 50                         | 2-200                  |

Every error has the same shape:

```json
{"error": {"code": "analysis_not_found", "message": "There is no analysis with this id.", "details": []}}
```

| Status | `code`                    | Meaning                                         |
| ------ | ------------------------- | ----------------------------------------------- |
| 400    | (plain text)              | The `Host` header is not an allowed host        |
| 404    | `repository_not_found`    | The repository id is not on the allowlist       |
| 404    | `analysis_not_found`, `not_found` | No such analysis or route               |
| 405    | `method_not_allowed`      | The route does not support this method          |
| 409    | `repository_unavailable`  | The repository cannot be read (details in the server log) |
| 409    | `analysis_type_mismatch`  | For example `/hotspots` of a `coupling` analysis |
| 422    | `validation_error`        | Invalid request; `details` lists each problem   |
| 422    | `analysis_too_large`      | The report exceeds 5 MiB; lower `top` or `limit` |
| 429    | `analysis_busy`           | All analysis slots are in use; see `Retry-After` |
| 500    | `internal_error`          | Unexpected failure                              |
| 503    | `storage_unavailable`     | The database cannot be used                     |

### Long-running requests

An analysis runs **inside the `POST` request**; there is no job queue. Reading
history starts one Git process per commit, about 12 ms each on a small
repository, so 5000 commits take on the order of a minute or more. Give your
HTTP client a long enough timeout. Other requests are still served meanwhile.

At most `max_concurrent_analyses` (default 2) run at once, and further requests
get `429` right away. Disconnecting does not stop a running analysis.

### Report storage

Reports are kept in a SQLite database, by default in
`$XDG_DATA_HOME/git-detective/analyses.db` (`~/.local/share/git-detective/analyses.db`
if the variable is not set); change this with `database` in the configuration
file. The file is readable only by its owner.

Each row holds the analysis id (UUID), repository id, analysis type, parameters,
execution time, the HEAD hash at that moment, the number of analyzed commits and
the report as JSON. The hash is read once and the analysis walks from it, so it
always describes the stored report, even if someone commits in the meantime.
The schema version is kept in `PRAGMA user_version`, and migrations run
automatically at startup. A database created by a newer version is refused.

### Security

- **Allowlist only.** Requests name a repository id from the configuration file.
  The API never accepts a path, and has no endpoint that runs commands.
- **Paths are checked on every request.** A repository's real location is pinned
  at startup; if a symlink or directory later resolves elsewhere, the repository
  becomes unavailable. A real `.git` directory is required, so **worktrees,
  submodules and bare repositories are not supported** by the API.
- **Repository contents are untrusted.** Hooks, external diff drivers, textconv
  filters and `core.fsmonitor` never run, and neither does a partial clone's
  download of missing objects (such a repository gets `409`). `.gitdetectiveignore`
  and `.mailmap` must be regular files of at most 1 MiB.
- **No e-mail addresses.** The API cannot return them, even on request, so they
  are never stored either. Use `git-detective authors --show-emails` locally.
  Author names are stored as written in commits, and a few people use an e-mail
  address as their name.
- **No absolute paths.** Reports name the repository by id, and error messages
  are fixed texts; details go only to the server log.
- **Local by default.** `serve` refuses a non-loopback address without
  `--allow-remote`, and always refuses `0.0.0.0` and IPv6. Requests are accepted
  only with a `Host` header naming a loopback host or the `--host` address, which
  stops DNS rebinding. No CORS headers are sent, so web pages cannot read
  responses or send `DELETE`, and a `POST` needs a JSON content type.

Known limitations:

- Any process on your machine can use the API, including deleting reports.
- There is no time limit on an analysis. The commit limit bounds it, but a
  repository with enormous commits can still take long and use a lot of memory.
- Blocking partial clone downloads relies on `GIT_NO_LAZY_FETCH`, which older
  Git versions ignore. Keep Git up to date if you allowlist partial clones.

## Project structure

```
src/git_detective/
├── cli.py              # Typer commands (presentation layer)
├── tables.py           # Rich tables for terminal output
├── service.py          # Runs analyses; shared by the CLI and the API
├── reporting.py        # JSON / Markdown reports (no Git or CLI code)
├── storage.py          # SQLite analysis history (no FastAPI code)
├── exceptions.py       # Project-specific errors
├── api/                # REST API (needs the "api" extra)
│   ├── app.py          # Application factory and middleware
│   ├── config.py       # TOML configuration
│   ├── registry.py     # Repository allowlist, path and symlink checks
│   ├── runner.py       # Runs one analysis within limits and stores it
│   ├── routes.py       # Endpoints
│   ├── schemas.py      # Request and response models, limits
│   └── errors.py       # One error format for every failure
├── core/
│   ├── git_reader.py   # Reads commits and per-file line changes from Git
│   ├── safe_files.py   # Safe reading of files stored in a repository
│   ├── filters.py      # --exclude / .gitdetectiveignore path filtering
│   ├── analyzer.py     # Aggregates commits into per-file statistics
│   ├── hotspots.py     # Hotspot Score: normalization, weights, ranking
│   ├── mailmap.py      # .mailmap parsing and alias resolution
│   ├── authors.py      # Contributor statistics
│   └── coupling.py     # Co-change pairs and Jaccard similarity
└── models/
    ├── commit.py       # CommitInfo, FileChange
    ├── file_stats.py   # FileStats
    ├── hotspot.py      # Hotspot
    ├── author.py       # AuthorStats
    └── coupling.py     # FileCoupling, CouplingResult
```

## Development

```bash
pytest                  # run the test suite
pytest tests/test_hotspots.py -k weights   # run a subset
ruff check .            # lint
ruff format .           # format (Python files and Python blocks in Markdown)
ruff format --check .   # verify formatting without changing files
```

Tests create temporary Git repositories and databases, and run with a temporary
home directory, so they never touch your own repositories or analysis history.

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

- Time filters such as `--since` / `--until`
- Combining hotspots with code complexity
- Background workers for long analyses in the API

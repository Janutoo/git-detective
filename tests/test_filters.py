"""Tests for path filtering. No Git repository is needed here."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from git_detective.core.filters import PathFilter, filter_commits, read_ignore_file
from git_detective.core.hotspots import compute_hotspots
from git_detective.exceptions import IgnorePatternError
from git_detective.models import CommitInfo, FileChange

REFERENCE = datetime(2024, 6, 30, tzinfo=UTC)


def commit(*changes: tuple[str, int, int], sha: str = "a") -> CommitInfo:
    return CommitInfo(
        hexsha=sha * 40,
        author_name="Alice",
        author_email="alice@example.com",
        authored_at=REFERENCE - timedelta(days=1),
        message="msg",
        files=tuple(
            FileChange(path, added, deleted) for path, added, deleted in changes
        ),
    )


def excluded(patterns: list[str], paths: list[str]) -> list[str]:
    path_filter = PathFilter(patterns)
    return [path for path in paths if path_filter.is_excluded(path)]


class TestPatternSemantics:
    def test_extension_matches_at_any_depth(self) -> None:
        paths = ["poetry.lock", "web/yarn.lock", "a/b/c/Cargo.lock", "lock.py"]

        assert excluded(["*.lock"], paths) == paths[:3]

    def test_pattern_with_slash_is_anchored_to_root(self) -> None:
        paths = ["dist/app.js", "dist/css/site.css", "src/dist/app.js", "distro.py"]

        assert excluded(["dist/**"], paths) == ["dist/app.js", "dist/css/site.css"]

    def test_leading_slash_anchors_directory(self) -> None:
        paths = ["build/out.o", "src/build/out.o"]

        assert excluded(["/build/"], paths) == ["build/out.o"]

    def test_directory_name_matches_at_any_depth(self) -> None:
        paths = ["node_modules/x.js", "web/node_modules/pkg/index.js", "src/modules.js"]

        assert excluded(["node_modules/"], paths) == paths[:2]

    def test_single_star_does_not_cross_slash(self) -> None:
        paths = ["src/a.py", "src/deep/a.py"]

        assert excluded(["src/*.py"], paths) == ["src/a.py"]

    def test_double_star_crosses_directories(self) -> None:
        paths = ["src/a.py", "src/deep/er/a.py", "tests/a.py"]

        assert excluded(["src/**/*.py"], paths) == ["src/a.py", "src/deep/er/a.py"]

    def test_multiple_patterns(self) -> None:
        paths = ["package-lock.json", "dist/app.js", "docs/a.min.js", "src/app.py"]

        assert excluded(["package-lock.json", "dist/", "*.min.js"], paths) == paths[:3]

    def test_negation_reincludes_path(self) -> None:
        paths = ["a.lock", "keep.lock"]

        assert excluded(["*.lock", "!keep.lock"], paths) == ["a.lock"]

    def test_empty_filter_excludes_nothing(self) -> None:
        assert excluded([], ["a.py", "dist/x.js"]) == []

    def test_bool_reflects_patterns(self) -> None:
        assert not PathFilter()
        assert PathFilter(["*.lock"])
        assert PathFilter(["*.lock", "dist/"]).patterns == ("*.lock", "dist/")

    def test_invalid_pattern_raises(self) -> None:
        with pytest.raises(IgnorePatternError, match="Invalid exclude pattern"):
            PathFilter(["foo\\"])


class TestReadIgnoreFile:
    def test_missing_file_gives_no_patterns(self, tmp_path: Path) -> None:
        assert read_ignore_file(tmp_path / ".gitdetectiveignore") == []

    def test_skips_comments_and_blank_lines(self, tmp_path: Path) -> None:
        ignore_file = tmp_path / ".gitdetectiveignore"
        ignore_file.write_text(
            "# lockfiles\n*.lock\n\n   \ndist/**\n\\#literal\n", encoding="utf-8"
        )

        assert read_ignore_file(ignore_file) == ["*.lock", "dist/**", "\\#literal"]

    def test_unreadable_file_raises(self, tmp_path: Path) -> None:
        ignore_file = tmp_path / ".gitdetectiveignore"
        ignore_file.write_bytes(b"\xff\xfe\xfa")

        with pytest.raises(IgnorePatternError, match="Cannot read ignore file"):
            read_ignore_file(ignore_file)


class TestFilterCommits:
    def test_removes_excluded_files_and_keeps_commits(self) -> None:
        commits = [
            commit(("src/app.py", 1, 0), ("poetry.lock", 500, 20), sha="a"),
            commit(("poetry.lock", 10, 10), sha="b"),
        ]

        result = filter_commits(commits, PathFilter(["*.lock"]))

        assert len(result) == 2
        assert result[0].files == (FileChange("src/app.py", 1, 0),)
        assert result[1].files == ()
        assert result[0].hexsha == commits[0].hexsha
        assert result[0].authored_at == commits[0].authored_at

    def test_does_not_mutate_input(self) -> None:
        original = commit(("a.lock", 1, 0), ("a.py", 1, 0))

        filter_commits([original], PathFilter(["*.lock"]))

        assert len(original.files) == 2

    def test_empty_filter_returns_same_commits(self) -> None:
        commits = [commit(("a.py", 1, 0))]

        assert filter_commits(commits, PathFilter()) == commits


class TestFilteringChangesNormalization:
    def test_excluding_outlier_raises_other_scores(self) -> None:
        commits = [
            commit(("package-lock.json", 20_000, 5_000), ("src/app.py", 80, 20)),
            commit(("src/app.py", 10, 0), ("src/util.py", 5, 0), sha="b"),
        ]

        before = {
            h.path: h for h in compute_hotspots(commits, reference_time=REFERENCE)
        }
        filtered = filter_commits(commits, PathFilter(["package-lock.json"]))
        after = {
            h.path: h for h in compute_hotspots(filtered, reference_time=REFERENCE)
        }

        assert "package-lock.json" not in after
        # The lockfile no longer defines the churn maximum ...
        assert before["src/app.py"].churn_norm < 1.0
        assert after["src/app.py"].churn_norm == 1.0
        # ... so remaining files score higher.
        assert after["src/app.py"].score > before["src/app.py"].score
        assert after["src/util.py"].score > before["src/util.py"].score

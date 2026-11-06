"""Files inside a repository are untrusted: see core/safe_files.py."""

import os
from pathlib import Path

import pytest

from git_detective.core import read_ignore_file, read_mailmap
from git_detective.core.safe_files import read_repo_text
from git_detective.exceptions import IgnorePatternError, MailmapError


class TestReadRepoText:
    def test_reads_utf8_text(self, tmp_path: Path) -> None:
        path = tmp_path / "file"
        path.write_text("zażółć\n", encoding="utf-8")

        assert read_repo_text(path) == "zażółć\n"

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_repo_text(tmp_path / "missing")

    def test_refuses_symlink_to_file(self, tmp_path: Path) -> None:
        secret = tmp_path / "secret"
        secret.write_text("private", encoding="utf-8")
        link = tmp_path / "link"
        link.symlink_to(secret)

        with pytest.raises(OSError):
            read_repo_text(link)

    def test_refuses_dangling_symlink_instead_of_treating_it_as_missing(
        self, tmp_path: Path
    ) -> None:
        link = tmp_path / "link"
        link.symlink_to(tmp_path / "nowhere")

        with pytest.raises(OSError) as excinfo:
            read_repo_text(link)
        assert not isinstance(excinfo.value, FileNotFoundError)

    def test_refuses_fifo_without_blocking(self, tmp_path: Path) -> None:
        fifo = tmp_path / "fifo"
        os.mkfifo(fifo)

        with pytest.raises(OSError, match="Not a regular file"):
            read_repo_text(fifo)

    def test_refuses_directory(self, tmp_path: Path) -> None:
        with pytest.raises(OSError, match="Not a regular file"):
            read_repo_text(tmp_path)

    def test_refuses_device(self) -> None:
        with pytest.raises(OSError, match="Not a regular file"):
            read_repo_text(Path("/dev/zero"))

    def test_size_limit_is_inclusive(self, tmp_path: Path) -> None:
        path = tmp_path / "file"
        path.write_bytes(b"x" * 10)

        assert read_repo_text(path, max_bytes=10) == "x" * 10
        with pytest.raises(OSError, match="too large"):
            read_repo_text(path, max_bytes=9)

    def test_invalid_utf8(self, tmp_path: Path) -> None:
        path = tmp_path / "file"
        path.write_bytes(b"\xff\xfe")

        with pytest.raises(UnicodeDecodeError):
            read_repo_text(path)


class TestCallersRefuseSymlinks:
    def test_ignore_file(self, tmp_path: Path) -> None:
        secret = tmp_path / "secret"
        secret.write_text("private-key-material\n", encoding="utf-8")
        link = tmp_path / ".gitdetectiveignore"
        link.symlink_to(secret)

        with pytest.raises(IgnorePatternError, match="Cannot read ignore file"):
            read_ignore_file(link)

    def test_mailmap(self, tmp_path: Path) -> None:
        secret = tmp_path / "secret"
        secret.write_text("Name <a@b.c>\n", encoding="utf-8")
        link = tmp_path / ".mailmap"
        link.symlink_to(secret)

        with pytest.raises(MailmapError, match="Cannot read mailmap"):
            read_mailmap(link)

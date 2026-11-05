"""Tests for .mailmap parsing and alias resolution."""

from pathlib import Path

import pytest

from git_detective.core.mailmap import Mailmap, read_mailmap
from git_detective.exceptions import MailmapError


class TestLineFormats:
    def test_proper_name_for_email(self) -> None:
        mailmap = Mailmap.parse("Alice Smith <alice@old.com>\n")

        assert mailmap.resolve("alice", "alice@old.com") == (
            "Alice Smith",
            "alice@old.com",
        )

    def test_proper_email_for_email(self) -> None:
        mailmap = Mailmap.parse("<alice@new.com> <alice@old.com>\n")

        assert mailmap.resolve("Alice", "alice@old.com") == ("Alice", "alice@new.com")

    def test_proper_name_and_email_for_email(self) -> None:
        mailmap = Mailmap.parse("Alice Smith <alice@new.com> <alice@old.com>\n")

        assert mailmap.resolve("al", "alice@old.com") == (
            "Alice Smith",
            "alice@new.com",
        )

    def test_proper_identity_for_name_and_email(self) -> None:
        mailmap = Mailmap.parse("Alice Smith <alice@new.com> al <shared@ci.com>\n")

        assert mailmap.resolve("al", "shared@ci.com") == (
            "Alice Smith",
            "alice@new.com",
        )
        # Same e-mail, different name: the entry does not apply.
        assert mailmap.resolve("bob", "shared@ci.com") == ("bob", "shared@ci.com")


class TestMatching:
    def test_email_and_name_are_case_insensitive(self) -> None:
        mailmap = Mailmap.parse("Alice <a@new.com> AL <A@Old.COM>\n")

        assert mailmap.resolve("al", "a@old.com") == ("Alice", "a@new.com")

    def test_name_specific_entry_wins_over_email_entry(self) -> None:
        mailmap = Mailmap.parse(
            "Generic Name <shared@ci.com>\nAlice <alice@x.com> alice <shared@ci.com>\n"
        )

        assert mailmap.resolve("alice", "shared@ci.com") == ("Alice", "alice@x.com")
        assert mailmap.resolve("bot", "shared@ci.com") == (
            "Generic Name",
            "shared@ci.com",
        )

    def test_later_lines_complete_earlier_ones(self) -> None:
        mailmap = Mailmap.parse("Alice <a@old.com>\n<a@new.com> <a@old.com>\n")

        assert mailmap.resolve("x", "a@old.com") == ("Alice", "a@new.com")

    def test_unknown_author_is_unchanged(self) -> None:
        mailmap = Mailmap.parse("Alice <a@old.com>\n")

        assert mailmap.resolve("Bob", "bob@x.com") == ("Bob", "bob@x.com")

    def test_empty_mailmap(self) -> None:
        mailmap = Mailmap()

        assert len(mailmap) == 0
        assert not mailmap
        assert mailmap.resolve("Bob", "bob@x.com") == ("Bob", "bob@x.com")


class TestParsing:
    def test_ignores_comments_blank_and_malformed_lines(self) -> None:
        mailmap = Mailmap.parse(
            "# team aliases\n"
            "\n"
            "   \n"
            "just some text\n"
            "<only@email.com>\n"
            "Alice <a@old.com>  # trailing text is ignored\n"
        )

        assert len(mailmap) == 1
        assert mailmap.resolve("x", "a@old.com") == ("Alice", "a@old.com")

    def test_extra_whitespace_is_trimmed(self) -> None:
        mailmap = Mailmap.parse("   Alice   Smith   <  a@new.com >   < a@old.com >\n")

        assert mailmap.resolve("x", "a@old.com") == ("Alice   Smith", "a@new.com")


class TestReadMailmap:
    def test_missing_file_gives_empty_mailmap(self, tmp_path: Path) -> None:
        assert len(read_mailmap(tmp_path / ".mailmap")) == 0

    def test_reads_file(self, tmp_path: Path) -> None:
        path = tmp_path / ".mailmap"
        path.write_text("Zażółć Gęślą <z@new.com> <z@old.com>\n", encoding="utf-8")

        assert read_mailmap(path).resolve("z", "z@old.com") == (
            "Zażółć Gęślą",
            "z@new.com",
        )

    def test_unreadable_file_raises(self, tmp_path: Path) -> None:
        path = tmp_path / ".mailmap"
        path.write_bytes(b"\xff\xfe\xfa")

        with pytest.raises(MailmapError, match="Cannot read mailmap"):
            read_mailmap(path)

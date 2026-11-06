"""Resolve author aliases with a Git ``.mailmap`` file.

Supported line formats (the same as Git's)::

    Proper Name <commit@email>
    <proper@email> <commit@email>
    Proper Name <proper@email> <commit@email>
    Proper Name <proper@email> Commit Name <commit@email>

Matching ignores letter case for both e-mail and name. An entry that also
names the commit author wins over an e-mail-only entry. Lines starting with
``#``, blank lines and malformed lines are ignored, as Git does.

Only ``.mailmap`` in the repository root is read; the ``mailmap.file`` and
``mailmap.blob`` Git settings are not supported. The file is parsed as plain
text - nothing from it is ever executed or printed.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from git_detective.core.safe_files import read_repo_text
from git_detective.exceptions import MailmapError

MAILMAP_FILE_NAME = ".mailmap"

# "<name1> <email1> [<name2> <email2>]"; anything after the second email is ignored.
_ENTRY = re.compile(r"^([^<]*)<([^>]*)>(?:([^<]*)<([^>]*)>)?")


@dataclass(slots=True)
class _Replacement:
    name: str | None = None
    email: str | None = None


class Mailmap:
    """Maps the author identities recorded in commits to canonical ones."""

    def __init__(self) -> None:
        self._by_email: dict[str, _Replacement] = {}
        self._by_name_and_email: dict[tuple[str, str], _Replacement] = {}

    @classmethod
    def parse(cls, text: str) -> Self:
        """Build a mailmap from the contents of a ``.mailmap`` file."""
        mailmap = cls()
        for line in text.splitlines():
            if line.startswith("#"):
                continue
            match = _ENTRY.match(line.strip())
            if match:
                mailmap._add_line(*match.groups())
        return mailmap

    def resolve(self, name: str, email: str) -> tuple[str, str]:
        """Return the canonical ``(name, email)`` for a commit author."""
        key = email.casefold()
        replacement = self._by_name_and_email.get(
            (name.casefold(), key)
        ) or self._by_email.get(key)
        if replacement is None:
            return name, email
        return replacement.name or name, replacement.email or email

    def __len__(self) -> int:
        return len(self._by_email) + len(self._by_name_and_email)

    def _add_line(
        self,
        name1: str,
        email1: str,
        name2: str | None,
        email2: str | None,
    ) -> None:
        if email2 is None:
            # "Proper Name <commit@email>" - only the name is replaced.
            proper_name, proper_email = name1.strip(), None
            commit_name, commit_email = None, email1.strip()
            if not proper_name:
                return
        else:
            proper_name, proper_email = name1.strip() or None, email1.strip() or None
            commit_name, commit_email = (name2 or "").strip() or None, email2.strip()

        if commit_name is None:
            replacement = self._by_email.setdefault(
                commit_email.casefold(), _Replacement()
            )
        else:
            key = (commit_name.casefold(), commit_email.casefold())
            replacement = self._by_name_and_email.setdefault(key, _Replacement())
        # Later lines for the same commit identity fill in or override fields.
        replacement.name = proper_name or replacement.name
        replacement.email = proper_email or replacement.email


def read_mailmap(path: Path) -> Mailmap:
    """Load a ``.mailmap`` file; a missing file yields an empty mailmap.

    Symlinks and other non-regular files are refused.
    """
    try:
        text = read_repo_text(path)
    except FileNotFoundError:
        return Mailmap()
    except (OSError, UnicodeDecodeError) as exc:
        raise MailmapError(f"Cannot read mailmap file {path}: {exc}") from exc
    return Mailmap.parse(text)

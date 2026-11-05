"""Exceptions raised by Git Detective.

All project errors inherit from :class:`GitDetectiveError`, so callers such as
the CLI can handle every expected failure with a single ``except`` clause.
"""


class GitDetectiveError(Exception):
    """Base class for all Git Detective errors."""


class InvalidRepositoryError(GitDetectiveError):
    """The given path does not exist or is not inside a Git repository."""


class GitReadError(GitDetectiveError):
    """Git failed while reading the repository history."""


class IgnorePatternError(GitDetectiveError):
    """An exclude pattern is invalid or the ignore file cannot be read."""


class MailmapError(GitDetectiveError):
    """The ``.mailmap`` file cannot be read."""

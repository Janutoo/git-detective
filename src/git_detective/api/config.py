"""Configuration of the API server, loaded from a TOML file.

Example::

    database = "~/.local/share/git-detective/analyses.db"  # optional
    max_concurrent_analyses = 2                            # optional

    [repositories]
    my-project = "/absolute/path/to/my-project"

The ``repositories`` table is the allowlist: the API can only read repositories
named here, and clients refer to them by id, never by path.
"""

import os
import tomllib
from pathlib import Path
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
)

from git_detective.exceptions import ApiConfigError

REPOSITORY_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"

RepositoryId = Annotated[str, StringConstraints(pattern=REPOSITORY_ID_PATTERN)]


def default_database_path() -> Path:
    """The XDG data directory, so that the database is never inside a repository."""
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "git-detective" / "analyses.db"


def _absolute_path(path: Path) -> Path:
    """Expand ``~`` and refuse relative paths, which depend on the working directory."""
    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise ValueError(f"Path must be absolute: {path}")
    return expanded


AbsolutePath = Annotated[Path, AfterValidator(_absolute_path)]


class ApiSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repositories: dict[RepositoryId, AbsolutePath] = Field(min_length=1)
    database: AbsolutePath = Field(default_factory=default_database_path)
    max_concurrent_analyses: int = Field(default=2, ge=1, le=16)


def load_settings(path: Path) -> ApiSettings:
    """Read and validate a configuration file.

    Raises:
        ApiConfigError: If the file cannot be read or its contents are invalid.
    """
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ApiConfigError(f"Cannot read config file {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ApiConfigError(f"Invalid TOML in config file {path}: {exc}") from exc

    try:
        return ApiSettings.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise ApiConfigError(f"Invalid config file {path}: {problems}") from exc

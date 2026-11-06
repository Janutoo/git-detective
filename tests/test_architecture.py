"""Layer boundaries, checked in a fresh interpreter.

The CLI must work without the ``api`` extra, and the service must stay usable
from anything that is not FastAPI or SQLite.
"""

import subprocess
import sys

import pytest


def modules_loaded_by(module: str) -> set[str]:
    code = (
        f"import sys, {module}\n"
        "print('\\n'.join(name.partition('.')[0] for name in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    return set(result.stdout.split())


@pytest.mark.parametrize(
    ("module", "forbidden"),
    [
        ("git_detective.service", {"fastapi", "starlette", "pydantic", "sqlite3"}),
        ("git_detective.core", {"fastapi", "starlette", "pydantic", "sqlite3"}),
        (
            "git_detective.cli",
            {"fastapi", "starlette", "pydantic", "uvicorn", "sqlite3"},
        ),
        ("git_detective.storage", {"fastapi", "starlette", "pydantic"}),
    ],
)
def test_module_does_not_load(module: str, forbidden: set[str]) -> None:
    assert not modules_loaded_by(module) & forbidden

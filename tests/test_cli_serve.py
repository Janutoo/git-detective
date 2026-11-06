"""The ``serve`` command: safe defaults for where the API listens."""

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from git_detective.cli import app


@pytest.fixture
def config_file(tmp_path: Path, repo_path: Path) -> Path:
    path = tmp_path / "api.toml"
    database = tmp_path / "data" / "analyses.db"
    path.write_text(
        f'database = "{database}"\n[repositories]\ndemo = "{repo_path}"\n',
        encoding="utf-8",
    )
    return path


@pytest.fixture
def uvicorn_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record how the server would start, without opening a socket."""
    calls: list[dict[str, Any]] = []

    def fake_run(application: Any, **kwargs: Any) -> None:
        calls.append({"app": application, **kwargs})

    monkeypatch.setattr("uvicorn.run", fake_run)
    return calls


def serve(*args: str):
    return CliRunner().invoke(app, ["serve", *args])


class TestDefaults:
    def test_listens_on_loopback_only(
        self, config_file: Path, uvicorn_calls: list[dict[str, Any]]
    ) -> None:
        result = serve("--config", str(config_file))

        assert result.exit_code == 0, result.output
        assert uvicorn_calls[0]["host"] == "127.0.0.1"
        assert uvicorn_calls[0]["port"] == 8000

    def test_config_is_required(self, uvicorn_calls: list[dict[str, Any]]) -> None:
        result = serve()

        assert result.exit_code == 2
        assert uvicorn_calls == []

    def test_config_from_environment(
        self,
        config_file: Path,
        uvicorn_calls: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GIT_DETECTIVE_CONFIG", str(config_file))

        assert serve().exit_code == 0
        assert len(uvicorn_calls) == 1

    def test_custom_port(
        self, config_file: Path, uvicorn_calls: list[dict[str, Any]]
    ) -> None:
        serve("--config", str(config_file), "--port", "9000")

        assert uvicorn_calls[0]["port"] == 9000

    def test_started_app_works(
        self, config_file: Path, uvicorn_calls: list[dict[str, Any]]
    ) -> None:
        serve("--config", str(config_file))
        client = TestClient(uvicorn_calls[0]["app"], base_url="http://127.0.0.1")

        assert client.get("/api/v1/repositories").json()["total"] == 1


class TestBindAddress:
    @pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "localhost"])
    def test_loopback_needs_no_flag(
        self, config_file: Path, uvicorn_calls: list[dict[str, Any]], host: str
    ) -> None:
        result = serve("--config", str(config_file), "--host", host)

        assert result.exit_code == 0, result.output
        assert uvicorn_calls[0]["host"] == host

    @pytest.mark.parametrize("host", ["192.168.1.5", "8.8.8.8", "myhost.local"])
    def test_other_addresses_need_the_flag(
        self, config_file: Path, uvicorn_calls: list[dict[str, Any]], host: str
    ) -> None:
        result = serve("--config", str(config_file), "--host", host)

        assert result.exit_code == 1
        assert "--allow-remote" in result.output
        assert uvicorn_calls == []

    @pytest.mark.parametrize("allow", [[], ["--allow-remote"]])
    @pytest.mark.parametrize("host", ["0.0.0.0", "::", "::1", "2001:db8::1", "", " "])
    def test_never_allowed(
        self,
        config_file: Path,
        uvicorn_calls: list[dict[str, Any]],
        host: str,
        allow: list[str],
    ) -> None:
        result = serve("--config", str(config_file), "--host", host, *allow)

        assert result.exit_code == 1
        assert uvicorn_calls == []

    def test_flag_allows_a_specific_address_and_warns(
        self, config_file: Path, uvicorn_calls: list[dict[str, Any]]
    ) -> None:
        result = serve(
            "--config", str(config_file), "--host", "192.168.1.5", "--allow-remote"
        )

        assert result.exit_code == 0, result.output
        assert "without authentication" in " ".join(result.output.split())

    def test_flag_adds_only_that_host_to_the_allowed_hosts(
        self, config_file: Path, uvicorn_calls: list[dict[str, Any]]
    ) -> None:
        serve("--config", str(config_file), "--host", "192.168.1.5", "--allow-remote")
        client = TestClient(uvicorn_calls[0]["app"], base_url="http://192.168.1.5")

        assert client.get("/api/v1/health").status_code == 200
        other = client.get("/api/v1/health", headers={"Host": "evil.example.com"})
        assert other.status_code == 400


class TestFailures:
    def test_invalid_config(
        self, tmp_path: Path, uvicorn_calls: list[dict[str, Any]]
    ) -> None:
        path = tmp_path / "bad.toml"
        path.write_text("[repositories]\n", encoding="utf-8")

        result = serve("--config", str(path))

        assert result.exit_code == 1
        assert "Invalid config" in " ".join(result.output.split())
        assert uvicorn_calls == []

    def test_missing_config_file(
        self, tmp_path: Path, uvicorn_calls: list[dict[str, Any]]
    ) -> None:
        result = serve("--config", str(tmp_path / "missing.toml"))

        assert result.exit_code == 1
        assert uvicorn_calls == []

    def test_unusable_database(
        self,
        tmp_path: Path,
        repo_path: Path,
        uvicorn_calls: list[dict[str, Any]],
    ) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("", encoding="utf-8")
        path = tmp_path / "api.toml"
        path.write_text(
            f'database = "{blocker}/a.db"\n[repositories]\ndemo = "{repo_path}"\n',
            encoding="utf-8",
        )

        result = serve("--config", str(path))

        assert result.exit_code == 1
        assert uvicorn_calls == []

    def test_without_the_api_extra(
        self, config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "uvicorn", None)

        result = serve("--config", str(config_file))

        assert result.exit_code == 1
        assert "git-detective[api]" in result.output

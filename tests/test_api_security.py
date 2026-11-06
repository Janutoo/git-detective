"""What the API must never do, whatever the repository or the client tries."""

import os
import shutil
import sqlite3
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from git import Repo
from typer.testing import CliRunner

from git_detective.api.app import create_app
from git_detective.api.config import ApiSettings, default_database_path
from git_detective.cli import app as cli_app

ANALYSES = "/api/v1/analyses"
TYPES = ["hotspots", "authors", "coupling"]


def create(client: TestClient, analysis_type: str = "authors"):
    return client.post(ANALYSES, json={"repository": "demo", "type": analysis_type})


@pytest.mark.usefixtures("seeded_repo")
class TestPrivacy:
    @pytest.fixture
    def analyses(self, client: TestClient, repo_path: Path) -> dict[str, dict]:
        (repo_path / ".mailmap").write_text(
            "Robert Builder <robert@secret.example.com> <bob@secret.example.com>\n",
            encoding="utf-8",
        )
        results = {}
        for analysis_type in TYPES:
            response = create(client, analysis_type)
            assert response.status_code == 201
            results[analysis_type] = response.json()
        return results

    def test_no_response_contains_emails_or_absolute_paths(
        self,
        client: TestClient,
        analyses: dict[str, dict],
        repo_path: Path,
        tmp_path: Path,
    ) -> None:
        urls = ["/api/v1/health", "/api/v1/repositories", ANALYSES]
        for analysis_type, analysis in analyses.items():
            urls.append(f"{ANALYSES}/{analysis['id']}")
            urls.append(f"{ANALYSES}/{analysis['id']}/{analysis_type}")

        for url in urls:
            response = client.get(url)
            assert response.status_code == 200, url
            assert "example.com" not in response.text, url
            assert str(repo_path) not in response.text, url
            assert str(tmp_path) not in response.text, url

    def test_report_names_the_repository_by_id(self, analyses: dict[str, dict]) -> None:
        for analysis in analyses.values():
            assert analysis["report"]["repository"]["path"] == "demo"

    def test_mailmap_names_are_still_applied(self, analyses: dict[str, dict]) -> None:
        names = {row["name"] for row in analyses["authors"]["report"]["authors"]}
        assert "Robert Builder" in names

    def test_database_contains_no_emails_or_absolute_paths(
        self,
        analyses: dict[str, dict],
        database_path: Path,
        repo_path: Path,
    ) -> None:
        connection = sqlite3.connect(database_path)
        rows = connection.execute("SELECT * FROM analyses").fetchall()
        connection.close()

        assert len(rows) == len(analyses)
        raw = repr(rows)
        assert "example.com" not in raw
        assert str(repo_path) not in raw
        assert b"example.com" not in database_path.read_bytes()

    def test_emails_cannot_be_requested(self, client: TestClient) -> None:
        response = client.post(
            ANALYSES,
            json={
                "repository": "demo",
                "type": "authors",
                "parameters": {"show_emails": True},
            },
        )

        assert response.status_code == 422

    def test_stored_parameters_say_emails_are_off(
        self, analyses: dict[str, dict]
    ) -> None:
        assert analyses["authors"]["report"]["parameters"]["show_emails"] is False


@pytest.mark.usefixtures("seeded_repo")
class TestErrorsLeakNothing:
    def test_corrupted_repository(self, client: TestClient, repo_path: Path) -> None:
        shutil.rmtree(repo_path / ".git" / "objects")

        response = create(client)

        assert response.status_code == 409
        assert str(repo_path) not in response.text

    def test_broken_ref(self, client: TestClient, repo: Repo, repo_path: Path) -> None:
        head = Path(repo.git_dir) / repo.head.ref.path
        head.write_text("0" * 40 + "\n", encoding="utf-8")

        response = TestClient(
            client.app, base_url="http://127.0.0.1", raise_server_exceptions=False
        ).post(ANALYSES, json={"repository": "demo", "type": "authors"})

        assert response.status_code == 409, response.text
        assert str(repo_path) not in response.text


@pytest.mark.usefixtures("seeded_repo")
class TestRepositoryFilesAreUntrusted:
    @pytest.fixture
    def secret(self, tmp_path: Path) -> Path:
        path = tmp_path / "id_rsa"
        path.write_text("TOP-SECRET-KEY-MATERIAL\n", encoding="utf-8")
        return path

    @pytest.mark.parametrize("analysis_type", TYPES)
    def test_symlinked_ignore_file_cannot_leak_a_file(
        self,
        client: TestClient,
        repo_path: Path,
        secret: Path,
        analysis_type: str,
    ) -> None:
        (repo_path / ".gitdetectiveignore").symlink_to(secret)

        response = create(client, analysis_type)

        assert response.status_code == 409
        assert "TOP-SECRET" not in response.text
        assert str(secret) not in response.text
        assert client.get(ANALYSES).json()["total"] == 0

    def test_symlinked_mailmap_cannot_leak_a_file(
        self, client: TestClient, repo_path: Path, secret: Path
    ) -> None:
        (repo_path / ".mailmap").symlink_to(secret)

        response = create(client, "authors")

        assert response.status_code == 409
        assert "TOP-SECRET" not in response.text

    def test_fifo_cannot_block_the_server(
        self, client: TestClient, repo_path: Path
    ) -> None:
        os.mkfifo(repo_path / ".gitdetectiveignore")

        assert create(client).status_code == 409

    def test_huge_ignore_file_is_refused(
        self, client: TestClient, repo_path: Path
    ) -> None:
        (repo_path / ".gitdetectiveignore").write_bytes(b"x" * (1024 * 1024 + 1))

        assert create(client).status_code == 409

    def test_regular_ignore_file_still_works(
        self, client: TestClient, repo_path: Path
    ) -> None:
        (repo_path / ".gitdetectiveignore").write_text("*.lock\n", encoding="utf-8")

        response = client.post(
            ANALYSES, json={"repository": "demo", "type": "hotspots"}
        )

        assert response.status_code == 201
        report = response.json()["report"]
        assert report["parameters"]["exclude"] == ["*.lock"]
        assert report["parameters"]["ignore_file"] == ".gitdetectiveignore"


class TestNoCodeFromTheRepositoryRuns:
    @pytest.fixture
    def sentinel(self, tmp_path: Path) -> Path:
        return tmp_path / "code-was-executed"

    @pytest.fixture
    def malicious_repo(
        self,
        repo: Repo,
        repo_path: Path,
        make_commit: Callable[..., str],
        sentinel: Path,
    ) -> Path:
        """A repository configured to run a command at every opportunity.

        Configured after committing, because creating the test commits would
        otherwise trigger the hooks.
        """
        make_commit({".gitattributes": "* diff=evil filter=evil\n", "a.py": "1\n"})
        make_commit({"a.py": "1\n2\n", "b.py": "1\n"})

        command = f"touch '{sentinel}'"
        sentinel_script = repo_path / ".git" / "fire.sh"
        sentinel_script.write_text(f"#!/bin/sh\n{command}\n", encoding="utf-8")
        sentinel_script.chmod(0o755)
        hooks = repo_path / ".git" / "hooks"
        hooks.mkdir(exist_ok=True)
        for name in (
            "pre-commit",
            "post-commit",
            "post-checkout",
            "post-merge",
            "post-rewrite",
            "pre-auto-gc",
            "reference-transaction",
            "fsmonitor-watchman",
        ):
            hook = hooks / name
            hook.write_text(f"#!/bin/sh\n{command}\n", encoding="utf-8")
            hook.chmod(0o755)

        with repo.config_writer() as config:
            config.set_value("core", "fsmonitor", command)
            config.set_value("core", "sshCommand", command)
            config.set_value("core", "pager", command)
            config.set_value("core", "gitProxy", command)
            config.set_value("core", "editor", command)
            config.set_value("core", "askPass", command)
            config.set_value("core", "alternateRefsCommand", command)
            config.set_value("core", "hooksPath", str(hooks))
            config.set_value("credential", "helper", f"!{command}")
            config.set_value("gpg", "program", str(sentinel_script))
            config.set_value("log", "showSignature", "true")
            config.set_value("pager", "diff-tree", command)
            config.set_value("pager", "rev-list", command)
            config.set_value("diff", "external", command)
            config.set_value('diff "evil"', "command", command)
            config.set_value('diff "evil"', "textconv", command)
            config.set_value('filter "evil"', "clean", command)
            config.set_value('filter "evil"', "smudge", command)
            config.set_value('filter "evil"', "process", command)
        return repo_path

    def test_analyses_do_not_trigger_anything(
        self, client: TestClient, malicious_repo: Path, sentinel: Path
    ) -> None:
        for analysis_type in TYPES:
            response = create(client, analysis_type)
            assert response.status_code == 201, response.text

        assert not sentinel.exists()

    @pytest.mark.parametrize("command", ["scan", "hotspots", "authors", "coupling"])
    def test_cli_does_not_trigger_anything_either(
        self, malicious_repo: Path, sentinel: Path, command: str
    ) -> None:
        result = CliRunner().invoke(
            cli_app, [command, str(malicious_repo), "-f", "json"]
        )

        assert result.exit_code == 0, result.output
        assert not sentinel.exists()

    def test_the_sentinel_really_detects_execution(
        self, malicious_repo: Path, sentinel: Path
    ) -> None:
        # Control: a command that does honor the configuration fires the trap,
        # so the test above is not passing because the trap is broken.
        subprocess.run(
            ["git", "status"], cwd=malicious_repo, capture_output=True, check=False
        )

        assert sentinel.exists()


class TestNonUtf8FileNames:
    @pytest.fixture
    def repo_with_binary_name(self, repo: Repo, repo_path: Path) -> str:
        """Commit a file whose name is not valid UTF-8, using Git plumbing only.

        Some file systems refuse such names, but Git history can still have them.
        """

        def git(*args: bytes | str, stdin: bytes | None = None) -> str:
            return (
                subprocess.run(
                    ["git", *args],
                    cwd=repo_path,
                    input=stdin,
                    capture_output=True,
                    check=True,
                    env={
                        **os.environ,
                        "GIT_AUTHOR_NAME": "A",
                        "GIT_AUTHOR_EMAIL": "a@example.com",
                        "GIT_COMMITTER_NAME": "A",
                        "GIT_COMMITTER_EMAIL": "a@example.com",
                    },
                )
                .stdout.decode()
                .strip()
            )

        blob = git("hash-object", "-w", "--stdin", stdin=b"1\n2\n")
        git(
            "update-index",
            "--add",
            "--cacheinfo",
            b"100644," + blob.encode() + b",caf\xe9.txt",
        )
        tree = git("write-tree")
        commit = git("commit-tree", tree, "-m", "binary name")
        git("update-ref", "HEAD", commit)
        return commit

    @pytest.mark.parametrize("analysis_type", ["hotspots", "coupling", "authors"])
    def test_is_stored_and_served(
        self, client: TestClient, repo_with_binary_name: str, analysis_type: str
    ) -> None:
        response = create(client, analysis_type)

        assert response.status_code == 201, response.text
        assert response.json()["head_sha"] == repo_with_binary_name
        analysis_id = response.json()["id"]
        assert client.get(f"{ANALYSES}/{analysis_id}").status_code == 200
        section = client.get(f"{ANALYSES}/{analysis_id}/{analysis_type}")
        assert section.status_code == 200

    def test_name_is_visible_as_an_escape(
        self, client: TestClient, repo_with_binary_name: str
    ) -> None:
        body = create(client, "hotspots").json()

        assert body["report"]["files"][0]["path"] == "caf\\udce9.txt"


@pytest.mark.usefixtures("seeded_repo")
class TestBrowserAttacks:
    """The API has no login, so web pages in the user's browser are the threat."""

    @pytest.mark.parametrize(
        "host", ["evil.example.com", "evil.example.com:8000", "127.0.0.1.evil.com"]
    )
    def test_dns_rebinding_is_stopped_by_the_host_check(
        self, client: TestClient, host: str
    ) -> None:
        response = client.get("/api/v1/repositories", headers={"Host": host})

        assert response.status_code == 400

    @pytest.mark.parametrize(
        "host", ["127.0.0.1", "127.0.0.1:8000", "localhost", "localhost:8000"]
    )
    def test_local_hosts_are_accepted(self, client: TestClient, host: str) -> None:
        response = client.get("/api/v1/health", headers={"Host": host})

        assert response.status_code == 200

    def test_custom_allowed_hosts(self, api_settings: ApiSettings) -> None:
        app = create_app(api_settings, allowed_hosts=["10.0.0.5"])
        client = TestClient(app, base_url="http://10.0.0.5")

        assert client.get("/api/v1/health").status_code == 200
        assert (
            client.get("/api/v1/health", headers={"Host": "localhost"}).status_code
            == 400
        )

    @pytest.mark.parametrize(
        "content_type", ["text/plain", "application/x-www-form-urlencoded", None]
    )
    def test_cross_site_post_without_json_content_type_is_rejected(
        self, client: TestClient, content_type: str | None
    ) -> None:
        # Browsers send these without a CORS preflight.
        headers = {"Content-Type": content_type} if content_type else {}

        response = client.post(
            ANALYSES,
            content=b'{"repository": "demo", "type": "authors"}',
            headers=headers,
        )

        assert response.status_code == 422
        assert client.get(ANALYSES).json()["total"] == 0

    def test_no_cors_headers_are_sent(self, client: TestClient) -> None:
        headers = {"Origin": "https://evil.example.com"}

        response = client.get("/api/v1/repositories", headers=headers)

        assert "access-control-allow-origin" not in response.headers

    @pytest.mark.parametrize("method", ["DELETE", "POST"])
    def test_cors_preflight_is_not_granted(
        self, client: TestClient, method: str
    ) -> None:
        response = client.options(
            ANALYSES,
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": method,
            },
        )

        assert "access-control-allow-origin" not in response.headers
        assert "access-control-allow-methods" not in response.headers


class TestTestIsolation:
    def test_default_database_is_inside_the_temporary_home(
        self, isolated_home: Path
    ) -> None:
        assert default_database_path().is_relative_to(isolated_home)

    def test_app_uses_only_the_configured_database(
        self, api_app: FastAPI, database_path: Path
    ) -> None:
        assert database_path.exists()
        assert not default_database_path().exists()

"""The REST endpoints and their status codes."""

import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from git_detective import __version__
from git_detective.reporting import (
    AUTHORS_DISCLAIMER,
    COUPLING_DISCLAIMER,
    SCORE_DISCLAIMER,
)

BASE = "/api/v1"
ANALYSES = f"{BASE}/analyses"
TYPES = ["hotspots", "authors", "coupling"]


def create(client: TestClient, analysis_type: str = "hotspots", **parameters: Any):
    return client.post(
        ANALYSES,
        json={"repository": "demo", "type": analysis_type, "parameters": parameters},
    )


def create_ok(client: TestClient, analysis_type: str = "hotspots", **parameters: Any):
    response = create(client, analysis_type, **parameters)
    assert response.status_code == 201, response.text
    return response.json()


def assert_error(response, status_code: int, code: str) -> None:
    assert response.status_code == status_code, response.text
    body = response.json()
    assert list(body) == ["error"]
    assert body["error"]["code"] == code
    assert body["error"]["message"]
    assert isinstance(body["error"]["details"], list)


class TestHealth:
    def test_ok(self, client: TestClient) -> None:
        response = client.get(f"{BASE}/health")

        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "version": __version__,
            "database": "ok",
        }

    def test_broken_database(self, client: TestClient, database_path: Path) -> None:
        database_path.unlink()
        database_path.mkdir()

        response = client.get(f"{BASE}/health")

        assert_error(response, 503, "storage_unavailable")
        assert str(database_path) not in response.text


class TestRepositories:
    def test_lists_ids_only(self, client: TestClient, repo_path: Path) -> None:
        response = client.get(f"{BASE}/repositories")

        assert response.status_code == 200
        assert response.json() == {
            "items": [{"id": "demo", "available": True}],
            "total": 1,
        }
        assert str(repo_path) not in response.text

    def test_reports_unavailable_repository(
        self, client: TestClient, repo_path: Path
    ) -> None:
        shutil.rmtree(repo_path)

        response = client.get(f"{BASE}/repositories")

        assert response.json()["items"] == [{"id": "demo", "available": False}]


@pytest.mark.usefixtures("seeded_repo")
class TestCreateAnalysis:
    @pytest.mark.parametrize("analysis_type", TYPES)
    def test_creates_and_stores(
        self, client: TestClient, seeded_repo: str, analysis_type: str
    ) -> None:
        response = create(client, analysis_type)

        assert response.status_code == 201
        body = response.json()
        uuid.UUID(body["id"])
        assert response.headers["location"] == f"{ANALYSES}/{body['id']}"
        assert body["repository"] == "demo"
        assert body["type"] == analysis_type
        assert body["head_sha"] == seeded_repo
        assert body["commits_analyzed"] == 3
        assert body["report"]["report"] == analysis_type
        assert client.get(response.headers["location"]).json() == body

    def test_analysis_is_finished_when_the_response_arrives(
        self, client: TestClient
    ) -> None:
        body = create_ok(client)

        assert "status" not in body
        assert body["report"]["files"]

    def test_default_parameters_are_stored(self, client: TestClient) -> None:
        body = create_ok(client, "coupling")

        assert body["parameters"] == {
            "limit": 500,
            "top": 20,
            "exclude": [],
            "min_cochanges": 3,
            "max_files_per_commit": 50,
        }

    def test_parameters_can_be_omitted(self, client: TestClient) -> None:
        response = client.post(ANALYSES, json={"repository": "demo", "type": "authors"})

        assert response.status_code == 201
        assert response.json()["parameters"]["sort"] == "commits"

    def test_limit_is_applied(self, client: TestClient) -> None:
        assert create_ok(client, limit=1)["commits_analyzed"] == 1

    def test_top_is_applied(self, client: TestClient) -> None:
        body = create_ok(client, top=1)

        assert len(body["report"]["files"]) == 1
        assert body["report"]["summary"]["files_changed"] == 3

    def test_recent_days_is_applied(self, client: TestClient) -> None:
        body = create_ok(client, recent_days=7)

        assert body["report"]["parameters"]["recent_days"] == 7

    def test_filters_are_applied(self, client: TestClient) -> None:
        body = create_ok(client, exclude=["*.lock"])

        paths = {row["path"] for row in body["report"]["files"]}
        assert paths == {"src/a.py", "src/b.py"}
        assert body["parameters"]["exclude"] == ["*.lock"]

    def test_author_sort_is_applied(self, client: TestClient) -> None:
        body = create_ok(client, "authors", sort="churn")

        assert body["report"]["parameters"]["sort"] == "churn"

    def test_coupling_thresholds_are_applied(self, client: TestClient) -> None:
        strict = create_ok(client, "coupling", min_cochanges=4)
        loose = create_ok(client, "coupling", min_cochanges=1)

        assert strict["report"]["pairs"] == []
        assert loose["report"]["pairs"]

    def test_unknown_repository(self, client: TestClient) -> None:
        response = client.post(
            ANALYSES, json={"repository": "other", "type": "authors"}
        )

        assert_error(response, 404, "repository_not_found")

    def test_unavailable_repository(self, client: TestClient, repo_path: Path) -> None:
        shutil.rmtree(repo_path)

        response = create(client)

        assert_error(response, 409, "repository_unavailable")
        assert str(repo_path) not in response.text

    def test_invalid_ignore_file_contents(
        self, client: TestClient, repo_path: Path
    ) -> None:
        (repo_path / ".gitdetectiveignore").write_bytes(b"\xff\xfe")

        response = create(client)

        assert_error(response, 409, "repository_unavailable")
        assert str(repo_path) not in response.text

    def test_invalid_mailmap_contents(
        self, client: TestClient, repo_path: Path
    ) -> None:
        (repo_path / ".mailmap").write_bytes(b"\xff\xfe")

        response = create(client, "authors")

        assert_error(response, 409, "repository_unavailable")
        assert str(repo_path) not in response.text

    def test_too_many_analyses_at_once(
        self, client: TestClient, api_app: FastAPI
    ) -> None:
        slots = api_app.state.runner._slots
        for _ in range(2):
            assert slots.acquire(blocking=False)

        response = create(client)

        assert_error(response, 429, "analysis_busy")
        assert int(response.headers["retry-after"]) > 0

        # A slot that frees up can be used again.
        slots.release()
        assert create(client).status_code == 201

    def test_slot_is_released_after_a_failure(
        self, client: TestClient, repo_path: Path
    ) -> None:
        (repo_path / ".gitdetectiveignore").write_bytes(b"\xff\xfe")
        for _ in range(5):
            assert_error(create(client), 409, "repository_unavailable")

        (repo_path / ".gitdetectiveignore").unlink()
        assert create(client).status_code == 201

    def test_result_too_large(self, client: TestClient, api_app: FastAPI) -> None:
        api_app.state.runner._max_result_bytes = 10

        assert_error(create(client), 422, "analysis_too_large")
        assert client.get(ANALYSES).json()["total"] == 0

    def test_database_failure(self, client: TestClient, database_path: Path) -> None:
        database_path.unlink()
        database_path.mkdir()

        response = create(client)

        assert_error(response, 503, "storage_unavailable")
        assert str(database_path) not in response.text

    def test_unexpected_failure_shows_no_details(
        self, api_app: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(*_: Any, **__: Any) -> None:
            raise RuntimeError("secret /private/path")

        monkeypatch.setattr("git_detective.api.runner.run_analysis", explode)
        client = TestClient(
            api_app, base_url="http://127.0.0.1", raise_server_exceptions=False
        )

        response = create(client)

        assert_error(response, 500, "internal_error")
        assert "secret" not in response.text


class TestEmptyRepository:
    @pytest.mark.parametrize("analysis_type", TYPES)
    def test_is_a_valid_analysis(self, client: TestClient, analysis_type: str) -> None:
        body = create_ok(client, analysis_type)

        assert body["head_sha"] is None
        assert body["commits_analyzed"] == 0
        section = client.get(f"{ANALYSES}/{body['id']}/{analysis_type}")
        assert section.status_code == 200


@pytest.mark.usefixtures("seeded_repo")
class TestListAnalyses:
    def test_empty(self, client: TestClient) -> None:
        response = client.get(ANALYSES)

        assert response.status_code == 200
        assert response.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}

    def test_newest_first_without_reports(self, client: TestClient) -> None:
        ids = [create_ok(client, t)["id"] for t in TYPES]

        body = client.get(ANALYSES).json()

        assert [item["id"] for item in body["items"]] == ids[::-1]
        assert body["total"] == 3
        assert all("report" not in item for item in body["items"])

    def test_pagination(self, client: TestClient) -> None:
        ids = [create_ok(client, t)["id"] for t in TYPES]

        body = client.get(ANALYSES, params={"limit": 1, "offset": 1}).json()

        assert [item["id"] for item in body["items"]] == [ids[1]]
        assert (body["total"], body["limit"], body["offset"]) == (3, 1, 1)

    def test_filter_by_type(self, client: TestClient) -> None:
        for analysis_type in TYPES:
            create_ok(client, analysis_type)

        body = client.get(ANALYSES, params={"type": "authors"}).json()

        assert [item["type"] for item in body["items"]] == ["authors"]

    def test_filter_by_repository(self, client: TestClient) -> None:
        create_ok(client)

        assert client.get(ANALYSES, params={"repository": "demo"}).json()["total"] == 1
        assert client.get(ANALYSES, params={"repository": "x"}).json()["total"] == 0

    def test_database_failure(self, client: TestClient, database_path: Path) -> None:
        database_path.unlink()
        database_path.mkdir()

        assert_error(client.get(ANALYSES), 503, "storage_unavailable")


@pytest.mark.usefixtures("seeded_repo")
class TestGetAnalysis:
    def test_returns_the_report(self, client: TestClient) -> None:
        created = create_ok(client)

        response = client.get(f"{ANALYSES}/{created['id']}")

        assert response.status_code == 200
        assert response.json() == created

    def test_unknown_id(self, client: TestClient) -> None:
        response = client.get(f"{ANALYSES}/{uuid.uuid4()}")

        assert_error(response, 404, "analysis_not_found")

    def test_invalid_id(self, client: TestClient) -> None:
        assert_error(client.get(f"{ANALYSES}/not-a-uuid"), 422, "validation_error")


@pytest.mark.usefixtures("seeded_repo")
class TestHistoryWithoutRepository:
    """Reading old reports must not need the repository at all."""

    def test_reports_survive_repository_removal(
        self, client: TestClient, repo_path: Path
    ) -> None:
        created = {t: create_ok(client, t) for t in TYPES}
        sections = {
            t: client.get(f"{ANALYSES}/{created[t]['id']}/{t}").json() for t in TYPES
        }

        shutil.rmtree(repo_path)

        for analysis_type in TYPES:
            analysis_id = created[analysis_type]["id"]
            assert (
                client.get(f"{ANALYSES}/{analysis_id}").json()
                == (created[analysis_type])
            )
            assert (
                client.get(f"{ANALYSES}/{analysis_id}/{analysis_type}").json()
                == (sections[analysis_type])
            )
        assert client.get(ANALYSES).json()["total"] == 3
        assert_error(create(client), 409, "repository_unavailable")

    def test_reports_do_not_change_with_new_commits(
        self, client: TestClient, make_commit, seeded_repo: str
    ) -> None:
        old = create_ok(client)

        new_head = make_commit({"new.py": "1\n"})
        new = create_ok(client)

        assert new["head_sha"] == new_head
        assert new["commits_analyzed"] == 4
        stored = client.get(f"{ANALYSES}/{old['id']}").json()
        assert stored == old
        assert stored["head_sha"] == seeded_repo

    def test_reading_never_opens_git(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        created = create_ok(client)

        def fail(*_: Any, **__: Any) -> None:
            raise AssertionError("Git must not be used to read history")

        monkeypatch.setattr("git_detective.service.GitReader", fail)

        assert client.get(f"{ANALYSES}/{created['id']}").status_code == 200
        assert client.get(f"{ANALYSES}/{created['id']}/hotspots").status_code == 200
        assert client.get(ANALYSES).status_code == 200


@pytest.mark.usefixtures("seeded_repo")
class TestSections:
    def test_hotspots(self, client: TestClient) -> None:
        created = create_ok(client)

        response = client.get(f"{ANALYSES}/{created['id']}/hotspots")

        assert response.status_code == 200
        body = response.json()
        assert body["files"] == created["report"]["files"]
        assert body["summary"] == created["report"]["summary"]
        assert body["disclaimer"] == SCORE_DISCLAIMER
        assert body["analysis"]["id"] == created["id"]
        assert "report" not in body["analysis"]

    def test_authors(self, client: TestClient) -> None:
        created = create_ok(client, "authors")

        response = client.get(f"{ANALYSES}/{created['id']}/authors")

        assert response.status_code == 200
        body = response.json()
        assert body["disclaimer"] == AUTHORS_DISCLAIMER
        assert [a["name"] for a in body["authors"]] == ["Alice Example", "Bob Builder"]
        for row, stored in zip(
            body["authors"], created["report"]["authors"], strict=True
        ):
            assert set(row) == set(stored)
            for field in ("first_commit", "last_commit"):
                assert datetime.fromisoformat(row[field]) == datetime.fromisoformat(
                    stored[field]
                )
            for field in set(row) - {"first_commit", "last_commit"}:
                assert row[field] == stored[field]

    def test_coupling(self, client: TestClient) -> None:
        created = create_ok(client, "coupling")

        response = client.get(f"{ANALYSES}/{created['id']}/coupling")

        assert response.status_code == 200
        body = response.json()
        assert body["pairs"] == created["report"]["pairs"]
        assert body["graph"] == created["report"]["graph"]
        assert body["disclaimer"] == COUPLING_DISCLAIMER
        assert body["pairs"][0]["file_a"] == "src/a.py"
        assert body["pairs"][0]["jaccard"] == 1.0

    @pytest.mark.parametrize(
        ("stored_type", "requested"),
        [
            ("hotspots", "authors"),
            ("authors", "coupling"),
            ("coupling", "hotspots"),
        ],
    )
    def test_wrong_type(
        self, client: TestClient, stored_type: str, requested: str
    ) -> None:
        created = create_ok(client, stored_type)

        response = client.get(f"{ANALYSES}/{created['id']}/{requested}")

        assert_error(response, 409, "analysis_type_mismatch")

    @pytest.mark.parametrize("section", TYPES)
    def test_unknown_id(self, client: TestClient, section: str) -> None:
        response = client.get(f"{ANALYSES}/{uuid.uuid4()}/{section}")

        assert_error(response, 404, "analysis_not_found")

    @pytest.mark.parametrize("section", TYPES)
    def test_invalid_id(self, client: TestClient, section: str) -> None:
        response = client.get(f"{ANALYSES}/not-a-uuid/{section}")

        assert_error(response, 422, "validation_error")


@pytest.mark.usefixtures("seeded_repo")
class TestDeleteAnalysis:
    def test_deletes(self, client: TestClient) -> None:
        kept = create_ok(client)
        removed = create_ok(client)

        response = client.delete(f"{ANALYSES}/{removed['id']}")

        assert response.status_code == 204
        assert response.content == b""
        assert_error(
            client.get(f"{ANALYSES}/{removed['id']}"), 404, "analysis_not_found"
        )
        assert client.get(f"{ANALYSES}/{kept['id']}").status_code == 200
        assert client.get(ANALYSES).json()["total"] == 1

    def test_second_delete_is_not_found(self, client: TestClient) -> None:
        created = create_ok(client)
        client.delete(f"{ANALYSES}/{created['id']}")

        response = client.delete(f"{ANALYSES}/{created['id']}")

        assert_error(response, 404, "analysis_not_found")

    def test_invalid_id(self, client: TestClient) -> None:
        assert_error(client.delete(f"{ANALYSES}/not-a-uuid"), 422, "validation_error")

    def test_leaves_the_repository_alone(
        self, client: TestClient, repo_path: Path
    ) -> None:
        created = create_ok(client)

        client.delete(f"{ANALYSES}/{created['id']}")

        assert (repo_path / "src" / "a.py").exists()


class TestRouting:
    def test_unknown_route_uses_the_error_format(self, client: TestClient) -> None:
        assert_error(client.get(f"{BASE}/nope"), 404, "not_found")

    def test_wrong_method_uses_the_error_format(self, client: TestClient) -> None:
        response = client.put(f"{BASE}/health")

        assert_error(response, 405, "method_not_allowed")
        assert "GET" in response.headers["allow"]

    def test_only_the_documented_endpoints_exist(self, api_app: FastAPI) -> None:
        operations = {
            (method.upper(), path)
            for path, item in api_app.openapi()["paths"].items()
            for method in item
        }

        assert operations == {
            ("GET", f"{BASE}/health"),
            ("GET", f"{BASE}/repositories"),
            ("POST", ANALYSES),
            ("GET", ANALYSES),
            ("GET", f"{ANALYSES}/{{analysis_id}}"),
            ("GET", f"{ANALYSES}/{{analysis_id}}/hotspots"),
            ("GET", f"{ANALYSES}/{{analysis_id}}/authors"),
            ("GET", f"{ANALYSES}/{{analysis_id}}/coupling"),
            ("DELETE", f"{ANALYSES}/{{analysis_id}}"),
        }


class TestDocs:
    def test_swagger_ui(self, client: TestClient) -> None:
        response = client.get("/docs")

        assert response.status_code == 200
        assert "swagger" in response.text.lower()

    def test_openapi_documents_limits_and_errors(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()

        limit = schema["components"]["schemas"]["HotspotsParameters"]["properties"][
            "limit"
        ]
        assert limit["maximum"] == 5000
        post = schema["paths"][ANALYSES]["post"]
        assert {"201", "404", "409", "422", "429", "503"} <= set(post["responses"])
        assert post["responses"]["429"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }

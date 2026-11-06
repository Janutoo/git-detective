"""Request validation: limits, unknown fields and malformed input."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from git_detective.exceptions import IgnorePatternError

ANALYSES = "/api/v1/analyses"

pytestmark = pytest.mark.usefixtures("seeded_repo")


def post(client: TestClient, analysis_type: str, **parameters: Any):
    return client.post(
        ANALYSES,
        json={"repository": "demo", "type": analysis_type, "parameters": parameters},
    )


def assert_invalid(response) -> dict:
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"]
    for detail in error["details"]:
        assert set(detail) == {"loc", "msg", "type"}
    return error


@pytest.mark.parametrize(
    ("analysis_type", "field", "accepted", "rejected"),
    [
        ("hotspots", "limit", [1, 5000], [0, -1, 5001]),
        ("authors", "limit", [1, 5000], [0, 5001]),
        ("coupling", "limit", [1, 5000], [0, 5001]),
        ("hotspots", "top", [1, 1000], [0, 1001]),
        ("authors", "top", [1, 1000], [0, 1001]),
        ("coupling", "top", [1, 1000], [0, 1001]),
        ("hotspots", "recent_days", [1, 3650], [0, 3651]),
        ("coupling", "min_cochanges", [1, 10000], [0, 10001]),
        ("coupling", "max_files_per_commit", [2, 200], [1, 201]),
    ],
)
class TestNumericLimits:
    def test_boundaries_are_accepted(
        self, client: TestClient, analysis_type, field, accepted, rejected
    ) -> None:
        for value in accepted:
            response = post(client, analysis_type, **{field: value})
            assert response.status_code == 201, response.text

    def test_values_outside_are_rejected(
        self, client: TestClient, analysis_type, field, accepted, rejected
    ) -> None:
        for value in rejected:
            error = assert_invalid(post(client, analysis_type, **{field: value}))
            assert field in error["details"][0]["loc"]

    def test_only_integers_are_accepted(
        self, client: TestClient, analysis_type, field, accepted, rejected
    ) -> None:
        for value in ["10", 1.5, True, None]:
            assert_invalid(post(client, analysis_type, **{field: value}))


class TestUnknownFields:
    @pytest.mark.parametrize("analysis_type", ["hotspots", "authors", "coupling"])
    @pytest.mark.parametrize("field", ["path", "show_emails", "command", "cwd"])
    def test_unknown_parameter(
        self, client: TestClient, analysis_type: str, field: str
    ) -> None:
        assert_invalid(post(client, analysis_type, **{field: True}))

    def test_unknown_top_level_field(self, client: TestClient) -> None:
        assert_invalid(
            client.post(
                ANALYSES,
                json={"repository": "demo", "type": "authors", "path": "/etc"},
            )
        )

    @pytest.mark.parametrize(
        ("analysis_type", "field", "value"),
        [
            ("hotspots", "sort", "churn"),
            ("hotspots", "min_cochanges", 1),
            ("authors", "recent_days", 1),
            ("coupling", "recent_days", 1),
        ],
    )
    def test_parameter_of_another_type(
        self, client: TestClient, analysis_type: str, field: str, value: Any
    ) -> None:
        assert_invalid(post(client, analysis_type, **{field: value}))


class TestRequestShape:
    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"repository": "demo"},
            {"type": "hotspots"},
            {"repository": "demo", "type": "scan"},
            {"repository": "demo", "type": "unknown"},
            {"repository": "demo", "type": None},
            {"repository": 1, "type": "hotspots"},
            {"repository": "demo", "type": "hotspots", "parameters": []},
            [],
            "text",
        ],
    )
    def test_invalid_body(self, client: TestClient, body: Any) -> None:
        assert_invalid(client.post(ANALYSES, json=body))

    @pytest.mark.parametrize(
        "repository",
        [
            "../etc",
            "/etc/passwd",
            "demo/../../etc",
            "Demo",
            "",
            ".hidden",
            "-flag",
            "a" * 65,
            "demo\n",
            "demo\x00",
            "file:///etc",
        ],
    )
    def test_repository_id_format(self, client: TestClient, repository: str) -> None:
        assert_invalid(
            client.post(ANALYSES, json={"repository": repository, "type": "authors"})
        )

    def test_malformed_json(self, client: TestClient) -> None:
        assert_invalid(
            client.post(
                ANALYSES,
                content='{"repository": ',
                headers={"Content-Type": "application/json"},
            )
        )

    def test_invalid_sort(self, client: TestClient) -> None:
        assert_invalid(post(client, "authors", sort="name"))

    def test_validation_errors_do_not_echo_input(self, client: TestClient) -> None:
        response = post(client, "hotspots", limit="/secret/value")

        assert_invalid(response)
        assert "/secret/value" not in response.text


class TestExcludePatterns:
    def test_at_the_limits(self, client: TestClient) -> None:
        response = post(client, "hotspots", exclude=["x" * 256] * 100)

        assert response.status_code == 201

    @pytest.mark.parametrize(
        "exclude",
        [
            ["x"] * 101,
            ["x" * 257],
            [""],
            ["a\nb"],
            ["a\x00b"],
            ["a\tb"],
            "*.lock",
            [1],
            None,
        ],
    )
    def test_invalid(self, client: TestClient, exclude: Any) -> None:
        assert_invalid(post(client, "hotspots", exclude=exclude))

    def test_lone_surrogate_in_json(self, client: TestClient) -> None:
        assert_invalid(
            client.post(
                ANALYSES,
                content=(
                    '{"repository": "demo", "type": "hotspots", '
                    '"parameters": {"exclude": ["\\ud800"]}}'
                ),
                headers={"Content-Type": "application/json"},
            )
        )

    def test_pattern_rejected_by_the_filter(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def reject(_: list[str]) -> None:
            raise IgnorePatternError("Invalid exclude pattern: x")

        monkeypatch.setattr("git_detective.api.schemas.PathFilter", reject)

        error = assert_invalid(post(client, "hotspots", exclude=["x"]))
        assert "Invalid exclude pattern" in error["details"][0]["msg"]


class TestListQuery:
    @pytest.mark.parametrize(
        "params",
        [
            {"limit": 0},
            {"limit": 101},
            {"limit": "many"},
            {"offset": -1},
            {"offset": "x"},
            {"type": "scan"},
            {"repository": "../etc"},
            {"repository": "/etc/passwd"},
        ],
    )
    def test_invalid(self, client: TestClient, params: dict) -> None:
        assert_invalid(client.get(ANALYSES, params=params))

    @pytest.mark.parametrize("params", [{"limit": 1}, {"limit": 100}, {"offset": 0}])
    def test_boundaries(self, client: TestClient, params: dict) -> None:
        assert client.get(ANALYSES, params=params).status_code == 200

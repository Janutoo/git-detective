"""Loading the API configuration file."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from git_detective.api.config import ApiSettings, default_database_path, load_settings
from git_detective.exceptions import ApiConfigError


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


class TestLoadSettings:
    def test_minimal_file_uses_defaults(self, tmp_path: Path) -> None:
        path = write_config(tmp_path, '[repositories]\ndemo = "/srv/demo"\n')

        settings = load_settings(path)

        assert settings.repositories == {"demo": Path("/srv/demo")}
        assert settings.database == default_database_path()
        assert settings.max_concurrent_analyses == 2

    def test_all_options(self, tmp_path: Path) -> None:
        path = write_config(
            tmp_path,
            'database = "/data/a.db"\nmax_concurrent_analyses = 4\n'
            '[repositories]\ndemo = "/srv/demo"\n',
        )

        settings = load_settings(path)

        assert settings.database == Path("/data/a.db")
        assert settings.max_concurrent_analyses == 4

    def test_expands_home_directory(self, tmp_path: Path, isolated_home: Path) -> None:
        path = write_config(
            tmp_path,
            'database = "~/a.db"\n[repositories]\ndemo = "~/code/demo"\n',
        )

        settings = load_settings(path)

        assert settings.database == isolated_home / "a.db"
        assert settings.repositories["demo"] == isolated_home / "code" / "demo"

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ApiConfigError, match="Cannot read"):
            load_settings(tmp_path / "missing.toml")

    def test_invalid_toml(self, tmp_path: Path) -> None:
        with pytest.raises(ApiConfigError, match="Invalid TOML"):
            load_settings(write_config(tmp_path, "this is = = not toml"))

    @pytest.mark.parametrize(
        ("text", "field"),
        [
            ("", "repositories"),
            ("[repositories]\n", "repositories"),
            ('[repositories]\ndemo = "relative/path"\n', "repositories"),
            ('[repositories]\n"../evil" = "/srv/demo"\n', "repositories"),
            ('[repositories]\n"UPPER" = "/srv/demo"\n', "repositories"),
            ('database = "relative.db"\n[repositories]\nd = "/srv/d"\n', "database"),
            (
                'max_concurrent_analyses = 0\n[repositories]\nd = "/srv/d"\n',
                "max_concurrent_analyses",
            ),
            ('unknown = 1\n[repositories]\nd = "/srv/d"\n', "unknown"),
        ],
    )
    def test_invalid_contents(self, tmp_path: Path, text: str, field: str) -> None:
        with pytest.raises(ApiConfigError, match=field):
            load_settings(write_config(tmp_path, text))


class TestDefaultDatabasePath:
    def test_uses_xdg_data_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

        assert default_database_path() == tmp_path / "git-detective" / "analyses.db"

    def test_falls_back_to_home(self, isolated_home: Path) -> None:
        assert default_database_path() == (
            isolated_home / ".local" / "share" / "git-detective" / "analyses.db"
        )


class TestSettingsModel:
    def test_is_immutable(self) -> None:
        settings = ApiSettings(repositories={"demo": Path("/srv/demo")})

        with pytest.raises(ValidationError):
            settings.max_concurrent_analyses = 5

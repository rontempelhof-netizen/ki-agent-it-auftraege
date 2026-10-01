from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.config import ConfigError, load_settings

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"


def test_defaults_without_any_config_file():
    settings = load_settings(env_file=None)

    assert settings.environment == "development"
    assert settings.database.url == "sqlite:///data/agent.db"
    assert settings.logging.level == "INFO"
    assert settings.logging.format == "json"


def test_repository_config_file_is_valid():
    settings = load_settings(config_file=REPO_CONFIG, env_file=None)

    assert settings.app_name == "ki-agent-it-auftraege"
    assert settings.database.url.startswith("sqlite:///")


def test_values_are_loaded_from_yaml(write_yaml):
    path = write_yaml(
        """
environment: test
database:
  url: sqlite:///from-yaml.db
logging:
  level: debug
  format: text
"""
    )

    settings = load_settings(config_file=path, env_file=None)

    assert settings.environment == "test"
    assert settings.database.url == "sqlite:///from-yaml.db"
    assert settings.logging.level == "DEBUG"
    assert settings.logging.format == "text"


def test_environment_overrides_yaml(write_yaml, monkeypatch):
    path = write_yaml("database:\n  url: sqlite:///from-yaml.db\nlogging:\n  level: INFO\n")
    monkeypatch.setenv("AGENT_DATABASE__URL", "sqlite:///from-env.db")
    monkeypatch.setenv("AGENT_ENVIRONMENT", "production")

    settings = load_settings(config_file=path, env_file=None)

    assert settings.database.url == "sqlite:///from-env.db"
    assert settings.environment == "production"
    assert settings.logging.level == "INFO"  # nicht überschriebene Werte bleiben aus YAML


def test_dotenv_file_is_read_and_env_wins_over_it(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("AGENT_LOGGING__LEVEL=WARNING\nAGENT_ENVIRONMENT=test\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_ENVIRONMENT", "production")

    settings = load_settings(env_file=env_file)

    assert settings.logging.level == "WARNING"
    assert settings.environment == "production"


def test_config_file_from_environment_variable(write_yaml, monkeypatch):
    path = write_yaml("environment: test\n", name="custom.yaml")
    monkeypatch.setenv("AGENT_CONFIG_FILE", str(path))

    assert load_settings(env_file=None).environment == "test"


def test_missing_explicit_config_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_settings(config_file=tmp_path / "missing.yaml", env_file=None)


def test_repository_config_matches_scoring_defaults():
    """config/settings.yaml und Code-Defaults dürfen nicht auseinanderlaufen."""
    from src.config import ScoringSettings

    assert load_settings(config_file=REPO_CONFIG, env_file=None).scoring == ScoringSettings()


def test_scoring_values_from_yaml_and_environment(write_yaml, monkeypatch):
    path = write_yaml("scoring:\n  thresholds:\n    a: 85\n  hard_fail:\n    available_certifications: [ISTQB]\n")
    monkeypatch.setenv("AGENT_SCORING__THRESHOLDS__B", "70")
    monkeypatch.setenv("AGENT_SCORING__UNKNOWN_FRACTION", "0.3")

    scoring = load_settings(config_file=path, env_file=None).scoring

    assert (scoring.thresholds.a, scoring.thresholds.b, scoring.thresholds.c) == (85, 70, 50)
    assert scoring.hard_fail.available_certifications == ["ISTQB"]
    assert scoring.unknown_fraction == 0.3


@pytest.mark.parametrize(
    "yaml_content",
    [
        "scoring:\n  weights:\n    scope: 25\n",  # Summe 105
        "scoring:\n  thresholds:\n    b: 85\n",  # b > a
        "scoring:\n  unknown_fraction: 1.5\n",
    ],
)
def test_invalid_scoring_config_rejected(write_yaml, yaml_content):
    with pytest.raises(ValidationError):
        load_settings(config_file=write_yaml(yaml_content), env_file=None)


def test_invalid_value_raises_validation_error(write_yaml):
    path = write_yaml("logging:\n  level: LOUD\n")

    with pytest.raises(ValidationError):
        load_settings(config_file=path, env_file=None)

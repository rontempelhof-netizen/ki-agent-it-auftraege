"""Pydantic-basierte Anwendungskonfiguration.

Priorität (höchste zuerst):
1. Environment-Variablen (Präfix ``AGENT_``, Verschachtelung über ``__``)
2. ``.env``-Datei
3. YAML-Datei (Standard: ``config/settings.yaml``, überschreibbar via ``AGENT_CONFIG_FILE``)
4. Defaults im Code
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from src.domain.enums import Region

ENV_PREFIX = "AGENT_"
CONFIG_FILE_ENV_VAR = "AGENT_CONFIG_FILE"
DEFAULT_CONFIG_FILE = Path("config/settings.yaml")
DEFAULT_ENV_FILE = Path(".env")

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class ConfigError(Exception):
    """Konfiguration konnte nicht geladen werden."""


class DatabaseSettings(BaseModel):
    url: str = "sqlite:///data/agent.db"
    echo: bool = False


class LoggingSettings(BaseModel):
    level: LogLevel = "INFO"
    format: Literal["json", "text"] = "json"

    @field_validator("level", mode="before")
    @classmethod
    def _normalize_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


class ScoreWeights(BaseModel):
    """Maximalpunkte je Kriterium (docs/requirements.md, Abschnitt 8). Summe muss 100 sein."""

    scope: int = Field(20, ge=0)
    deliverability: int = Field(15, ge=0)
    win_probability: int = Field(20, ge=0)
    budget_effort: int = Field(15, ge=0)
    direct_customer: int = Field(10, ge=0)
    remote_fit: int = Field(5, ge=0)
    urgency: int = Field(5, ge=0)
    follow_up: int = Field(5, ge=0)
    consulting_fit: int = Field(5, ge=0)

    @model_validator(mode="after")
    def _sum_is_100(self) -> ScoreWeights:
        total = sum(self.model_dump().values())
        if total != 100:
            raise ValueError(f"Summe der Score-Gewichte muss 100 sein, ist {total}")
        return self


class ClassThresholds(BaseModel):
    """Untergrenzen (inklusive) der Klassen A/B/C; darunter REJECT."""

    a: int = Field(80, le=100)
    b: int = 65
    c: int = Field(50, gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> ClassThresholds:
        if not self.a > self.b > self.c:
            raise ValueError("Schwellen müssen a > b > c erfüllen")
        return self


class HardFailSettings(BaseModel):
    available_certifications: list[str] = []
    has_security_clearance: bool = False
    onsite_allowed_regions: list[Region] = [Region.DACH]
    max_person_days: float = Field(60, gt=0)
    max_must_have_requirements: int = Field(15, ge=0)
    min_day_rate_eur: float = Field(300, ge=0)


class ScopeSettings(BaseModel):
    target_person_days_min: float = Field(1, gt=0)
    target_person_days_max: float = Field(20, gt=0)
    stretch_person_days_max: float = Field(40, gt=0)
    must_have_soft_limit: int = Field(8, ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> ScopeSettings:
        if not self.target_person_days_min <= self.target_person_days_max <= self.stretch_person_days_max:
            raise ValueError("Personentage: min <= target_max <= stretch_max erforderlich")
        return self


class BudgetSettings(BaseModel):
    target_day_rate_eur: float = Field(800, gt=0)
    acceptable_day_rate_eur: float = Field(600, gt=0)
    hours_per_day: float = Field(8, gt=0)
    first_order_min_eur: float = Field(1_000, ge=0)
    first_order_max_eur: float = Field(15_000, gt=0)
    currency_rates_to_eur: dict[str, float] = {"EUR": 1.0, "CHF": 1.05, "USD": 0.92, "GBP": 1.17}


class ScoringSettings(BaseModel):
    weights: ScoreWeights = ScoreWeights()
    thresholds: ClassThresholds = ClassThresholds()
    hard_fail: HardFailSettings = HardFailSettings()
    scope: ScopeSettings = ScopeSettings()
    budget: BudgetSettings = BudgetSettings()
    unknown_fraction: float = Field(0.4, ge=0, le=1)
    """Anteil der Maximalpunkte, wenn eine Information unbekannt ist (None)."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_nested_delimiter="__",
        env_file=DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
        yaml_file=DEFAULT_CONFIG_FILE,
        yaml_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ki-agent-it-auftraege"
    environment: Literal["development", "test", "production"] = "development"
    database: DatabaseSettings = DatabaseSettings()
    logging: LoggingSettings = LoggingSettings()
    scoring: ScoringSettings = ScoringSettings()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
        )


def load_settings(
    config_file: Path | str | None = None,
    env_file: Path | str | None = DEFAULT_ENV_FILE,
) -> Settings:
    """Lädt die Konfiguration aus YAML, ``.env`` und Environment.

    Ein explizit angegebener Pfad (Argument oder ``AGENT_CONFIG_FILE``) muss existieren.
    Fehlt die Standarddatei, werden Defaults und Environment verwendet.
    """
    explicit = config_file or os.environ.get(CONFIG_FILE_ENV_VAR)
    yaml_path = Path(explicit) if explicit else DEFAULT_CONFIG_FILE
    if explicit and not yaml_path.is_file():
        raise ConfigError(f"Konfigurationsdatei nicht gefunden: {yaml_path}")

    class _ResolvedSettings(Settings):
        model_config = SettingsConfigDict(yaml_file=yaml_path, env_file=env_file)

    return _ResolvedSettings()

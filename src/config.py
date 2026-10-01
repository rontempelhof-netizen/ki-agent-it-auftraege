"""Pydantic-basierte Anwendungskonfiguration.

Priorität (höchste zuerst):
1. Environment-Variablen (Präfix ``AGENT_``, Verschachtelung über ``__``)
2. ``.env``-Datei
3. YAML-Datei (Standard: ``config/settings.yaml``, überschreibbar via ``AGENT_CONFIG_FILE``)
4. Defaults im Code
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, field_validator, model_validator
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


def _compile_ignorecase(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        return re.compile(value, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"Ungültiger regulärer Ausdruck {value!r}: {exc}") from exc


Regex = Annotated[re.Pattern[str], BeforeValidator(_compile_ignorecase)]
"""Regulärer Ausdruck aus der Konfiguration, immer case-insensitiv kompiliert."""

LinkLayout = Literal["link_start", "link_end"]


class EmailSourceProfile(BaseModel):
    """Parserprofil für die Benachrichtigungsmails eines Portals.

    Layout ``link_start``: Ein Projektblock beginnt mit dem (Titel-)Link.
    Layout ``link_end``: Ein Projektblock endet mit dem Projektlink.
    """

    name: str = Field(min_length=1)
    sender_patterns: list[Regex] = Field(min_length=1)
    subject_patterns: list[Regex] = []
    project_url_patterns: list[Regex] = Field(min_length=1)
    project_id_pattern: Regex | None = None
    html_layout: LinkLayout = "link_start"
    text_layout: LinkLayout = "link_start"
    content_start_markers: list[Regex] = []
    footer_markers: list[Regex] = []
    strip_url_query: bool = True


def _default_email_profiles() -> list[EmailSourceProfile]:
    return [
        EmailSourceProfile(
            name="freelancermap",
            sender_patterns=[r"@(mail\.)?freelancermap\.(de|com|at|ch)$"],
            project_url_patterns=[r"^https?://(www\.)?freelancermap\.(de|com|at|ch)/projekt/[^/?#]+"],
            project_id_pattern=r"-(\d+)(?:[/?#]|$)",
            html_layout="link_start",
            text_layout="link_start",
            footer_markers=[r"Sie erhalten diese E-Mail"],
        ),
        EmailSourceProfile(
            name="freelance.de",
            sender_patterns=[r"@(mail\.)?freelance\.de$"],
            project_url_patterns=[r"^https?://(www\.)?freelance\.de/projekte/projekt-\d+"],
            project_id_pattern=r"projekt-(\d+)",
            html_layout="link_start",
            text_layout="link_end",
            content_start_markers=[r"Projekte? gefunden[^\n]*\n"],
            footer_markers=[r"Projektalarm verwalten"],
        ),
    ]


class EmailSourceSettings(BaseModel):
    enabled: bool = True
    mailbox_dir: Path = Path("data/inbox")
    """Offline-Postfach: Verzeichnis mit ``.eml``-Dateien."""
    unknown_sender_policy: Literal["skip", "generic"] = "skip"
    """``skip``: unbekannte Absender überspringen; ``generic``: ganze Mail als ein Eintrag."""
    unknown_source_name: str = "email_unknown"
    generic_link_texts: list[str] = [
        "projekt ansehen",
        "zum projekt",
        "details",
        "mehr erfahren",
        "mehr",
        "jetzt bewerben",
        "view project",
    ]
    """Linktexte, die nicht als Projekttitel taugen (case-insensitiv)."""
    profiles: list[EmailSourceProfile] = Field(default_factory=_default_email_profiles)

    @model_validator(mode="after")
    def _unique_profile_names(self) -> EmailSourceSettings:
        names = [p.name for p in self.profiles]
        if len(names) != len(set(names)):
            raise ValueError("Profilnamen der E-Mail-Quellen müssen eindeutig sein")
        return self


class SourcesSettings(BaseModel):
    email: EmailSourceSettings = EmailSourceSettings()


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
    sources: SourcesSettings = SourcesSettings()

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

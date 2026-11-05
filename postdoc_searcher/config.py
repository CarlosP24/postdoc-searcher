"""Loading and validating the YAML inputs and settings."""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ROOT = Path(os.environ.get("POSTDOC_RADAR_ROOT", Path(__file__).resolve().parent.parent))
INPUTS_DIR = ROOT / "inputs"
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"


def _as_str(v: Any) -> Any:
    """YAML turns 2027-03-01 into a date; we want strings everywhere."""
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _as_list(v: Any) -> Any:
    if v is None or v == "":
        return []
    if isinstance(v, str):
        return [v]
    return v


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# ── inputs/pis.yaml ─────────────────────────────────────────────────────────

class PI(_Model):
    name: str
    institution: str | None = None
    country: str | None = None
    website: str | None = None
    jobs_page: str | None = None
    topics: list[str] = []
    priority: Literal["high", "normal", "low"] = "normal"
    notes: str | None = None

    _blank = field_validator("institution", "country", "website", "jobs_page", "notes", mode="before")(
        lambda v: (v or None) if not isinstance(v, str) or v.strip() else None
    )
    _lists = field_validator("topics", mode="before")(_as_list)

    @property
    def watch_url(self) -> str | None:
        return self.jobs_page or self.website

    def describe(self) -> str:
        bits = [self.name]
        if self.institution:
            bits.append(self.institution)
        if self.country:
            bits.append(self.country)
        s = " — ".join(bits)
        if self.topics:
            s += f" (topics: {', '.join(self.topics)})"
        return s


# ── inputs/topics.yaml ──────────────────────────────────────────────────────

class Topic(_Model):
    name: str
    keywords: list[str] = []
    board_queries: list[str] = []
    weight: float = 1.0

    _lists = field_validator("keywords", "board_queries", mode="before")(_as_list)

    @model_validator(mode="after")
    def _defaults(self) -> "Topic":
        if not self.keywords:
            self.keywords = [self.name]
        if not self.board_queries:
            self.board_queries = [self.name]
        self.weight = min(max(self.weight, 0.05), 1.0)
        return self


class TopicsFile(_Model):
    topics: list[Topic] = []
    field_keywords: list[str] = []
    exclude_title_patterns: list[str] = []


# ── inputs/me.yaml ──────────────────────────────────────────────────────────

class Residence(_Model):
    country: str
    start: str | None = Field(None, alias="from")
    end: str | None = Field(None, alias="to")

    _dates = field_validator("start", "end", mode="before")(_as_str)


class Preferences(_Model):
    regions_allowed: list[str] = []          # regions/countries always OK (empty = anywhere)
    exceptional_countries: list[str] = []    # OK only for outstanding matches
    exceptional_min_relevance: int = 80
    countries_preferred: list[str] = []
    countries_excluded: list[str] = []
    earliest_start: str | None = None
    notes: str = ""

    _lists = field_validator("regions_allowed", "exceptional_countries", "countries_preferred",
                             "countries_excluded", mode="before")(_as_list)
    _dates = field_validator("earliest_start", mode="before")(_as_str)


class Me(_Model):
    name: str = "the candidate"
    current_position: str = ""
    research_summary: str = ""
    skills: list[str] = []
    phd_institution: str | None = None
    phd_defense_date: str | None = None
    nationality: list[str] = []
    residence_history: list[Residence] = []
    preferences: Preferences = Preferences()

    _lists = field_validator("skills", "nationality", mode="before")(_as_list)
    _dates = field_validator("phd_defense_date", mode="before")(_as_str)


# ── config/settings.yaml ────────────────────────────────────────────────────

class DashboardSettings(_Model):
    title: str = "Postdoc Radar"
    url: str | None = None


class LLMSettings(_Model):
    enabled: bool = True
    backend: Literal["api", "claude-code"] = "api"
    claude_code_path: str | None = None
    max_claude_code_calls_per_run: int = 15
    model: str = "claude-opus-5-5"
    effort_search: str = "medium"
    effort_triage: str = "low"
    max_search_tasks_per_run: int = 4
    max_fellowship_checks_per_run: int = 2
    max_web_searches_per_task: int = 5
    max_page_fetches_per_task: int = 4
    max_lab_page_analyses_per_run: int = 6
    max_enrichments_per_run: int = 8
    triage_batch_size: int = 20
    max_triage_items_per_run: int = 160
    max_cost_per_run_usd: float = 2.0


class ScheduleSettings(_Model):
    pi_every_days: dict[str, float] = {"high": 3, "normal": 7, "low": 14}
    topic_every_days: float = 4
    fellowship_refresh_every_days: float = 14


class RelevanceSettings(_Model):
    store_threshold: int = 40
    notify_threshold: int = 65


class EmailSettings(_Model):
    enabled: bool = True
    subject_prefix: str = "[Postdoc Radar]"
    deadline_reminder_days: int = 21


class DataSettings(_Model):
    forget_closed_after_days: int = 180
    forget_seen_items_after_days: int = 120


class Feed(_Model):
    name: str
    url: str


class Toggle(_Model):
    enabled: bool = True


class InspireSettings(Toggle):
    query: str = "status:open and (arxiv_categories:cond-mat or arxiv_categories:quant-ph)"


class MastodonSettings(Toggle):
    instance: str = "https://mastodon.social"
    hashtags: list[str] = ["postdoc"]
    max_age_days: int = 14


class SourceSettings(_Model):
    keyword_feeds: list[Feed] = []
    feeds: list[Feed] = []
    jobs_ac_uk: Toggle = Toggle()
    inspire: InspireSettings = InspireSettings()
    mastodon: MastodonSettings = MastodonSettings()
    lab_pages: Toggle = Toggle()
    web_search: Toggle = Toggle()


class Settings(_Model):
    dashboard: DashboardSettings = DashboardSettings()
    llm: LLMSettings = LLMSettings()
    schedule: ScheduleSettings = ScheduleSettings()
    relevance: RelevanceSettings = RelevanceSettings()
    email: EmailSettings = EmailSettings()
    data: DataSettings = DataSettings()
    sources: SourceSettings = SourceSettings()


# ── config/fellowships.yaml ─────────────────────────────────────────────────

class HostRule(_Model):
    countries: list[str] = []
    groups: list[str] = []
    any: bool = False
    exclude_countries: list[str] = []
    exclude_groups: list[str] = []
    institution_keywords: list[str] = []


class MobilityRule(_Model):
    countries: list[str] | Literal["host"] = "host"
    max_months: float = 12
    window_months: float = 36


class Fellowship(_Model):
    id: str
    name: str
    funder: str = ""
    url: str
    host: HostRule = HostRule(any=True)
    mobility: MobilityRule | None = None
    exclude_phd_institution: bool = False
    nationality_required: list[str] = []
    nationality_excluded: list[str] = []
    min_years_since_phd: float | None = None
    max_years_since_phd: float | None = None
    eligibility: str = ""
    duration: str = ""
    funding: str = ""
    pi_role: str = ""
    typical_window: str = ""
    typical_open_month: int | None = None
    typical_deadline_month: int | None = None
    rolling: bool = False


class FellowshipCatalog(_Model):
    fellowships: list[Fellowship] = []
    country_groups: dict[str, list[str]] = {}


# ── Everything together ─────────────────────────────────────────────────────

class Config(_Model):
    me: Me
    pis: list[PI]
    topics: list[Topic]
    field_keywords: list[str] = []
    exclude_title_patterns: list[str]
    settings: Settings
    catalog: FellowshipCatalog


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_config(root: Path = ROOT) -> Config:
    inputs, config = root / "inputs", root / "config"
    topics_file = TopicsFile.model_validate(_read_yaml(inputs / "topics.yaml"))
    pis_raw = _read_yaml(inputs / "pis.yaml").get("pis") or []
    return Config(
        me=Me.model_validate(_read_yaml(inputs / "me.yaml")),
        pis=[PI.model_validate(p) for p in pis_raw if p and p.get("name")],
        topics=topics_file.topics,
        field_keywords=topics_file.field_keywords,
        exclude_title_patterns=topics_file.exclude_title_patterns,
        settings=Settings.model_validate(_read_yaml(config / "settings.yaml")),
        catalog=FellowshipCatalog.model_validate(_read_yaml(config / "fellowships.yaml")),
    )

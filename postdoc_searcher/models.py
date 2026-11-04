"""Data models: raw items from sources, AI findings, and stored opportunities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .util import canonical_url, short_hash, slug

Kind = Literal["position", "fellowship"]
Status = Literal["open", "upcoming", "rolling", "unknown", "closed"]


@dataclass
class RawItem:
    """Something a source returned (a feed entry, a search result, a post)."""

    source: str
    title: str
    url: str
    text: str = ""
    published: str | None = None
    institution: str | None = None
    location: str | None = None
    deadline: str | None = None
    salary: str | None = None
    trusted: bool = False          # curated source (skip keyword pre-filter)
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return short_hash(canonical_url(self.url) if self.url else f"{self.source}:{self.title}")


# ── Schemas Claude fills in ─────────────────────────────────────────────────
# All fields are required-but-nullable: this is what structured outputs and
# strict tool schemas handle best.

class Finding(BaseModel):
    """One opportunity as described by Claude."""

    model_config = ConfigDict(extra="ignore")

    title: str
    kind: Kind
    institution: str | None
    country: str | None
    location: str | None
    pi: str | None
    url: str
    call_opens: str | None
    deadline: str | None
    rolling: bool
    start_date: str | None
    duration: str | None
    salary: str | None
    eligibility: str | None
    summary: str
    topics: list[str]
    relevance: int
    relevance_reason: str


class SearchReport(BaseModel):
    opportunities: list[Finding]
    notes: str


class TriageDecision(BaseModel):
    """Claude's verdict on one job-board item (by index)."""

    index: int
    relevance: int
    relevance_reason: str
    kind: Kind
    institution: str | None
    country: str | None
    location: str | None
    pi: str | None
    call_opens: str | None
    deadline: str | None
    rolling: bool
    summary: str
    topics: list[str]


class TriageBatch(BaseModel):
    decisions: list[TriageDecision]


class PageAnalysis(BaseModel):
    """What Claude found on a lab page or a full job advert."""

    has_open_positions: bool
    opportunities: list[Finding]


class CallStatus(BaseModel):
    """Current state of a fellowship call, as found on the web."""

    model_config = ConfigDict(extra="ignore")

    status: Literal["open", "upcoming", "rolling", "closed", "unknown"]
    call_opens: str | None
    deadline: str | None
    dates_estimated: bool
    summary: str
    eligibility: str | None
    duration: str | None
    funding: str | None
    url: str | None
    sources: list[str]


# ── What we store ───────────────────────────────────────────────────────────

class Opportunity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    kind: Kind
    title: str
    institution: str | None = None
    country: str | None = None
    location: str | None = None
    pi: str | None = None                       # PI / supervisor named in the offer
    matched_pis: list[str] = []                 # PIs from YOUR list linked to it
    pi_notes: dict[str, str] = {}               # per-PI notes (eligibility, same institute…)
    topics: list[str] = []
    relevance: int = 0
    relevance_reason: str = ""
    summary: str = ""
    eligibility: str | None = None
    duration: str | None = None
    salary: str | None = None
    start_date: str | None = None
    call_opens: str | None = None
    deadline: str | None = None
    rolling: bool = False
    dates_estimated: bool = False
    dates_checked: str | None = None
    status: Status = "unknown"
    url: str
    sources: list[str] = []
    fellowship_id: str | None = None
    first_seen: str
    last_seen: str
    notified_status: str | None = None
    notified_on: str | None = None

    @property
    def dedupe_key(self) -> str:
        return f"{slug(self.title, 70)}|{slug(self.institution, 30)}"


def opportunity_id(url: str, title: str = "", institution: str | None = None) -> str:
    if url:
        return short_hash(canonical_url(url))
    return short_hash(f"{slug(title)}|{slug(institution)}")

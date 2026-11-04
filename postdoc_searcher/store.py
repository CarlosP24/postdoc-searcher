"""Persistent JSON store of opportunities + run state."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

from .config import PI
from .models import Opportunity
from .prefilter import same_institution, same_person
from .util import parse_date

log = logging.getLogger(__name__)

KEYWORD_REASON = "Keyword match"

_FILL_FIELDS = ("institution", "country", "location", "pi", "eligibility", "duration", "salary",
                "start_date", "call_opens", "deadline")


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def compute_status(o: Opportunity, ref: date) -> str:
    deadline = parse_date(o.deadline, end_of_month=True)
    opens = parse_date(o.call_opens)
    if deadline and deadline < ref:
        return "closed"
    if opens and opens > ref:
        return "upcoming"
    if o.rolling:
        return "rolling"
    if deadline or opens:
        return "open"
    return "unknown"


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.items: dict[str, Opportunity] = {}
        if path.exists():
            for raw in json.loads(path.read_text(encoding="utf-8")):
                o = Opportunity.model_validate(raw)
                self.items[o.id] = o
        self._by_key = {o.dedupe_key: o.id for o in self.items.values()}

    def all(self) -> list[Opportunity]:
        return list(self.items.values())

    def get(self, oid: str) -> Opportunity | None:
        return self.items.get(oid)

    def known_titles(self, *, pi: str | None = None, topic: str | None = None, limit: int = 40) -> list[str]:
        out = []
        for o in sorted(self.items.values(), key=lambda x: x.last_seen, reverse=True):
            if o.kind != "position" or o.status == "closed":
                continue
            if pi and pi not in o.matched_pis and not same_person(pi, o.pi):
                continue
            if topic and topic not in o.topics:
                continue
            out.append(f"- {o.title} — {o.institution or '?'} ({o.url})")
            if len(out) >= limit:
                break
        return out

    def upsert(self, new: Opportunity) -> tuple[Opportunity, bool]:
        """Insert or merge. Returns (stored, is_new)."""
        oid = new.id if new.id in self.items else self._by_key.get(new.dedupe_key)
        if oid is None:
            self.items[new.id] = new
            self._by_key[new.dedupe_key] = new.id
            return new, True
        old = self.items[oid]
        if new.kind == "fellowship" and new.fellowship_id:
            # catalog-derived: replace, but keep history fields
            merged = new.model_copy(update={
                "first_seen": old.first_seen, "notified_status": old.notified_status,
                "notified_on": old.notified_on,
            })
            self.items[oid] = merged
            return merged, False
        upd: dict = {"last_seen": max(old.last_seen, new.last_seen)}
        for f in _FILL_FIELDS:
            nv = getattr(new, f)
            if nv and (not getattr(old, f) or f in ("deadline", "call_opens")):
                upd[f] = nv
        if len(new.summary or "") > len(old.summary or ""):
            upd["summary"] = new.summary
        ai_replaces_keywords = (old.relevance_reason.startswith(KEYWORD_REASON)
                                and not new.relevance_reason.startswith(KEYWORD_REASON))
        if new.relevance > old.relevance or ai_replaces_keywords:
            upd["relevance"], upd["relevance_reason"] = new.relevance, new.relevance_reason
        upd["rolling"] = old.rolling or new.rolling
        upd["topics"] = sorted(set(old.topics) | set(new.topics))
        upd["matched_pis"] = sorted(set(old.matched_pis) | set(new.matched_pis))
        upd["pi_notes"] = {**old.pi_notes, **new.pi_notes}
        upd["sources"] = sorted(set(old.sources) | set(new.sources))
        merged = old.model_copy(update=upd)
        self.items[oid] = merged
        return merged, False

    def drop_keyword_only(self, ids: set[str]) -> int:
        """Remove items that were stored on keyword scoring alone and later rejected by Claude."""
        n = 0
        for oid in ids:
            o = self.items.get(oid)
            if o and o.relevance_reason.startswith(KEYWORD_REASON):
                del self.items[oid]
                n += 1
        self._by_key = {o.dedupe_key: o.id for o in self.items.values()}
        return n

    def refresh(self, ref: date, forget_closed_after_days: int) -> None:
        stale_cutoff = (ref - timedelta(days=90)).isoformat()
        forget_cutoff = ref - timedelta(days=forget_closed_after_days)
        for oid, o in list(self.items.items()):
            status = compute_status(o, ref)
            if status == "unknown" and o.kind == "position" and o.last_seen < stale_cutoff:
                status = "closed"  # no deadline and not seen for 3 months: assume gone
            o.status = status
            if status == "closed":
                d = parse_date(o.deadline, end_of_month=True) or parse_date(o.last_seen)
                if d and d < forget_cutoff and o.kind == "position":
                    del self.items[oid]
        self._by_key = {o.dedupe_key: o.id for o in self.items.values()}

    def save(self) -> None:
        order = {"open": 0, "rolling": 1, "upcoming": 2, "unknown": 3, "closed": 4}
        items = sorted(self.items.values(), key=lambda o: (order.get(o.status, 9), -o.relevance, o.id))
        _write_json(self.path, [o.model_dump() for o in items])


def associate_pis(o: Opportunity, pis: list[PI]) -> Opportunity:
    """Link a position to PIs on your list (by name, or same institution)."""
    if o.kind != "position":
        return o
    matched = set(o.matched_pis)
    notes = dict(o.pi_notes)
    for p in pis:
        if same_person(p.name, o.pi):
            matched.add(p.name)
            notes.setdefault(p.name, "★ PI of this offer")
        elif o.institution and p.institution and same_institution(p.institution, o.institution):
            matched.add(p.name)
            notes.setdefault(p.name, "same institution — ask whether you could join / collaborate")
    return o.model_copy(update={"matched_pis": sorted(matched), "pi_notes": notes})


class State:
    """Bookkeeping: seen items, lab-page hashes, task timestamps, fellowship checks, run log."""

    def __init__(self, path: Path):
        self.path = path
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.seen: dict[str, str] = data.get("seen", {})          # judged by Claude (or dropped by keywords)
        self.seen_kw: dict[str, str] = data.get("seen_kw", {})    # kept by keyword scoring only (no AI yet)
        self.lab_pages: dict[str, dict] = data.get("lab_pages", {})
        self.tasks: dict[str, str] = data.get("tasks", {})
        self.fellowships: dict[str, dict] = data.get("fellowships", {})
        self.runs: list[dict] = data.get("runs", [])

    def prune(self, ref: date, days: int) -> None:
        cutoff = (ref - timedelta(days=days)).isoformat()
        self.seen = {k: v for k, v in self.seen.items() if v >= cutoff}
        self.seen_kw = {k: v for k, v in self.seen_kw.items() if v >= cutoff and k not in self.seen}
        self.runs = self.runs[-90:]

    def save(self) -> None:
        _write_json(self.path, {
            "seen": self.seen, "seen_kw": self.seen_kw, "lab_pages": self.lab_pages, "tasks": self.tasks,
            "fellowships": self.fellowships, "runs": self.runs,
        })

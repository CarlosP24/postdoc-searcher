"""The daily run: collect → pre-filter → AI triage/search → merge → notify → publish."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

import anthropic

from . import fellowships, notify, site, sources
from .config import DATA_DIR, DOCS_DIR, PI, Config, Topic
from .http import Http
from .llm import BudgetExceeded, Claude
from .models import Finding, Opportunity, RawItem, TriageDecision, opportunity_id
from .prefilter import Match, Prefilter, same_person
from .sources import lab_pages
from .store import KEYWORD_REASON, State, Store, associate_pis
from .util import now_iso, page_text, parse_date, short_hash, today, truncate

log = logging.getLogger(__name__)

STEPS = ("feeds", "labs", "search", "fellowships")


@dataclass
class RunOptions:
    use_ai: bool = True
    web_search: bool = True
    email: bool = True
    dry_run: bool = False
    max_tasks: int | None = None
    only: set[str] | None = None


class Ctx:
    """Everything a run step needs, plus guarded AI calls."""

    def __init__(self, cfg: Config, opts: RunOptions, store: Store, state: State, http: Http):
        self.cfg, self.opts, self.store, self.state, self.http = cfg, opts, store, state, http
        self.ref = today()
        self.iso = self.ref.isoformat()
        self.stats: dict[str, Any] = {
            "date": now_iso(), "ai_enabled": False, "raw_items": 0, "candidates": 0, "triaged": 0,
            "lab_pages_changed": 0, "search_tasks": [], "fellowship_checks": [], "new_positions": 0,
            "emailed": 0, "errors": [],
        }
        self.claude: Claude | None = None
        self.ai_active = False
        self.rejected_ids: set[str] = set()
        if opts.use_ai and cfg.settings.llm.enabled:
            if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
                self.claude = Claude(cfg)
                self.ai_active = True
                self.stats["ai_enabled"] = True
            else:
                log.warning("ANTHROPIC_API_KEY not set — running without AI (keyword scoring only)")

    def error(self, msg: str) -> None:
        log.error(msg)
        self.stats["errors"].append(truncate(msg, 300))

    def ai(self, fn: Callable[[], Any], what: str) -> tuple[bool, Any]:
        """Run an AI call. Returns (completed, result). Stops all AI on budget/auth problems."""
        if not self.ai_active:
            return False, None
        try:
            return True, fn()
        except BudgetExceeded as exc:
            self.error(str(exc))
            self.ai_active = False
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            self.error(f"AI disabled for this run (credentials): {exc}")
            self.ai_active = False
        except anthropic.NotFoundError as exc:
            self.error(f"AI disabled for this run (model '{self.cfg.settings.llm.model}' not found?): {exc}")
            self.ai_active = False
        except anthropic.RateLimitError as exc:
            self.error(f"{what}: rate limited ({exc})")
        except anthropic.APIStatusError as exc:
            self.error(f"{what}: API error {exc.status_code}: {exc.message}")
        except anthropic.APIConnectionError as exc:
            self.error(f"{what}: connection error: {exc}")
        return False, None


# ── Converting AI output into stored opportunities ──────────────────────────

def clean_date(value: str | None) -> str | None:
    if not value:
        return None
    v = str(value).strip()[:10]
    if parse_date(v):
        return v if len(v) in (7, 10) else None
    return None


def _clamp(n: int) -> int:
    return max(0, min(100, int(n)))


def _topics(cfg: Config, names: list[str]) -> list[str]:
    known = {t.name.lower(): t.name for t in cfg.topics}
    out = []
    for n in names or []:
        k = n.lower().strip()
        hit = known.get(k) or next((v for kk, v in known.items() if k and (k in kk or kk in k)), None)
        if hit and hit not in out:
            out.append(hit)
    return out


def from_finding(ctx: Ctx, f: Finding, source: str) -> Opportunity | None:
    if not f.url or not f.url.startswith("http"):
        return None
    summary = f.summary
    for label, raw, clean in (("deadline", f.deadline, clean_date(f.deadline)),
                              ("opens", f.call_opens, clean_date(f.call_opens))):
        if raw and not clean:
            summary += f" ({label}: {raw})"
    return Opportunity(
        id=opportunity_id(f.url, f.title, f.institution), kind=f.kind, title=f.title.strip(),
        institution=f.institution, country=f.country, location=f.location, pi=f.pi,
        topics=_topics(ctx.cfg, f.topics), relevance=_clamp(f.relevance), relevance_reason=f.relevance_reason,
        summary=summary, eligibility=f.eligibility, duration=f.duration, salary=f.salary,
        start_date=f.start_date, call_opens=clean_date(f.call_opens), deadline=clean_date(f.deadline),
        rolling=f.rolling, url=f.url, sources=[source], first_seen=ctx.iso, last_seen=ctx.iso,
    )


def from_triage(ctx: Ctx, m: Match, d: TriageDecision) -> Opportunity:
    it = m.item
    return Opportunity(
        id=opportunity_id(it.url, it.title, it.institution), kind=d.kind, title=it.title,
        institution=d.institution or it.institution, country=d.country, location=d.location or it.location,
        pi=d.pi, topics=sorted(set(_topics(ctx.cfg, d.topics)) | set(m.topics)),
        relevance=_clamp(d.relevance), relevance_reason=d.relevance_reason, summary=d.summary,
        salary=it.salary, call_opens=clean_date(d.call_opens), deadline=clean_date(d.deadline) or it.deadline,
        rolling=d.rolling, url=it.url, sources=[it.source], first_seen=ctx.iso, last_seen=ctx.iso,
    )


def from_keywords(ctx: Ctx, pf: Prefilter, m: Match) -> Opportunity:
    it = m.item
    why = []
    if m.topics:
        why.append("topics: " + ", ".join(m.topics))
    if m.pis:
        why.append("mentions: " + ", ".join(m.pis))
    return Opportunity(
        id=opportunity_id(it.url, it.title, it.institution), kind="position", title=it.title,
        institution=it.institution, location=it.location, topics=m.topics,
        relevance=pf.heuristic_relevance(m), relevance_reason=f"{KEYWORD_REASON} (" + "; ".join(why) + ")",
        summary=truncate(it.text, 500), salary=it.salary, deadline=it.deadline, url=it.url,
        sources=[it.source], first_seen=ctx.iso, last_seen=ctx.iso, matched_pis=m.pis,
    )


# ── Steps ───────────────────────────────────────────────────────────────────

def step_feeds(ctx: Ctx, pf: Prefilter) -> list[Opportunity]:
    raw = sources.collect_all(ctx.cfg, ctx.http)
    ctx.stats["raw_items"] = len(raw)
    # Items kept by keyword scoring alone (seen_kw) get a proper Claude verdict once AI is available.
    done = ctx.state.seen if ctx.ai_active else {**ctx.state.seen, **ctx.state.seen_kw}
    fresh = [r for r in raw if r.key not in done]
    matches: list[Match] = []
    for r in fresh:
        m = pf.score(r)
        if m:
            matches.append(m)
        elif not ctx.ai_active and pf.score(r, broad=True):
            ctx.state.seen_kw[r.key] = ctx.iso  # let Claude judge it once AI is available
        else:
            ctx.state.seen[r.key] = ctx.iso
    matches.sort(key=lambda m: -m.score)
    ctx.stats["candidates"] = len(matches)
    log.info("%d new items, %d passed the keyword pre-filter", len(fresh), len(matches))

    out: list[Opportunity] = []
    s = ctx.cfg.settings
    if not ctx.ai_active:
        for m in matches:
            ctx.state.seen_kw[m.item.key] = ctx.iso
            out.append(from_keywords(ctx, pf, m))
        return out

    todo = matches[: s.llm.max_triage_items_per_run]
    bs = max(1, s.llm.triage_batch_size)
    for i in range(0, len(todo), bs):
        batch = todo[i:i + bs]
        ok, decisions = ctx.ai(lambda: ctx.claude.triage(
            [(j, m.item, m.topics, m.pis) for j, m in enumerate(batch)], s.relevance.store_threshold),
            "triage")
        if not ok:
            if not ctx.ai_active:  # budget/auth: keep the rest for keyword scoring
                for m in matches[i:]:
                    ctx.state.seen_kw[m.item.key] = ctx.iso
                    out.append(from_keywords(ctx, pf, m))
                return out
            continue  # transient error: these items stay unseen and are retried next run
        by_index = {d.index: d for d in decisions}
        for j, m in enumerate(batch):
            ctx.state.seen[m.item.key] = ctx.iso
            ctx.state.seen_kw.pop(m.item.key, None)
            d = by_index.get(j)
            if d and d.relevance >= s.relevance.store_threshold:
                out.append(from_triage(ctx, m, d))
            else:
                ctx.rejected_ids.add(opportunity_id(m.item.url, m.item.title, m.item.institution))
        ctx.stats["triaged"] += len(batch)
    if len(matches) > len(todo):
        log.info("%d candidates left for the next run (max_triage_items_per_run)", len(matches) - len(todo))
    enrich(ctx, out)
    return out


def enrich(ctx: Ctx, opps: list[Opportunity]) -> None:
    """Read the full advert of the most promising items to get deadline + conditions."""
    s = ctx.cfg.settings
    targets = sorted(
        (o for o in opps if o.relevance >= s.relevance.notify_threshold and (not o.deadline or len(o.summary) < 250)),
        key=lambda o: -o.relevance,
    )[: s.llm.max_enrichments_per_run]
    for o in targets:
        if not ctx.ai_active:
            return
        html = ctx.http.text(o.url)
        if not html:
            continue
        text = page_text(html)
        if len(text) < 300:
            continue
        ok, res = ctx.ai(lambda: ctx.claude.analyze_page(
            o.url, text, f"This is the full job advert for: {o.title} ({o.institution or 'unknown institution'}). "
                         "Extract its details as a single Finding."), f"enrich {o.url}")
        if not ok or not res or not res.opportunities:
            continue
        f = res.opportunities[0]
        o.deadline = clean_date(f.deadline) or o.deadline
        o.call_opens = clean_date(f.call_opens) or o.call_opens
        for attr in ("summary", "salary", "duration", "start_date", "eligibility", "pi", "country", "location"):
            val = getattr(f, attr)
            if val:
                setattr(o, attr, val)
        o.rolling = o.rolling or f.rolling
        o.relevance, o.relevance_reason = _clamp(f.relevance), f.relevance_reason
        o.sources = sorted(set(o.sources) | {"full advert"})


def step_labs(ctx: Ctx) -> list[Opportunity]:
    changes = lab_pages.check(ctx.http, ctx.cfg.pis, ctx.state.lab_pages)
    ctx.stats["lab_pages_changed"] = len(changes)
    out: list[Opportunity] = []
    analyses = 0
    cap = ctx.cfg.settings.llm.max_lab_page_analyses_per_run
    for ch in changes:
        if not ch.mentions_opening:
            continue
        res = None
        if ctx.ai_active and analyses < cap:
            analyses += 1
            ok, res = ctx.ai(lambda: ctx.claude.analyze_page(
                ch.url, ch.text, f"This is the web page of the research group of {ch.pi.describe()}."),
                f"lab page {ch.pi.name}")
            if ok and res is not None:
                for f in res.opportunities:
                    o = from_finding(ctx, f, f"Group page ({ch.pi.name})")
                    if o:
                        o.pi = o.pi or ch.pi.name
                        o.matched_pis = [ch.pi.name]
                        o.pi_notes = {ch.pi.name: "posted on the PI's group page"}
                        out.append(o)
                continue
        # No AI (or AI failed): flag the change itself.
        digest = ctx.state.lab_pages.get(ch.url, {}).get("hash", "")
        verb = "first check" if ch.first_check else "page changed"
        out.append(Opportunity(
            id=f"lab-{short_hash(ch.url + digest)}", kind="position",
            title=f"Possible opening — {ch.pi.name}'s group page ({verb} {ctx.iso})",
            institution=ch.pi.institution, country=ch.pi.country, pi=ch.pi.name, matched_pis=[ch.pi.name],
            pi_notes={ch.pi.name: "group page mentions openings"}, relevance=70,
            relevance_reason="Your PI's group page mentions openings — have a look.",
            summary=f"…{ch.snippet.strip()}…", url=ch.url, sources=["group page watch"],
            first_seen=ctx.iso, last_seen=ctx.iso,
        ))
    return out


def due_search_tasks(cfg: Config, state: State, ref: date) -> list[tuple[float, str, PI | Topic]]:
    sched = cfg.settings.schedule
    tasks: list[tuple[float, str, PI | Topic]] = []

    def age(key: str) -> float:
        last = state.tasks.get(key)
        return (ref - date.fromisoformat(last[:10])).days if last else 10_000

    for p in cfg.pis:
        interval = float(sched.pi_every_days.get(p.priority, 7))
        a = age(f"pi:{p.name}")
        if a >= interval:
            tasks.append((a / interval + (0.5 if p.priority == "high" else 0), f"pi:{p.name}", p))
    for t in cfg.topics:
        interval = sched.topic_every_days / t.weight
        a = age(f"topic:{t.name}")
        if a >= interval:
            tasks.append((a / interval, f"topic:{t.name}", t))
    tasks.sort(key=lambda x: -x[0])
    return tasks


def step_search(ctx: Ctx) -> list[Opportunity]:
    cap = ctx.opts.max_tasks if ctx.opts.max_tasks is not None else ctx.cfg.settings.llm.max_search_tasks_per_run
    out: list[Opportunity] = []
    for _, key, obj in due_search_tasks(ctx.cfg, ctx.state, ctx.ref)[:cap]:
        if not ctx.ai_active:
            break
        log.info("web search task: %s", key)
        if isinstance(obj, PI):
            ok, rep = ctx.ai(lambda: ctx.claude.search_pi(obj, ctx.store.known_titles(pi=obj.name)), key)
        else:
            ok, rep = ctx.ai(lambda: ctx.claude.search_topic(obj, ctx.store.known_titles(topic=obj.name)), key)
        if not ok:
            continue
        ctx.state.tasks[key] = now_iso()
        found = rep.opportunities if rep else []
        ctx.stats["search_tasks"].append({"task": key, "found": len(found),
                                          "notes": truncate(rep.notes, 300) if rep else "no report"})
        label = f"Web search ({key.split(':', 1)[0]}: {key.split(':', 1)[1]})"
        for f in found:
            o = from_finding(ctx, f, label)
            if not o:
                continue
            if isinstance(obj, PI) and (same_person(f.pi, obj.name) or not f.pi):
                o.matched_pis = sorted(set(o.matched_pis) | {obj.name})
                o.pi_notes = {obj.name: "★ PI of this offer" if same_person(f.pi, obj.name)
                              else "found while searching this PI's institution"}
            out.append(o)
    return out


def step_fellowship_checks(ctx: Ctx) -> None:
    cfg = ctx.cfg
    cap = cfg.settings.llm.max_fellowship_checks_per_run
    interval = cfg.settings.schedule.fellowship_refresh_every_days
    groups = cfg.catalog.country_groups
    cands = []
    for f in cfg.catalog.fellowships:
        has_host = any(fellowships.host_allows(f, p, groups) for p in cfg.pis)
        v = ctx.state.fellowships.get(f.id) or {}
        last = v.get("checked")
        a = (ctx.ref - date.fromisoformat(last[:10])).days if last else 10_000
        eff = interval if has_host else interval * 3
        passed = (d := parse_date(v.get("deadline"), end_of_month=True)) is not None and d < ctx.ref
        if a >= eff or (passed and a >= 2):
            cands.append((has_host, a / eff, f))
    cands.sort(key=lambda c: (not c[0], -c[1]))
    for _, _, f in cands[:cap]:
        if not ctx.ai_active:
            return
        log.info("fellowship check: %s", f.id)
        ok, res = ctx.ai(lambda: ctx.claude.fellowship_status(f), f"fellowship {f.id}")
        if not ok:
            continue
        entry = {"checked": now_iso()}
        if res:
            entry.update(res.model_dump())
            entry["call_opens"] = clean_date(res.call_opens)
            entry["deadline"] = clean_date(res.deadline)
            if not (res.url or "").startswith("http"):
                entry["url"] = None
        ctx.state.fellowships[f.id] = entry
        ctx.stats["fellowship_checks"].append({"id": f.id, "status": res.status if res else "no report",
                                               "deadline": entry.get("deadline")})


# ── Main entry ──────────────────────────────────────────────────────────────

def run(cfg: Config, opts: RunOptions, data_dir: Path = DATA_DIR, docs_dir: Path = DOCS_DIR) -> dict:
    store = Store(data_dir / "opportunities.json")
    state = State(data_dir / "state.json")
    http = Http()
    ctx = Ctx(cfg, opts, store, state, http)
    pf = Prefilter(cfg.topics, cfg.pis, cfg.exclude_title_patterns, cfg.field_keywords, broad=ctx.ai_active)
    want = (lambda step: opts.only is None or step in opts.only)
    s = cfg.settings
    found: list[Opportunity] = []

    if want("feeds"):
        found += step_feeds(ctx, pf)
    if want("labs") and s.sources.lab_pages.enabled:
        found += step_labs(ctx)
    if want("search") and opts.web_search and s.sources.web_search.enabled and ctx.ai_active:
        found += step_search(ctx)
    if want("fellowships"):
        if ctx.ai_active:
            step_fellowship_checks(ctx)
        first_seen = {o.id: o.first_seen for o in store.all()}
        found += fellowships.build(cfg, state.fellowships, ctx.ref, first_seen)

    for o in found:
        o = associate_pis(o, cfg.pis)
        if o.kind == "position" and o.relevance < s.relevance.store_threshold:
            continue
        _, is_new = store.upsert(o)
        if is_new and o.kind == "position":
            ctx.stats["new_positions"] += 1
    if ctx.rejected_ids:
        dropped = store.drop_keyword_only(ctx.rejected_ids)
        if dropped:
            log.info("removed %d keyword-only items that Claude judged irrelevant", dropped)
    store.refresh(ctx.ref, s.data.forget_closed_after_days)

    # Notifications
    events = notify.pending_events(store.all(), s.relevance.notify_threshold)
    if events:
        log.info("%d opportunities to notify", len(events))
    if events and opts.email and s.email.enabled:
        if not notify.smtp_configured():
            log.warning("email not sent: set SMTP_USER, SMTP_PASSWORD and EMAIL_TO (see README)")
        else:
            reminders = notify.upcoming_deadlines(store.all(), s.relevance.notify_threshold,
                                                  s.email.deadline_reminder_days, ctx.ref,
                                                  {o.id for _, o in events})
            msg = notify.compose(events, reminders, s, ctx.ref)
            if opts.dry_run:
                log.info("dry run — would send: %s", msg["Subject"])
            else:
                try:
                    notify.send(msg)
                    for _, o in events:
                        o.notified_status, o.notified_on = o.status, ctx.iso
                    ctx.stats["emailed"] = len(events)
                except Exception as exc:  # keep events pending; retry next run
                    ctx.error(f"email failed: {exc}")

    if ctx.claude:
        ctx.stats["ai"] = ctx.claude.usage.as_dict()
        ctx.stats["ai"]["refusals"] = ctx.claude.refusals
    ctx.stats["errors"] += [truncate(e, 200) for e in http.errors[:25]]
    ctx.stats["open_total"] = sum(1 for o in store.all() if o.status in ("open", "rolling", "unknown"))
    state.runs.append(ctx.stats)
    state.prune(ctx.ref, s.data.forget_seen_items_after_days)
    if opts.dry_run:
        log.info("dry run — nothing saved")
    else:
        store.save()
        state.save()
        site.build(cfg, store, state, docs_dir)
    return ctx.stats

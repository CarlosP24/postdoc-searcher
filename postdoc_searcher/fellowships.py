"""Fellowship catalog → opportunities, with host-PI matching and eligibility checks."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from .config import PI, Config, Fellowship, Me
from .models import Opportunity
from .prefilter import same_institution
from .util import months_between, norm, parse_date, strip_accents

# Common spellings → canonical country name
_ALIASES = {
    "uk": "united kingdom", "u.k.": "united kingdom", "great britain": "united kingdom", "britain": "united kingdom",
    "england": "united kingdom", "scotland": "united kingdom", "wales": "united kingdom",
    "northern ireland": "united kingdom",
    "usa": "united states", "us": "united states", "u.s.": "united states", "u.s.a.": "united states",
    "united states of america": "united states", "america": "united states",
    "czech republic": "czechia", "the netherlands": "netherlands", "holland": "netherlands",
    "turkey": "turkiye", "republic of korea": "south korea", "korea": "south korea",
    "espana": "spain", "deutschland": "germany", "danmark": "denmark", "schweiz": "switzerland",
    "suisse": "switzerland", "italia": "italy", "osterreich": "austria",
}

_NATIONALITY = {
    "spanish": "spain", "german": "germany", "british": "united kingdom", "japanese": "japan",
    "french": "france", "italian": "italy", "portuguese": "portugal", "dutch": "netherlands",
    "danish": "denmark", "swiss": "switzerland", "american": "united states", "canadian": "canada",
    "austrian": "austria", "belgian": "belgium", "swedish": "sweden", "finnish": "finland",
    "norwegian": "norway", "irish": "ireland", "polish": "poland", "greek": "greece",
}


def country_key(name: str | None) -> str:
    n = norm(strip_accents(name or "")).strip(" .")
    return _ALIASES.get(n, n)


def nationality_key(name: str) -> str:
    n = norm(name)
    return _NATIONALITY.get(n, country_key(n))


@dataclass
class HostCheck:
    pi: PI
    eligible: bool | None  # None = can't tell (missing data)
    note: str


def _expand(groups: dict[str, list[str]], names: list[str], group_names: list[str]) -> set[str]:
    out = {country_key(c) for c in names}
    for g in group_names:
        out |= {country_key(c) for c in groups.get(g, [])}
    return out


def host_allows(f: Fellowship, pi: PI, groups: dict[str, list[str]]) -> bool:
    c = country_key(pi.country)
    if not c:
        return False
    rule = f.host
    allowed = _expand(groups, rule.countries, rule.groups)
    excluded = _expand(groups, rule.exclude_countries, rule.exclude_groups)
    if c in excluded:
        return False
    if not rule.any and c not in allowed:
        return False
    if rule.institution_keywords:
        inst = norm(pi.institution)
        if not any(norm(k) in inst for k in rule.institution_keywords):
            return False
    return True


def months_resided(me: Me, countries: set[str], ref: date, window_months: float) -> float | None:
    """Months spent in `countries` during the window before `ref`. None if unknown."""
    if not me.residence_history:
        return None
    start_window = date.fromordinal(max(1, ref.toordinal() - int(window_months * 30.4375)))
    total = 0.0
    for r in me.residence_history:
        if country_key(r.country) not in countries:
            continue
        start = parse_date(r.start)
        end = ref if (not r.end or norm(r.end) in {"present", "now", "today"}) else parse_date(r.end, end_of_month=True)
        if start is None or end is None:
            return None
        lo, hi = max(start, start_window), min(end, ref)
        if hi > lo:
            total += months_between(lo, hi)
    return total


def check_candidate(f: Fellowship, me: Me, pi: PI, ref: date) -> tuple[bool | None, list[str]]:
    """Is the candidate eligible for `f` hosted by `pi`? Returns (eligible, notes)."""
    notes: list[str] = []
    eligible: bool | None = True

    def unknown(note: str) -> None:
        nonlocal eligible
        if eligible is True:
            eligible = None
        notes.append(note)

    nats = {nationality_key(n) for n in me.nationality}
    if f.nationality_required:
        req = {nationality_key(n) for n in f.nationality_required}
        if not nats:
            unknown("nationality not given in me.yaml")
        elif not (nats & req):
            return False, [f"requires nationality: {', '.join(f.nationality_required)}"]
    if f.nationality_excluded and nats:
        exc = {nationality_key(n) for n in f.nationality_excluded}
        if nats <= exc:
            return False, [f"not open to {', '.join(f.nationality_excluded)} nationals"]

    phd = parse_date(me.phd_defense_date)
    if f.max_years_since_phd is not None or f.min_years_since_phd is not None:
        if phd is None:
            unknown("PhD date not given in me.yaml")
        else:
            years = (ref - phd).days / 365.25
            if f.max_years_since_phd is not None and years > f.max_years_since_phd:
                return False, [f"more than {f.max_years_since_phd:g} years since PhD"]
            if f.min_years_since_phd is not None and years < f.min_years_since_phd:
                notes.append(f"needs ≥ {f.min_years_since_phd:g} years since PhD (eligible from "
                             f"~{phd.year + int(f.min_years_since_phd)})")
                eligible = False

    if f.mobility:
        countries = ({country_key(pi.country)} if f.mobility.countries == "host"
                     else {country_key(c) for c in f.mobility.countries})
        months = months_resided(me, countries, ref, f.mobility.window_months)
        if months is None:
            unknown("mobility rule: fill residence_history dates in me.yaml")
        elif months > f.mobility.max_months:
            return False, [f"mobility rule: ~{months:.0f} months in {', '.join(sorted(countries)).title()} "
                           f"in the last {f.mobility.window_months:g} (max {f.mobility.max_months:g})"]

    if f.exclude_phd_institution and me.phd_institution:
        if same_institution(me.phd_institution, pi.institution):
            return False, ["host must differ from your PhD institution"]

    return eligible, notes


def estimate_window(f: Fellowship, ref: date) -> tuple[date | None, date | None]:
    """Next (or current) call window from typical months."""
    om, dm = f.typical_open_month, f.typical_deadline_month
    if not dm:
        return None, None
    for y in (ref.year - 1, ref.year, ref.year + 1):
        dy = y if (om is None or dm >= om) else y + 1
        deadline = date(dy, dm, calendar.monthrange(dy, dm)[1])
        opens = date(y, om, 1) if om else None
        if deadline >= ref:
            return opens, deadline
    return None, None


def build(cfg: Config, verified: dict[str, dict], ref: date, first_seen: dict[str, str]) -> list[Opportunity]:
    """One Opportunity per catalogued fellowship.

    `verified` holds the latest web-checked call info per fellowship id.
    """
    groups = cfg.catalog.country_groups
    out = []
    for f in cfg.catalog.fellowships:
        v = verified.get(f.id) or {}
        if not v.get("status"):  # a check that produced no report
            v = {}
        matched, notes, eligible_count = [], {}, 0
        for pi in cfg.pis:
            if not host_allows(f, pi, groups):
                continue
            ok, why = check_candidate(f, cfg.me, pi, ref)
            if ok is False:
                notes[pi.name] = "✗ " + "; ".join(why)
                continue
            matched.append(pi.name)
            eligible_count += 1
            notes[pi.name] = ("✓ eligible" if ok else "? check: " + "; ".join(why)) + (
                f" — {f.pi_role}" if f.pi_role else "")

        # Dates: verified web info first, else typical-month estimate.
        opens, deadline, estimated = None, None, True
        v_deadline = parse_date(v.get("deadline"), end_of_month=True)
        if v and (v_deadline is None or v_deadline >= ref or v.get("status") == "rolling"):
            opens, deadline = v.get("call_opens"), v.get("deadline")
            estimated = bool(v.get("dates_estimated"))
        if not deadline and not f.rolling:
            eo, ed = estimate_window(f, ref)
            if ed:
                opens, deadline, estimated = (eo.isoformat() if eo else None), ed.isoformat(), True
        rolling = f.rolling or v.get("status") == "rolling"

        relevance = 30
        if matched:
            relevance = min(92, 66 + 8 * min(eligible_count, 3))
        elif notes:
            relevance = 20
        preferred = {country_key(c) for c in cfg.me.preferences.countries_preferred}
        if preferred and any(country_key(p.country) in preferred for p in cfg.pis if p.name in matched):
            relevance = min(95, relevance + 5)

        if matched:
            reason = f"Could be hosted by {', '.join(matched[:4])}{'…' if len(matched) > 4 else ''} from your PI list."
        elif notes:
            reason = "Your listed PIs in eligible host countries are excluded by the rules (see notes)."
        else:
            reason = "None of your listed PIs is in an eligible host country/institution."

        summary = v.get("summary") or " ".join(
            x for x in [f.eligibility, f"Duration: {f.duration}." if f.duration else "",
                        f"Funding: {f.funding}" if f.funding else "", f"Call: {f.typical_window}" if f.typical_window else ""]
            if x)
        out.append(Opportunity(
            id=f"fellowship-{f.id}",
            kind="fellowship",
            title=f.name,
            institution=f.funder,
            country=_host_label(f),
            matched_pis=matched,
            pi_notes=notes,
            relevance=relevance,
            relevance_reason=reason,
            summary=summary,
            eligibility=v.get("eligibility") or f.eligibility or None,
            duration=v.get("duration") or f.duration or None,
            salary=v.get("funding") or f.funding or None,
            call_opens=opens,
            deadline=deadline,
            rolling=rolling,
            dates_estimated=estimated and not rolling,
            dates_checked=v.get("checked"),
            url=v.get("url") or f.url,
            sources=["fellowship catalog"] + (["web check"] if v else []),
            fellowship_id=f.id,
            first_seen=first_seen.get(f"fellowship-{f.id}", ref.isoformat()),
            last_seen=ref.isoformat(),
        ))
    return out


def _host_label(f: Fellowship) -> str:
    r = f.host
    if r.any and not (r.exclude_countries or r.exclude_groups):
        return "Any country"
    if r.any:
        return "Outside " + ", ".join(r.exclude_groups + r.exclude_countries)
    parts = r.groups + r.countries
    label = ", ".join(parts)
    if r.institution_keywords:
        label += " (specific institutions)"
    return label.replace("HE_ASSOCIATED", "Horizon-Europe associated")

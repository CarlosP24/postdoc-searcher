"""Small text/date helpers shared by the whole package."""

from __future__ import annotations

import calendar
import hashlib
import re
import unicodedata
from datetime import date, datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

_WS = re.compile(r"\s+")
_TRACKING_PARAMS = {
    "trackid", "ref", "refid", "src", "source", "fbclid", "gclid", "trk", "trackingid",
    "mc_cid", "mc_eid", "utm", "campaign", "si",
}


def today() -> date:
    return datetime.now(timezone.utc).date()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def norm(text: str | None) -> str:
    """Lower-case, accent-free, single-spaced text for matching."""
    if not text:
        return ""
    return _WS.sub(" ", strip_accents(text).lower()).strip()


def slug(text: str | None, limit: int = 80) -> str:
    return re.sub(r"[^a-z0-9]+", "-", norm(text)).strip("-")[:limit]


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    for br in soup.find_all(["br", "p", "li", "div", "h1", "h2", "h3", "h4", "tr"]):
        br.append("\n")
    lines = [_WS.sub(" ", ln).strip() for ln in soup.get_text().splitlines()]
    return "\n".join(ln for ln in lines if ln)


def page_text(html: str) -> str:
    """Visible text of a full web page, without navigation/header/footer chrome."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    return html_to_text(str(main))


def canonical_url(url: str) -> str:
    url = (url or "").strip()
    parts = urlsplit(url)
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(((parts.scheme or "https").lower(), host, path, urlencode(sorted(query)), ""))


def short_hash(text: str, n: int = 12) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]


def parse_date(value: str | date | None, *, end_of_month: bool = False) -> date | None:
    """Parse YYYY-MM-DD or YYYY-MM (→ first/last day of month)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?", s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", s)
    if m:
        y, mo = int(m[1]), int(m[2])
        if not 1 <= mo <= 12:
            return None
        day = calendar.monthrange(y, mo)[1] if end_of_month else 1
        return date(y, mo, day)
    return None


def months_between(a: date, b: date) -> float:
    return (b - a).days / 30.4375


_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_name) if m})


def parse_day_month(text: str, reference: date) -> date | None:
    """Parse '14 Dec' / 'December 14' as the next such date on/after `reference` minus 31 days."""
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3,9})", text) or None
    day = mon = None
    if m and m[2].lower() in _MONTHS:
        day, mon = int(m[1]), _MONTHS[m[2].lower()]
    else:
        m = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2})", text)
        if m and m[1].lower() in _MONTHS:
            day, mon = int(m[2]), _MONTHS[m[1].lower()]
    if day is None:
        return None
    for year in (reference.year, reference.year + 1):
        try:
            d = date(year, mon, day)
        except ValueError:
            return None
        if (d - reference).days >= -31:
            return d
    return None


def truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

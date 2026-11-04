"""jobs.ac.uk keyword search (HTML results page)."""

from __future__ import annotations

import logging
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from ..http import Http
from ..models import RawItem
from ..util import parse_day_month, today

log = logging.getLogger(__name__)

BASE = "https://www.jobs.ac.uk"


def parse_results(html: str) -> list[RawItem]:
    soup = BeautifulSoup(html, "html.parser")
    items = []
    ref = today()
    for res in soup.select(".j-search-result__result"):
        link = res.select_one('a[href^="/job/"]')
        if not link:
            continue
        title = link.get_text(" ", strip=True)
        employer = res.select_one(".j-search-result__employer")
        dept = res.select_one(".j-search-result__department")
        info = res.select_one(".j-search-result__info")
        closes = res.select_one(".j-search-result__date--blue")
        deadline = parse_day_month(closes.get_text(" ", strip=True), ref) if closes else None
        text_bits = [b.get_text(" ", strip=True) for b in res.select(".j-search-result__text > div")]
        location = next((b.split(":", 1)[1].strip() for b in text_bits if b.startswith("Location:")), None)
        items.append(RawItem(
            source="jobs.ac.uk",
            title=title,
            url=urljoin(BASE, link["href"]),
            text="\n".join(text_bits),
            institution=employer.get_text(" ", strip=True) if employer else None,
            location=location,
            salary=info.get_text(" ", strip=True).replace("Salary:", "").strip() if info else None,
            deadline=deadline.isoformat() if deadline else None,
            extra={"department": dept.get_text(" ", strip=True) if dept else None},
        ))
    return items


def collect(http: Http, queries: list[str]) -> list[RawItem]:
    out: list[RawItem] = []
    for q in queries:
        html = http.text(f"{BASE}/search/?keywords={quote_plus(q)}&sortOrder=1")
        if html:
            got = parse_results(html)
            log.info("%-28s %-35s %3d items", "jobs.ac.uk", q[:35], len(got))
            out.extend(got)
    return out

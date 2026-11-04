"""RSS/Atom job feeds (Madgex boards such as Nature Careers, jobRxiv, …)."""

from __future__ import annotations

import logging
from urllib.parse import quote_plus

import feedparser

from ..config import Feed
from ..http import Http
from ..models import RawItem
from ..util import html_to_text

log = logging.getLogger(__name__)


def _madgex_fields(title: str, summary: str) -> tuple[str, str | None, str | None, str | None]:
    """Madgex feeds use 'Employer: Job title' and 'Salary:\\n\\nEmployer:\\n…\\nLocation'."""
    institution = None
    if ": " in title:
        head, tail = title.split(": ", 1)
        if len(head) < 120 and tail:
            institution, title = head.strip(), tail.strip()
    lines = [ln.strip() for ln in summary.splitlines() if ln.strip()]
    salary = None
    location = None
    if lines and lines[0].endswith(":"):
        salary = lines[0].rstrip(":").strip() or None
    if len(lines) >= 3 and len(lines[-1]) < 80 and not lines[-1].endswith("."):
        location = lines[-1]
    return title, institution, salary, location


def parse_feed(name: str, xml: str) -> list[RawItem]:
    parsed = feedparser.parse(xml)
    items = []
    for e in parsed.entries:
        link = e.get("link") or ""
        title = html_to_text(e.get("title", "")).strip()
        summary = html_to_text(e.get("summary", "") or e.get("description", ""))
        if not link or not title:
            continue
        title, institution, salary, location = _madgex_fields(title, summary)
        items.append(RawItem(
            source=name, title=title, url=link, text=summary,
            published=e.get("published") or e.get("updated"),
            institution=institution, salary=salary, location=location,
        ))
    return items


def collect_keyword_feeds(http: Http, feeds: list[Feed], queries: list[str]) -> list[RawItem]:
    out: list[RawItem] = []
    for feed in feeds:
        for q in queries:
            url = feed.url.format(query=quote_plus(q))
            xml = http.text(url)
            if xml:
                got = parse_feed(feed.name, xml)
                log.info("%-28s %-35s %3d items", feed.name, q[:35], len(got))
                out.extend(got)
    return out


def collect_feeds(http: Http, feeds: list[Feed]) -> list[RawItem]:
    out: list[RawItem] = []
    for feed in feeds:
        xml = http.text(feed.url)
        if xml:
            got = parse_feed(feed.name, xml)
            log.info("%-28s %3d items", feed.name, len(got))
            out.extend(got)
    return out

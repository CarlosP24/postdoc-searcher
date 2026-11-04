"""INSPIRE-HEP jobs API — curated theory postdocs with deadlines."""

from __future__ import annotations

import logging
from urllib.parse import quote

from ..http import Http
from ..models import RawItem
from ..util import html_to_text

log = logging.getLogger(__name__)

API = "https://inspirehep.net/api/jobs"


def collect(http: Http, query: str, size: int = 100) -> list[RawItem]:
    data = http.json(f"{API}?q={quote(query)}&sort=mostrecent&size={size}")
    if not data:
        return []
    items = []
    for hit in data.get("hits", {}).get("hits", []):
        m = hit.get("metadata", {})
        ranks = m.get("ranks") or []
        if ranks and not ({"POSTDOC", "JUNIOR"} & set(ranks)):
            continue
        number = m.get("control_number")
        urls = [u.get("value") for u in m.get("urls", []) if u.get("value")]
        insts = ", ".join(i.get("value", "") for i in m.get("institutions", []))
        regions = ", ".join(m.get("regions", []))
        cats = ", ".join(m.get("arxiv_categories", []))
        desc = html_to_text(m.get("description", ""))
        items.append(RawItem(
            source="INSPIRE jobs",
            title=m.get("position", "Postdoctoral position"),
            url=urls[0] if urls else f"https://inspirehep.net/jobs/{number}",
            text=f"{desc}\nRanks: {', '.join(ranks)}. arXiv categories: {cats}. Region: {regions}.",
            institution=insts or None,
            location=regions or None,
            deadline=m.get("deadline_date"),
            published=m.get("created"),
            trusted=True,
            extra={"inspire_url": f"https://inspirehep.net/jobs/{number}"},
        ))
    log.info("%-28s %3d items", "INSPIRE jobs", len(items))
    return items
